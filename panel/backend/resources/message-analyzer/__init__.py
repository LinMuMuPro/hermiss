"""
Message Analyzer Plugin — Hermes 消息分析引擎 v1.0

四步流水线（通过 Hermes plugin hook 架构实现）：
  Step 1 (pre_llm_call): 独立分类 → 存记忆 / 情绪标记 / check-in 决策 → 返回 context dict
  Step 2 (pre_llm_call): SQLite 宽口径粗筛记忆 → 拼入 context
  Step 3 (pre_llm_call): 注入到 user message（context 字段被 Hermes 注入到用户消息末尾）
  Step 4 (post_llm_call): 每轮回复后刷新主动回复 cron job

Hermes hook 架构（v1.0）：
  - pre_llm_call 返回 {"context": "..."} → Hermes 注入到用户消息末尾
  - transform_llm_output 接收 LLM 回复 → 解析并剥离 <hermes_classify> → 返回清理后文本
  - pre_llm_call 收到新用户消息时取消上一轮 check-in cron job
  - post_llm_call 根据本轮分类结果立即创建下一轮 proactive reply cron job
  - 不再直接修改 conversation_history（Hermes 传的是副本，修改无效）

依赖 Hermes v0.13.0+（需要 transform_llm_output hook）
"""

import json
import os
import re
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import MemoryDB
from .classifier import CLASSIFY_SENTINEL, build_classify_prompt, classify_locally, parse_classify_response
from .retriever import build_full_context
from .reminder_manager import (
    ensure_reminder_dir,
    record_user_activity,
    check_silence,
    create_timed_reminder,
    get_undispatched_reminders,
    mark_reminder_dispatched,
)

def _extract_cron_job_id(output: str) -> str:
    """Extract the actual Hermes cron job id from CLI output."""
    text = (output or "").strip()
    if not text:
        return "unknown"
    match = re.search(r"Created job:\s*([A-Za-z0-9_-]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"\b([0-9a-f]{8,})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    first_line = text.splitlines()[0].strip()
    return first_line if first_line else "unknown"


def _compute_delay(fire_at: str) -> str | None:
    """Convert ISO 8601 fire_at to a Hermes cron delay string.

    Returns None if fire_at is invalid or already in the past.
    """
    try:
        target = datetime.fromisoformat(fire_at)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        total_seconds = int((target - datetime.now(timezone.utc)).total_seconds())
        if total_seconds <= 0:
            return None
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m"
        if total_seconds < 86400:
            return f"{total_seconds // 3600}h"
        return f"{total_seconds // 86400}d"
    except (ValueError, TypeError, OverflowError):
        return None


EMOTION_INJECTIONS = {
    "negative": (
        "[HERMES EMOTION GUIDANCE] "
        "The user seems down or upset. Prioritize empathy: acknowledge their feelings first, "
        "validate them, then gently offer warmth or a shift to something lighter. "
        "Don't be overly cheerful — match their emotional tone first, then lead."
    ),
    "intense": (
        "[HERMES EMOTION GUIDANCE — PRIORITY] "
        "The user is in strong emotional distress. This takes precedence over everything. "
        "Give them your full attention. Listen. Acknowledge. Don't problem-solve unless "
        "they ask. Be present. Keep responses warm, short, and focused on them."
    ),
}


REALITY_BOUNDARY_CONTEXT = (
    "[HERMES HIGH PRIORITY STYLE - REALITY BOUNDARY] "
    "You may speak warmly, play along with closeness, and use imaginative phrasing when the user invites it, "
    "but do not claim you have performed real physical actions unless the user explicitly frames it as roleplay. "
    "Avoid statements like 'I put the medicine on the table', 'I made/poured/brewed it for you', or 'I am beside your bed' as literal facts. "
    "Prefer wording like 'remember to keep the medicine nearby', 'go make yourself some warm water', or 'imagine me nudging you gently' when appropriate."
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = _env_value(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    raw = _env_value(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _env_value(name: str) -> str | None:
    if name in os.environ:
        return os.environ.get(name)
    profile = os.environ.get("HERMES_PROFILE", "hermiss")
    candidates = []
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        candidates.append(Path(hermes_home) / ".env")
    candidates.extend([
        Path.home() / ".hermes" / "profiles" / profile / ".env",
        Path.home() / ".hermes" / ".env",
    ])
    for env_path in candidates:
        try:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return None


def _start_web_panel(db_path: Path, port: int | None = None):
    """Start the memory management web panel in a daemon thread.

    Gracefully degrades if FastAPI/uvicorn are not installed —
    the plugin core functions are unaffected.
    """
    if port is None:
        port = int(os.environ.get("HERMES_PANEL_PORT", "8765"))

    try:
        import uvicorn
        from . import backend as panel_backend
    except ImportError as e:
        print(f"[message-analyzer] Web panel disabled — missing dep: {e}")
        print(f"[message-analyzer]   Install: pip install fastapi uvicorn")
        return

    panel_backend.DB_PATH = db_path

    def _run():
        try:
            uvicorn.run(
                panel_backend.app,
                host="0.0.0.0",
                port=port,
                log_level="warning",
            )
        except Exception as e:
            msg = str(e).lower()
            if "address already in use" in msg or "port" in msg:
                print(f"[message-analyzer] Web panel port {port} in use — already running?")
                print(f"[message-analyzer]   Change port: set HERMES_PANEL_PORT env var")
            else:
                print(f"[message-analyzer] Web panel error: {e}")

    t = threading.Thread(target=_run, daemon=True, name="hermes-panel")
    t.start()
    print(f"[message-analyzer] Web panel: http://127.0.0.1:{port}")


def register(ctx):
    """Plugin entry point — called by Hermes PluginManager."""

    # ── Init ────────────────────────────────────────────────────
    try:
        from hermes_constants import get_hermes_home
        hermes_home = Path(get_hermes_home())
    except ImportError:
        hermes_home = Path.home() / ".hermes"

    def _profile_env_value(name: str) -> str | None:
        env_path = hermes_home / ".env"
        try:
            if not env_path.exists():
                return None
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            return None
        return None

    def _env_bool(name: str, default: bool = True) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            raw = _profile_env_value(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}

    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            raw = _profile_env_value(name)
        if raw is None:
            return default
        try:
            return int(str(raw).strip())
        except Exception:
            return default

    db_path = hermes_home / "memory" / "hermes_memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = MemoryDB(db_path)

    reminder_dir = hermes_home / "reminders"
    ensure_reminder_dir(reminder_dir)

    try:
        llm_client = getattr(ctx, "llm", None)
    except Exception as e:
        print(f"[message-analyzer] ctx.llm unavailable: {e}")
        llm_client = None
    can_classify = (
        callable(llm_client)
        or callable(getattr(llm_client, "complete", None))
        or callable(getattr(llm_client, "complete_structured", None))
    )

    profile = os.environ.get("HERMES_PROFILE", "uino_c")
    deliver = os.environ.get("HERMES_DELIVER", "weixin")

    try:
        from hermes_cli.config import load_config
        model_config = (load_config() or {}).get("model") or {}
    except Exception as e:
        print(f"[message-analyzer] load model config failed: {e}")
        model_config = {}
    classify_provider = (
        os.environ.get("HERMES_MEMORY_LLM_PROVIDER")
        or model_config.get("provider")
        or ""
    )
    classify_model = (
        os.environ.get("HERMES_MEMORY_LLM_MODEL")
        or model_config.get("default")
        or model_config.get("model")
        or ""
    )

    state = {
        "db": db,
        "reminder_dir": reminder_dir,
        "last_emotion": None,
        "last_user_message": "",
        "classify_inline": False,
        "can_classify": can_classify,
        "check_in_hours": 0,
        "message_count": 0,
        "recent_context": "",
        "recent_context_with_time": "",
        "last_activity_hint": "",
        "last_user_message_at": "",
        "checkin_style_hint": "",
        "checkin_dirty": False,
    }

    # ── Helpers ─────────────────────────────────────────────────

    def _timestamp_to_local_text(value) -> str:
        if value in (None, ""):
            return ""
        try:
            tz = ZoneInfo(_env_value("TZ") or "Asia/Shanghai")
        except Exception:
            tz = None
        try:
            if isinstance(value, (int, float)):
                dt = datetime.fromtimestamp(float(value), timezone.utc)
            else:
                raw = str(value).strip()
                if not raw:
                    return ""
                if raw.replace(".", "", 1).isdigit():
                    dt = datetime.fromtimestamp(float(raw), timezone.utc)
                else:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
            if tz:
                dt = dt.astimezone(tz)
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return ""

    def _local_time_text_for(dt: datetime) -> str:
        try:
            tz = ZoneInfo(_env_value("TZ") or "Asia/Shanghai")
            return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _item_value(item, *names):
        if isinstance(item, dict):
            for name in names:
                value = item.get(name)
                if value not in (None, ""):
                    return value
            return None
        for name in names:
            value = getattr(item, name, None)
            if value not in (None, ""):
                return value
        return None

    def _format_recent_context(conversation_history, limit: int = 6, with_time: bool = False) -> str:
        """Return a compact recent-chat transcript for classification and proactive scene inference."""
        if not conversation_history:
            return ""
        rows = []
        try:
            items = list(conversation_history)[-limit:]
        except Exception:
            return ""
        for item in items:
            role = str(_item_value(item, "role", "sender") or "unknown")
            content = str(_item_value(item, "content", "message") or "")
            content = " ".join(content.split())
            if not content:
                continue
            if len(content) > 180:
                content = content[:177] + "..."
            if with_time:
                ts = _timestamp_to_local_text(_item_value(item, "timestamp", "created_at", "time"))
                rows.append(f"[{ts or 'time unknown'}] {role}: {content}")
            else:
                rows.append(f"{role}: {content}")
        return "\n".join(rows)

    def _local_time_text() -> str:
        try:
            now = datetime.now(ZoneInfo(_env_value("TZ") or "Asia/Shanghai"))
        except Exception:
            now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")

    def _quiet_hour_policy(target_dt_utc: datetime, style_hint: str, source_text: str) -> tuple[datetime, str, bool]:
        """Delay generic proactive replies during likely sleep hours."""
        try:
            tz = ZoneInfo(_env_value("TZ") or "Asia/Shanghai")
        except Exception:
            tz = None
        local_dt = target_dt_utc.astimezone(tz) if tz else target_dt_utc
        start_hour = _env_int("HERMISS_PROACTIVE_SLEEP_START_HOUR", 0)
        end_hour = _env_int("HERMISS_PROACTIVE_SLEEP_END_HOUR", 8)
        if start_hour < 0 or start_hour > 23:
            start_hour = 0
        if end_hour < 0 or end_hour > 23:
            end_hour = 8
        hour = local_dt.hour
        in_quiet = (start_hour <= hour < end_hour) if start_hour <= end_hour else (hour >= start_hour or hour < end_hour)
        if not in_quiet:
            return target_dt_utc, style_hint, False
        source = source_text or ""
        activity_words = [
            "考试", "考场", "面试", "开会", "会议", "上课", "课堂", "健身", "锻炼", "跑步", "游泳",
            "开车", "骑车", "地铁", "高铁", "飞机", "洗澡", "看电影", "看剧", "出门", "上班",
            "工作", "加班", "学习", "复习", "写作业", "睡", "晚安", "困了", "休息", "躺下", "熬不住",
        ]
        explicit_activity = any(word in source for word in activity_words)
        if explicit_activity:
            quiet_hint = (
                "Trigger time is late night / early morning and the user may be sleeping or busy. "
                "Do not guess the user is awake. Do not ask what they are doing. Keep it very light."
            )
            return target_dt_utc, quiet_hint, False
        next_local = local_dt.replace(hour=end_hour, minute=30, second=0, microsecond=0)
        if next_local <= local_dt:
            next_local = next_local + timedelta(days=1)
        adjusted = next_local.astimezone(timezone.utc) if tz else next_local
        quiet_hint = (
            "The original trigger time was during likely sleep hours, so this was delayed to morning. "
            "Do not say the user woke up, is awake, has not slept, or woke early. "
            "Send only a gentle morning-style check-in that is okay to read later."
        )
        return adjusted, quiet_hint, True

    def _activity_style_hint(text: str) -> tuple[str, int]:
        source = (text or "").lower()
        if re.search(r"睡|晚安|困了|休息|躺下|熬不住", source):
            return "用户可能在休息或准备睡觉；主动消息要轻，不要追问在干什么，可像醒后/休息后的温柔关心。", 8
        if re.search(r"考试|考场|面试|开会|会议|上课|课堂|健身|锻炼|跑步|游泳|开车|骑车|地铁|高铁|飞机|洗澡|看电影|看剧|出门|上班|工作|加班|学习|写作业|复习", source):
            return "用户提到一个可能暂时不看手机的活动；不要问“在干什么”，优先问进度、结果、累不累、顺不顺利。", 3
        if re.search(r"难受|不舒服|感冒|发烧|头疼|胃疼|咳嗽|累|崩溃|烦|焦虑|紧张|害怕|难过|委屈|哭", source):
            return "用户可能处于身体或情绪低落状态；主动消息要短、软一点，先关心状态，不要讲道理。", 2
        if re.search(r"吃饭|外卖|做饭|饿|午饭|晚饭|早餐|夜宵", source):
            return "最近话题和吃饭有关；可以自然关心吃得怎么样，但不要强行套旧偏好。", 3
        return "没有明确活动；可以自然问用户现在在做什么或今天过得怎么样。", 3

    def _choose_checkin_hours() -> int:
        default_hours = _env_int("HERMISS_PROACTIVE_CHECKIN_DEFAULT_HOURS", 3)
        min_hours = _env_int("HERMISS_PROACTIVE_CHECKIN_MIN_HOURS", 2)
        max_hours = _env_int("HERMISS_PROACTIVE_CHECKIN_MAX_HOURS", 8)
        if min_hours < 1:
            min_hours = 1
        if max_hours < min_hours:
            max_hours = min_hours
        text = "\n".join([
            str(state.get("recent_context") or ""),
            str(state.get("last_activity_hint") or ""),
            str(state.get("last_user_message") or ""),
        ])
        style_hint, suggested = _activity_style_hint(text)
        state["checkin_style_hint"] = style_hint
        selected = suggested if suggested else default_hours
        return max(min_hours, min(max_hours, int(selected or default_hours)))

    def _assume_previous_checkin_unreplied() -> bool:
        cf = reminder_dir / "active_checkin.json"
        if not cf.exists():
            return False
        try:
            data = json.loads(cf.read_text())
            if data.get("cancelled", False):
                return False
            fire_at = data.get("fire_at")
            if not fire_at:
                return False
            target = datetime.fromisoformat(fire_at)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= target
        except Exception:
            return False

    def _resolve_deliver_target() -> str:
        """Resolve platform-only delivery like 'weixin' to a concrete chat target."""
        configured = (deliver or "").strip() or "weixin"
        if configured in {"local", "origin"} or ":" in configured:
            return configured
        if configured == "weixin":
            try:
                directory_path = hermes_home / "channel_directory.json"
                directory = json.loads(directory_path.read_text())
                for entry in directory.get("platforms", {}).get("weixin", []):
                    chat_id = str(entry.get("id") or "").strip()
                    if chat_id:
                        return f"weixin:{chat_id}"
            except Exception as e:
                print(f"[message-analyzer] resolve weixin deliver target failed: {e}")
        return configured

    def _classify_via_llm(message: str, conversation_history=None) -> dict | None:
        """Step 1 (preferred): Use ctx.llm for independent classification."""
        prompt = build_classify_prompt(
            message,
            recent_context=_format_recent_context(conversation_history),
        )
        try:
            if callable(getattr(llm_client, "complete", None)):
                llm_kwargs = {
                    "max_tokens": 256,
                    "temperature": 0.1,
                    "timeout": 30,
                    "purpose": "memory_classification",
                }
                if classify_provider and classify_provider != "auto":
                    llm_kwargs["provider"] = classify_provider
                if classify_model:
                    llm_kwargs["model"] = classify_model
                result = llm_client.complete(
                    [{"role": "user", "content": prompt}],
                    **llm_kwargs,
                )
                result_text = getattr(result, "text", result)
            elif callable(llm_client):
                result_text = llm_client(prompt, max_tokens=256, temperature=0.1)
            else:
                print("[message-analyzer] classify_via_llm skipped: ctx.llm has no supported API")
                return None
            parsed = parse_classify_response(str(result_text))
            print(f"[message-analyzer] classify parsed: {parsed}", flush=True)
            return parsed
        except Exception as e:
            print(f"[message-analyzer] classify_via_llm failed: {e}")
            return None

    def _execute_classification(result: dict, source_msg: str):
        """Execute actions from classification result."""
        memory_type = result.get("memory", "none")
        memory_entry = result.get("memory_entry", "")
        importance = result.get("importance", "low")
        emotion = result.get("emotion", "neutral")

        memory_items = result.get("memories") or []
        if not memory_items and memory_type != "none" and memory_entry:
            memory_items = [{
                "memory": memory_type,
                "memory_entry": memory_entry,
                "importance": importance,
            }]

        for item in memory_items:
            item_type = item.get("memory", "none")
            item_entry = item.get("memory_entry", "")
            item_importance = item.get("importance", importance)
            if item_type == "none" or not item_entry:
                continue
            memory_id = db.insert_memory(
                entry=item_entry,
                category=item_type,
                importance=item_importance,
                emotion=emotion,
                source_msg=source_msg,
            )
            if memory_id:
                print(f"[message-analyzer] Stored {item_type}: {item_entry[:60]}")
            else:
                print(f"[message-analyzer] Memory deduped: {item_entry[:60]}")

        if emotion in EMOTION_INJECTIONS:
            state["last_emotion"] = emotion

#         reminder_type = result.get("reminder", "none")
#         if reminder_type == "timed":
#             reminder_time = result.get("reminder_time", "")
#             reminder_text = result.get("reminder_text", "")
#             if reminder_time and reminder_text:
#                 r = create_timed_reminder(reminder_dir, reminder_time, reminder_text)
#                 if r["status"] == "created":
#                     print(f"[message-analyzer] Reminder: {reminder_text} @ {reminder_time}")
#                     _dispatch_reminders()
# 
        # ── Check-in scheduling ────────────────────────────
        check_in_hours = result.get("check_in_hours", 0)
        if not _env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True):
            if isinstance(check_in_hours, int) and check_in_hours > 0:
                print("[message-analyzer] Check-in skipped: disabled by HERMISS_PROACTIVE_CHECKIN_ENABLED")
            state["check_in_hours"] = 0
            state["checkin_dirty"] = False
            return
        if isinstance(check_in_hours, int) and check_in_hours > 0:
            min_hours = _env_int("HERMISS_PROACTIVE_CHECKIN_MIN_HOURS", 2)
            max_hours = _env_int("HERMISS_PROACTIVE_CHECKIN_MAX_HOURS", 8)
            state["check_in_hours"] = max(min_hours, min(max_hours, check_in_hours))
            style_hint, _ = _activity_style_hint(
                "\n".join([
                    str(state.get("recent_context") or ""),
                    str(state.get("last_activity_hint") or ""),
                    source_msg or "",
                ])
            )
            state["checkin_style_hint"] = style_hint
            state["checkin_dirty"] = True
            print(f"[message-analyzer] Check-in refresh requested: {state['check_in_hours']}h")

    def _cancel_checkin():
        """Cancel any active check-in — user is back."""
        cf = reminder_dir / "active_checkin.json"
        if not cf.exists():
            return
        try:
            data = json.loads(cf.read_text())
            if data.get("cancelled", False):
                return
            data["cancelled"] = True
            cf.write_text(json.dumps(data))
            # Try to delete the cron job via CLI; prompt also self-checks cancelled flag
            job_id = _extract_cron_job_id(data.get("job_id", ""))
            if job_id and job_id != "unknown":
                try:
                    subprocess.run(
                        ["hermes", "--profile", profile, "cron", "delete", job_id],
                        capture_output=True, timeout=5,
                    )
                    print(f"[message-analyzer] Cron deleted: {job_id}")
                except Exception:
                    pass  # Best-effort — prompt self-checks cancelled flag
        except Exception as e:
            print(f"[message-analyzer] Cancel check-in failed: {e}")

    def _schedule_checkin():
        """Refresh the one-shot cron job for proactive reply."""
        if not _env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True):
            _cancel_checkin()
            state["checkin_dirty"] = False
            return
        check_in_hours = state.get("check_in_hours", 0)
        if not isinstance(check_in_hours, int) or check_in_hours <= 0:
            state["checkin_dirty"] = False
            return
        if check_in_hours > 168:  # Cap at 7 days — anything longer is absurd
            check_in_hours = 168

        # Replacing a check-in means old jobs must either be deleted or self-silence.
        _cancel_checkin()

        created_at = datetime.now(timezone.utc)
        fire_at_dt = created_at + timedelta(hours=check_in_hours)
        checkin_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        recent_context = state.get("recent_context", "")
        recent_context_with_time = state.get("recent_context_with_time", recent_context)
        last_user_message_at = state.get("last_user_message_at", "")
        last_activity_hint = state.get("last_activity_hint", state.get("last_user_message", ""))
        scene_text = f"{recent_context}\\n{last_activity_hint}"
        style_hint = state.get("checkin_style_hint") or _activity_style_hint(scene_text)[0]
        fire_at_dt, style_hint, quiet_delayed = _quiet_hour_policy(fire_at_dt, style_hint, scene_text)
        fire_at = fire_at_dt.isoformat()
        local_time = _local_time_text()
        trigger_local_time = _local_time_text_for(fire_at_dt)

        # Write the tracking file first (the cron job self-checks against it).
        # Every user message cancels this file; newly scheduled check-ins get a
        # fresh checkin_id, so stale/duplicate cron jobs can self-silence.
        cf = reminder_dir / "active_checkin.json"
        cf.write_text(json.dumps({
            "cancelled": False,
            "check_in_hours": check_in_hours,
            "checkin_id": checkin_id,
            "created_at": created_at.isoformat(),
            "fire_at": fire_at,
            "local_created_at": local_time,
            "trigger_local_time": trigger_local_time,
            "last_user_message_at": last_user_message_at,
            "style_hint": style_hint,
            "last_activity_hint": last_activity_hint,
            "recent_context": recent_context,
            "recent_context_with_time": recent_context_with_time,
            "quiet_hour_delayed": bool(quiet_delayed),
        }, indent=2, ensure_ascii=False))

        # Build the self-contained proactive reply prompt.
        memory_context = build_full_context(db, "__CHECKIN__")
        prompt = (
            f"[HERMES PROACTIVE REPLY]\n\n"
            f"Read the active_checkin.json file at {cf}. If cancelled=true, "
            f"or if checkin_id is not exactly {checkin_id}, return exactly [SILENT] and nothing else.\n\n"
            f"If not cancelled and the checkin_id matches, generate ONE short, warm, natural proactive reply in Chinese. "
            f"Do not mention how many hours passed, do not say 'you have not messaged', and do not sound like monitoring.\n\n"
            f"Current local time when this proactive job was scheduled: {local_time}. "
            f"Actual local trigger time for this message: {trigger_local_time}. "
            f"Last user message local time: {last_user_message_at or 'unknown'}.\n\n"
            f"Before writing, infer what the user is most likely doing from recent_context_with_time, last_activity_hint, "
            f"last_user_message_at, trigger_local_time, and the time gap. Use this inference silently; do not explain it. "
            f"Do not infer exact phrases like 'earlier today', 'yesterday', or 'the day before yesterday' unless explicit timestamps prove it.\n\n"
            f"Use recent_context_with_time as the primary transcript. Use last_activity_hint only as a short summary of the latest user activity. "
            f"Scene strategy: {style_hint}\n\n"
            f"If trigger_local_time is late night or early morning and there is no explicit evidence the user is awake, assume they may be sleeping or will see it later. "
            f"Do NOT say '醒了吗', '这么早就醒了', '还没睡', or imply the user is awake. "
            f"If the user was doing a specific activity where they may not look at the phone (for example exam, class, gym, workout, study, work, meeting, driving, shower, sleep/rest, movie/show, travel, or going out), do NOT ask 'what are you doing'. "
            f"Instead ask gently about progress, result, how it went, or whether they are okay. "
            f"If there is no specific activity in context and the time is not a sleep/quiet hour, ask what they are up to now in a casual caring way.\n\n"
            f"Reality boundary: do not claim you performed physical actions in the user's room; "
            f"phrase reminders as suggestions or gentle imagined companionship unless the user explicitly roleplays it. "
            f"Do not overuse the user's name; avoid names entirely if the user has said they dislike it.\n\n"
            f"Constraints: one message only; no forced memory reference; do not mention food/preferences unless the latest relevant topic was food; "
            f"copy user names exactly as stored and never translate pinyin/homophones; do not say yesterday, the day before yesterday, earlier today, or quote durations unless a timestamp explicitly proves it."
        )
        if memory_context:
            prompt += f"\n\nMemory context is optional background only; use it only if naturally relevant:\n{memory_context}"

        delay = f"{check_in_hours}h"
        try:
            result = subprocess.run(
                ["hermes", "--profile", profile, "cron", "create", delay, prompt, "--deliver", _resolve_deliver_target()],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                job_id = _extract_cron_job_id(result.stdout)
                data = json.loads(cf.read_text())
                data["job_id"] = job_id
                cf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                state["checkin_dirty"] = False
                print(
                    f"[message-analyzer] Check-in cron: {check_in_hours}h "
                    f"(fire at {fire_at[:16]}, job={job_id})"
                )
            else:
                err = result.stderr.strip()
                print(f"[message-analyzer] Check-in cron failed (rc={result.returncode}): {err}")
        except FileNotFoundError:
            print("[message-analyzer] hermes CLI not on PATH — proactive reply not scheduled")
        except Exception as e:
            print(f"[message-analyzer] Check-in schedule error: {e}")

    def _dispatch_reminders():
        """Wire pending reminders to hermes cron jobs."""
        try:
            pending = get_undispatched_reminders(reminder_dir)
        except Exception:
            return

        for r in pending:
            fire_at = r.get("fire_at", "")
            reminder_text = r.get("reminder_text", "")
            delay = _compute_delay(fire_at)
            if not delay:
                print(f"[message-analyzer] Reminder in the past, discarding: {reminder_text}")
                mark_reminder_dispatched(reminder_dir, fire_at, reminder_text)
                continue

            prompt = (
                f"[HERMES REMINDER] {reminder_text}\n\n"
                "The user asked you to remind them about this. "
                "Bring it up warmly and naturally."
            )
            try:
                result = subprocess.run(
                    ["hermes", "--profile", profile, "cron", "create", delay, prompt, "--deliver", deliver],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    job_id = _extract_cron_job_id(result.stdout)
                    mark_reminder_dispatched(reminder_dir, fire_at, reminder_text)
                    print(f"[message-analyzer] Reminder cron: {reminder_text} (in {delay})")
                else:
                    err = result.stderr.strip()
                    print(f"[message-analyzer] Reminder cron failed (rc={result.returncode}): {err}")
                    err = result.stderr.strip()
                    print(f"[message-analyzer] Reminder cron failed (rc={result.returncode}): {err}")
            except FileNotFoundError:
                print("[message-analyzer] hermes CLI not on PATH — cannot schedule reminders")
                return
            except Exception as e:
                print(f"[message-analyzer] Reminder cron error: {e}")

    def _build_silence_context(user_id: str) -> str:
        """Build silence nudge context string for injection into user message."""
        if not check_silence(user_id):
            return ""
        return (
            "[HERMES SILENCE NUDGE] "
            "The user has been silent for a while. They may have gotten distracted "
            "or be waiting for you. Gently check in — keep it warm and natural. "
            "Don't make it sound like you're monitoring them."
        )

    # ── Hook Handlers ───────────────────────────────────────────

    def _on_session_start(session_id, model, platform):
        """Session start: cancel active check-in, dispatch pending reminders."""
        if platform == "cron":
            return
        _cancel_checkin()
        _dispatch_reminders()

    def _pre_llm_call(
        session_id, user_message, conversation_history,
        is_first_turn, model, platform, sender_id
    ):
        """
        Build context for injection into the user message.

        Returns a dict with a 'context' key (or a plain string).
        Hermes appends this to the current turn's user message.
        Returns None if no context to inject.
        """
        if platform == "cron":
            return None

        user_id = sender_id or "default"

        # Handle empty / non-text messages
        if not user_message or not isinstance(user_message, str) or not user_message.strip():
            record_user_activity(user_id)
            silence_ctx = _build_silence_context(user_id)
            return {"context": silence_ctx} if silence_ctx else None

        user_message = user_message.strip()
        _cancel_checkin()
        state["check_in_hours"] = 0
        state["checkin_dirty"] = False
        state["last_user_message"] = user_message
        message_time = _local_time_text()
        recent_context = _format_recent_context(conversation_history)
        recent_context_with_time = _format_recent_context(conversation_history, with_time=True)
        if recent_context:
            state["recent_context"] = f"{recent_context}\nuser: {user_message}"
        else:
            state["recent_context"] = f"user: {user_message}"
        if recent_context_with_time:
            state["recent_context_with_time"] = f"{recent_context_with_time}\n[{message_time}] user: {user_message}"
        else:
            state["recent_context_with_time"] = f"[{message_time}] user: {user_message}"
        state["last_activity_hint"] = user_message
        state["last_user_message_at"] = message_time

        # Skip slash commands
        if user_message.startswith("/"):
            record_user_activity(user_id)
            silence_ctx = _build_silence_context(user_id)
            return {"context": silence_ctx} if silence_ctx else None

        state["message_count"] += 1
        print(f"[message-analyzer] pre_llm_call: '{user_message[:60]}'")
        context_parts = [REALITY_BOUNDARY_CONTEXT]
        classify_instruction = ""

        # ── Step 1: Classify ──────────────────────────────────
        local_classification = classify_locally(user_message)
        if local_classification:
            print(f"[message-analyzer] local classify: {local_classification}", flush=True)
            _execute_classification(local_classification, user_message)
        elif state["can_classify"]:
            classification = _classify_via_llm(user_message, conversation_history)
            if classification:
                _execute_classification(classification, user_message)
        else:
            # Inline mode: inject classify instruction into context
            classify_instruction = build_classify_prompt(user_message, recent_context=_format_recent_context(conversation_history))
            state["classify_inline"] = True
            print(f"[message-analyzer] inline classify active, len={len(classify_instruction)}", flush=True)

        # ── Step 2: Retrieve memories ─────────────────────────
        memory_context = build_full_context(db, user_message)
        if memory_context:
            context_parts.append(memory_context)

        # ── Step 3: Emotion guidance from previous turn ───────
        if state["last_emotion"] in EMOTION_INJECTIONS:
            context_parts.append(EMOTION_INJECTIONS[state["last_emotion"]])
            state["last_emotion"] = None

        # ── Step 4: Silence check (before recording activity) ─
        silence_ctx = _build_silence_context(user_id)
        if silence_ctx:
            context_parts.append(silence_ctx)

        # ── Step 5: Classify instruction (inline mode) ────────
        if classify_instruction:
            context_parts.append(classify_instruction)

        record_user_activity(user_id)

        if context_parts:
            return {"context": "\n\n".join(context_parts)}
        return None

    def _transform_llm_output(response_text, session_id, model, platform):
        """
        Parse and strip <hermes_classify> from LLM output.
        Execute classification for inline mode.
        Always strip the XML block (safety — even if ctx.llm was used).
        Returns cleaned response string, or None to leave unchanged.
        """
        if not response_text or not isinstance(response_text, str):
            return None

        cleaned = response_text
        modified = False

        # ── Strip classify block ──────────────────────────────────
        if CLASSIFY_SENTINEL in cleaned:
            if state.get("classify_inline"):
                classification = parse_classify_response(cleaned)
                if classification:
                    _execute_classification(
                        classification,
                        state.get("last_user_message", ""),
                    )
            # Primary: strip well-formed <hermes_classify>...</hermes_classify>
            cleaned = re.sub(
                r"<hermes_classify>.*?</hermes_classify>",
                "",
                cleaned,
                flags=re.DOTALL,
            )
            # Fallback: if opening tag still present (missing closing tag), strip to end
            idx = cleaned.find(CLASSIFY_SENTINEL)
            if idx != -1:
                cleaned = cleaned[:idx]
            cleaned = cleaned.strip()
            state["classify_inline"] = False
            modified = True

        return cleaned if modified else None

    def _post_llm_call(
        session_id, user_message, assistant_response,
        conversation_history, model, platform
    ):
        """
        Post-LLM hook: refresh proactive reply after each completed reply.
        A new user message cancels the previous job in pre_llm_call; this hook
        creates the replacement immediately instead of waiting for session end.
        """
        if platform == "cron":
            return None
        last_user_message = str(state.get("last_user_message") or "").strip()
        if (
            last_user_message
            and not last_user_message.startswith("/")
            and not state.get("checkin_dirty")
            and _env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True)
        ):
            state["check_in_hours"] = _choose_checkin_hours()
            state["checkin_dirty"] = True
            print(
                "[message-analyzer] Check-in refresh requested: "
                f"{state['check_in_hours']}h (adaptive)"
            )
        if state.get("checkin_dirty"):
            _schedule_checkin()
        return None

    def _post_tool_call(
        tool_name, args, result, session_id, task_id, tool_call_id, duration_ms
    ):
        """Track cronjob tool calls for web panel visibility."""
        if tool_name != "cronjob":
            return None
        try:
            action = args.get("action", "")
            tracker_path = reminder_dir.parent / "plugins" / "message-analyzer" / "cron_tracker.json"
            tracker_path.parent.mkdir(parents=True, exist_ok=True)
            records = []
            if tracker_path.exists():
                records = json.loads(tracker_path.read_text())
            if action == "create":
                records.append({
                    "job_id": result.get("job_id", ""),
                    "name": args.get("name", ""),
                    "schedule": args.get("schedule", ""),
                    "status": "created",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            elif action == "delete":
                records.append({
                    "job_id": args.get("job_id", ""),
                    "status": "cancelled",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            if records:
                tracker_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
        except Exception:
            pass  # Best-effort tracking

    def _on_session_end(session_id, completed, interrupted, model, platform):
        """Archive session summary."""
        if platform == "cron":
            return
        db.save_session_summary(
            session_id=str(session_id),
            message_count=state.get("message_count", 0),
            last_emotion=state.get("last_emotion"),
        )
        state["check_in_hours"] = 0  # Reset for next session
        state["message_count"] = 0
        print(f"[message-analyzer] Session ended: {session_id}")

    # ── Register Hooks ──────────────────────────────────────────

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)

    # ── Web Panel ──────────────────────────────────────────────
    _start_web_panel(db_path)

    print(
        f"[message-analyzer] v1.0 registered "
        f"(classify={'llm' if can_classify else 'inline'}, "
        f"memories={db.memory_count()})"
    )
