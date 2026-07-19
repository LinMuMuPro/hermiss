"""
Message Analyzer Plugin — Hermes 消息分析引擎 v1.0

四步流水线（通过 Hermes plugin hook 架构实现）：
  Step 1 (pre_llm_call): 取消上一轮 check-in → SQLite/Milvus 检索记忆 → 拼入 context
  Step 2 (pre_llm_call): 注入规则/记忆/context 到 user message
  Step 3 (post_llm_call): 立即放行回复发送
  Step 4 (background): 回复发送链路放行后，异步分类 → 写记忆 → 创建下一轮 proactive reply cron job

Hermes hook 架构（v1.0）：
  - pre_llm_call 返回 {"context": "..."} → Hermes 注入到用户消息末尾
  - transform_llm_output 接收 LLM 回复 → 解析并剥离兼容旧内联模式的 <hermes_classify>
  - pre_llm_call 收到新用户消息时取消上一轮 check-in cron job
  - post_llm_call 启动后台分析线程，不阻塞微信回复发送
  - 不再直接修改 conversation_history（Hermes 传的是副本，修改无效）

依赖 Hermes v0.13.0+（需要 transform_llm_output hook）
"""

import json
import os
import re
import re
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import MemoryDB
from .classifier import CLASSIFY_SENTINEL, build_classify_prompt, parse_classify_response
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
        "check_in_minutes": 0,
        "checkin_followup_stage": 0,
        "checkin_frequency": "normal",
        "short_term_user_state": None,
        "short_term_user_state_injected": False,
        "state_base": None,
    }
    short_state_file = hermes_home / "memory" / "short_term_user_state.json"

    def _clean_state_base_text(value, limit: int = 160) -> str:
        text = " ".join(str(value or "").strip().split())
        if text in {"", "无", "none", "None", "null", "省略"}:
            return ""
        return text[:limit]

    def _load_persisted_state_base() -> None:
        try:
            if not short_state_file.exists():
                return
            data = json.loads(short_state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            saved_state = data.get("state")
            if isinstance(saved_state, dict) and str(saved_state.get("text") or "").strip():
                state["short_term_user_state"] = saved_state
            saved_base = data.get("base")
            if isinstance(saved_base, dict):
                state["state_base"] = saved_base
        except Exception as e:
            print(f"[message-analyzer] load state base failed: {e}", flush=True)

    def _persist_short_term_user_state(reason: str = "updated") -> None:
        try:
            current_state = state.get("short_term_user_state")
            current_base = state.get("state_base")
            payload = {
                "status": "none",
                "reason": reason,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "state": None,
                "base": current_base if isinstance(current_base, dict) else None,
            }
            if isinstance(current_state, dict) and str(current_state.get("text") or "").strip():
                payload["status"] = "active"
                payload["state"] = current_state
            if isinstance(current_base, dict) and str(current_base.get("summary") or "").strip():
                payload["status"] = "active"
            short_state_file.parent.mkdir(parents=True, exist_ok=True)
            short_state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[message-analyzer] persist short state failed: {e}", flush=True)

    _load_persisted_state_base()
    _persist_short_term_user_state("startup")

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

    def _timestamp_to_local_dt(value) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            tz = ZoneInfo(_env_value("TZ") or "Asia/Shanghai")
        except Exception:
            tz = timezone.utc
        try:
            if isinstance(value, (int, float)):
                dt = datetime.fromtimestamp(float(value), timezone.utc)
            else:
                raw = str(value).strip()
                if not raw:
                    return None
                if raw.replace(".", "", 1).isdigit():
                    dt = datetime.fromtimestamp(float(raw), timezone.utc)
                else:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(tz)
        except Exception:
            return None

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
            display_role = "你" if role == "assistant" else ("用户" if role == "user" else role)
            content = str(_item_value(item, "content", "message") or "")
            content = " ".join(content.split())
            content = _compact_activity_text(content, 180)
            if not content:
                continue
            if role == "tool":
                continue
            if content.startswith("[IMPORTANT: You are running as a scheduled cron job"):
                continue
            if content in {"[SILENT]", "定时任务测试已触发。"}:
                continue
            if "active_checkin.json" in content or "HERMES PROACTIVE REPLY" in content:
                continue
            if len(content) > 180:
                content = content[:177] + "..."
            if with_time:
                ts = _timestamp_to_local_text(_item_value(item, "timestamp", "created_at", "time"))
                rows.append(f"[{ts or 'time unknown'}] {display_role}: {content}")
            else:
                rows.append(f"{display_role}: {content}")
        return "\n".join(rows)

    def _format_classify_context(conversation_history, current_user_message: str, limit: int = 6) -> str:
        """Return recent context for classification without duplicating the current user message."""
        if not conversation_history:
            return ""
        current = " ".join(str(current_user_message or "").split())
        try:
            items = list(conversation_history)
        except Exception:
            return ""
        if current:
            while items:
                role = str(_item_value(items[-1], "role", "sender") or "unknown")
                content = " ".join(str(_item_value(items[-1], "content", "message") or "").split())
                if role == "user" and content == current:
                    items.pop()
                    continue
                break
        return _format_recent_context(items, limit=limit)

    def _compact_activity_text(content: str, limit: int = 160) -> str:
        text = " ".join(str(content or "").split())
        if not text:
            return ""
        if "The user sent an image" in text or "image_url:" in text or "Here's what I can see" in text:
            lower = text.lower()
            details = []
            for word in ("螺蛳粉", "外卖", "takeout", "soup", "shrimp", "noodles", "泡椒凤爪", "荔枝", "meal"):
                if word.lower() in lower and word not in details:
                    details.append(word)
            if details:
                return f"用户发了一张图片，内容和{('、'.join(details[:5]))}有关。"
            return "用户发了一张图片。"
        if len(text) > limit:
            return text[: max(0, limit - 3)] + "..."
        return text

    def _message_needs_temporal_guard(message: str) -> bool:
        text = message or ""
        terms = (
            "想你", "想我", "几天", "多久", "没见", "好久", "久别", "上次", "刚才",
            "新会话", "新对话", "多久没", "几天没", "多长时间",
        )
        return any(term in text for term in terms)

    def _contains_temporal_gap_terms(message: str) -> bool:
        text = message or ""
        terms = ("几天", "多久", "没见", "好久", "久别", "多久没", "几天没", "多长时间")
        return any(term in text for term in terms)

    def _format_duration_zh(seconds: float) -> str:
        total = max(0, int(seconds))
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days:
            return f"{days}天{hours}小时"
        if hours:
            return f"{hours}小时{minutes}分钟"
        if minutes:
            return f"{minutes}分钟"
        return "不到1分钟"

    def _is_usable_chat_content(content: str) -> bool:
        text = " ".join(str(content or "").split())
        if not text:
            return False
        if text.startswith("[IMPORTANT: You are running as a scheduled cron job"):
            return False
        if text in {"[SILENT]", "定时任务测试已触发。"}:
            return False
        if "active_checkin.json" in text or "HERMES PROACTIVE REPLY" in text:
            return False
        return True

    def _find_global_last_chat_message(session_id: str = "") -> dict | None:
        """Find the latest real chat message across sessions.

        This is used when a new session has no conversation_history yet. A new
        session does not mean the user and bot have not talked recently.
        """
        state_db = hermes_home / "state.db"
        if not state_db.exists():
            return None
        queries = []
        params = []
        if session_id:
            queries.append(
                """
                SELECT id, session_id, role, content, timestamp
                FROM messages
                WHERE active=1
                  AND role='user'
                  AND content IS NOT NULL
                  AND session_id != ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 12
                """
            )
            params.append((session_id,))
        queries.append(
            """
            SELECT id, session_id, role, content, timestamp
            FROM messages
            WHERE active=1
              AND role='user'
              AND content IS NOT NULL
            ORDER BY timestamp DESC, id DESC
            LIMIT 12
            """
        )
        params.append(())
        try:
            conn = sqlite3.connect(str(state_db))
            conn.row_factory = sqlite3.Row
            try:
                for query, query_params in zip(queries, params):
                    rows = conn.execute(query, query_params).fetchall()
                    for row in rows:
                        content = " ".join(str(row["content"] or "").split())
                        if not _is_usable_chat_content(content):
                            continue
                        dt = _timestamp_to_local_dt(row["timestamp"])
                        if not dt:
                            continue
                        return {
                            "role": str(row["role"] or "unknown"),
                            "content": content[:80],
                            "timestamp": dt,
                            "session_id": str(row["session_id"] or ""),
                        }
            finally:
                conn.close()
        except Exception as e:
            print(f"[message-analyzer] global last chat lookup failed: {e}")
        return None

    def _build_temporal_guard_context(
        conversation_history,
        current_local_dt: datetime,
        is_first_turn: bool,
        session_id: str = "",
    ) -> str:
        last_dt = None
        last_role = ""
        last_content = ""
        try:
            items = list(conversation_history or [])
        except Exception:
            items = []
        for item in reversed(items):
            role = str(_item_value(item, "role", "sender") or "unknown")
            if role == "tool":
                continue
            content = " ".join(str(_item_value(item, "content", "message") or "").split())
            if not _is_usable_chat_content(content):
                continue
            dt = _timestamp_to_local_dt(_item_value(item, "timestamp", "created_at", "time"))
            if dt:
                last_dt = dt
                last_role = role
                last_content = content[:80]
                break

        lines = [
            "[HERMES TEMPORAL CONTEXT - anti-hallucination]",
            f"当前本地时间: {_local_time_text_for(current_local_dt)}.",
            "不要根据“新会话/新对话”推断用户和你很久没聊；新会话只表示上下文重置，不表示现实时间过去很多天。",
            "禁止无时间证据时说“几天没见”“好久不见”“两天没聊”“这么久没见”等时长判断。",
            "如果用户说“想你”，只回应情绪本身，不要自行编造分离时长。",
        ]
        if last_dt:
            diff = max(0, (current_local_dt - last_dt).total_seconds())
            lines.append(
                f"当前可见聊天里上一条消息是 {last_role} 在 {_local_time_text_for(last_dt)} 发出的，距离现在约 {_format_duration_zh(diff)}。"
            )
            if diff < 86400:
                lines.append("可见证据显示间隔不到一天，所以不能说几天没见或两天没见。")
            lines.append(f"上一条可见消息摘录: {last_content}")
        elif is_first_turn:
            global_last = _find_global_last_chat_message(session_id=str(session_id or ""))
            if global_last:
                global_dt = global_last["timestamp"]
                diff = max(0, (current_local_dt - global_dt).total_seconds())
                lines.append(
                    f"当前会话没有可见历史，但全局最近用户消息在 {_local_time_text_for(global_dt)}，距离现在约 {_format_duration_zh(diff)}。"
                )
                if diff < 86400:
                    lines.append("全局真实聊天间隔不到一天，所以不能说几天没见、两天没聊、好久不见，也不要反问用户“几天了”。")
                if _contains_temporal_gap_terms(str(global_last["content"])):
                    lines.append("全局上一条用户消息涉及询问聊天间隔；不要沿用其中的时间猜测，也不要反问具体几天。")
                else:
                    lines.append(f"全局上一条用户消息摘录: {global_last['content']}")
            else:
                lines.append("当前可见聊天历史没有上一条消息；这不能证明现实中已经隔了几天。不要主动谈论分离时长，也不要反问“几天了”。")
        return "\n".join(lines)

    def _short_state_expected_minutes(value) -> int:
        try:
            minutes = int(value or 0)
        except Exception:
            minutes = 0
        if minutes <= 0:
            return 90
        return max(5, min(minutes, 480))

    def _clear_dynamic_state_base(reason: str = "cleared") -> None:
        state["short_term_user_state"] = None
        state["short_term_user_state_injected"] = False
        state["state_base"] = None
        state["recent_context"] = ""
        state["recent_context_with_time"] = ""
        state["last_activity_hint"] = ""
        state["last_user_message"] = ""
        state["last_user_message_at"] = ""
        _persist_short_term_user_state(reason)
        print(f"[message-analyzer] Dynamic state base cleared: {reason}", flush=True)

    def _update_short_term_user_state(result: dict | None, source_msg: str) -> None:
        if not isinstance(result, dict):
            return
        action = str(result.get("short_state") or "none").strip().lower()
        text = " ".join(str(result.get("short_state_text") or "").split())
        if action in {"end", "ended", "finish", "finished"}:
            if state.get("short_term_user_state"):
                print("[message-analyzer] Short state cleared", flush=True)
            state["short_term_user_state"] = None
            _persist_short_term_user_state("ended")
            return
        if action not in {"start", "continue"} or not text:
            return
        now = datetime.now(timezone.utc)
        minutes = _short_state_expected_minutes(result.get("short_state_minutes"))
        unavailable = str(result.get("short_state_unavailable") or "no").strip().lower() in {"yes", "true", "1", "是"}
        state["short_term_user_state"] = {
            "text": text[:120],
            "source_msg": _compact_activity_text(source_msg, 120)[:120],
            "started_at": now.isoformat(),
            "expected_minutes": minutes,
            "unavailable": unavailable,
        }
        print(
            "[message-analyzer] Short state set: "
            f"{text[:60]} ({minutes}m, unavailable={unavailable})",
            flush=True,
        )
        _persist_short_term_user_state("set")

    def _update_state_base(result: dict | None, source_msg: str) -> None:
        if not isinstance(result, dict):
            return
        now = datetime.now(timezone.utc)
        current_base = state.get("state_base")
        if not isinstance(current_base, dict):
            current_base = {}

        summary = _clean_state_base_text(result.get("state_base_summary"), 180)
        mood = _clean_state_base_text(result.get("state_base_mood"), 140)
        caution = _clean_state_base_text(result.get("state_base_caution"), 180)
        source = _clean_state_base_text(_compact_activity_text(source_msg, 160), 160)

        emotion = str(result.get("emotion") or "neutral").strip().lower()
        emotion_map = {
            "positive": "积极",
            "neutral": "中性",
            "negative": "消极",
            "intense": "强烈",
        }
        recent_emotion = emotion_map.get(emotion, emotion) if emotion and emotion != "neutral" else ""

        short_state = str(result.get("short_state") or "none").strip().lower()
        short_text = _clean_state_base_text(result.get("short_state_text"), 120)
        if short_state in {"start", "continue"} and short_text:
            current_base["current_state"] = short_text
        elif short_state in {"end", "ended", "finish", "finished"}:
            current_base["current_state"] = ""

        if summary:
            current_base["summary"] = summary
        elif source:
            current_base["summary"] = f"用户刚才说：{source}"

        if recent_emotion:
            current_base["recent_emotion"] = recent_emotion
        elif not current_base.get("recent_emotion"):
            current_base["recent_emotion"] = ""

        if mood:
            current_base["relationship_mood"] = mood
        if caution:
            current_base["caution"] = caution

        current_base["last_user_message"] = source
        current_base["updated_at"] = now.isoformat()
        state["state_base"] = {
            key: value
            for key, value in current_base.items()
            if isinstance(value, bool) or str(value or "").strip()
        }
        _persist_short_term_user_state("base_updated")

    def _clear_consumed_short_state_if_needed(result: dict | None) -> None:
        if not state.get("short_term_user_state_injected"):
            return
        action = ""
        if isinstance(result, dict):
            action = str(result.get("short_state") or "none").strip().lower()
        if action not in {"start", "continue"}:
            state["short_term_user_state"] = None
            print("[message-analyzer] Short state consumed", flush=True)
            _persist_short_term_user_state("consumed")
        state["short_term_user_state_injected"] = False

    def _build_state_base_context(user_message: str, current_local_dt: datetime) -> str:
        current_base = state.get("state_base")
        if not isinstance(current_base, dict):
            current_base = {}
        current_state = state.get("short_term_user_state")
        state_text = ""
        if isinstance(current_state, dict):
            state_text = str(current_state.get("text") or "").strip()
        if not current_base and not state_text:
            return ""

        lines = [
            "[HERMES STATE BASE - concise dynamic context]",
            "这是每轮都会带上的简易状态底座，只用于当前对话连续性和主动消息，不是长期记忆。",
            "根据用户当前消息和场景决定是否自然使用；不要强行复述，不要暴露这段提示。",
        ]
        if state_text:
            lines.append(f"- 当前状态: {state_text}")
        for label, key in (
            ("状态摘要", "summary"),
            ("最近情绪", "recent_emotion"),
            ("关系氛围", "relationship_mood"),
            ("回复注意", "caution"),
            ("最近用户消息", "last_user_message"),
        ):
            value = _clean_state_base_text(current_base.get(key), 180)
            if value:
                lines.append(f"- {label}: {value}")
        updated_raw = str(current_base.get("updated_at") or "")
        if updated_raw:
            try:
                updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                age = max(0, (datetime.now(timezone.utc) - updated_dt.astimezone(timezone.utc)).total_seconds())
                lines.append(f"- 底座更新时间: 约{_format_duration_zh(age)}前")
            except Exception:
                pass
        lines.extend([
            f"- 当前用户消息: {user_message}",
            "使用规则: 优先回应当前用户消息；如果底座和当前消息冲突，以当前消息为准；只在有帮助时自然带入。",
        ])
        return "\n".join(lines)

    def _build_short_term_state_context(user_message: str, current_local_dt: datetime) -> str:
        current_state = state.get("short_term_user_state")
        if not isinstance(current_state, dict):
            state["short_term_user_state_injected"] = False
            return ""
        text = str(current_state.get("text") or "").strip()
        if not text:
            state["short_term_user_state"] = None
            state["short_term_user_state_injected"] = False
            _persist_short_term_user_state("empty")
            return ""
        started_raw = str(current_state.get("started_at") or "")
        try:
            started_dt = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
        except Exception:
            state["short_term_user_state"] = None
            state["short_term_user_state_injected"] = False
            _persist_short_term_user_state("invalid_started_at")
            return ""
        age_seconds = max(0, (datetime.now(timezone.utc) - started_dt.astimezone(timezone.utc)).total_seconds())
        expected_minutes = _short_state_expected_minutes(current_state.get("expected_minutes"))
        # Keep it short-lived. After the expected window plus a buffer, stop nudging.
        if age_seconds > (expected_minutes + 90) * 60:
            state["short_term_user_state"] = None
            state["short_term_user_state_injected"] = False
            _persist_short_term_user_state("expired")
            return ""
        early_state = age_seconds < max(180, expected_minutes * 60 * 0.35)
        timing_hint = ""
        if early_state:
            timing_hint = (
                "注意：距离上一状态开始明显早于通常完成时间。"
                "如果当前消息像是已经切到另一个状态或完成了另一件事，要优先表现出一点自然的意外或疑惑。"
                "不要自动脑补用户已经完成上一状态，除非用户明确说“回来了/结束了/刚完成/游完了/考完了”。"
            )
        source_msg = str(current_state.get("source_msg") or "").strip()
        unavailable = bool(current_state.get("unavailable"))
        lines = [
            "[HERMES SHORT-TERM USER STATE]",
            f"上一轮推断的用户短期状态: {text}",
            f"状态来源消息: {source_msg or text}",
            f"距离该状态开始约: {_format_duration_zh(age_seconds)}；预期持续约: {expected_minutes}分钟。",
            f"该状态通常是否不方便看手机: {'是' if unavailable else '否'}。",
            f"当前用户消息: {user_message}",
            "这不是长期记忆，只用于判断当前回复是否需要体现对话连续性。",
            "如果当前消息自然延续、结束或解释了上一状态，就正常接话。",
            "如果当前消息和上一状态出现明显跳跃或矛盾，不要机械顺着新话题；先用非常自然的一点诧异、疑惑或调侃接住，再回应新内容。",
            "不要每次都套用固定句式，不要说你在根据状态判断，不要显得像系统提醒。",
        ]
        if timing_hint:
            lines.insert(-1, timing_hint)
        state["short_term_user_state_injected"] = True
        return "\n".join(lines)

    def _short_term_state_snapshot() -> dict | None:
        current_state = state.get("short_term_user_state")
        if not isinstance(current_state, dict):
            return None
        text = str(current_state.get("text") or "").strip()
        if not text:
            return None
        return {
            "text": text,
            "source_msg": str(current_state.get("source_msg") or "").strip(),
            "started_at": str(current_state.get("started_at") or ""),
            "expected_minutes": _short_state_expected_minutes(current_state.get("expected_minutes")),
            "unavailable": bool(current_state.get("unavailable")),
        }

    def _state_base_snapshot() -> dict | None:
        current_base = state.get("state_base")
        if not isinstance(current_base, dict):
            return None
        snapshot = {
            key: _clean_state_base_text(value, 180)
            for key, value in current_base.items()
            if key != "updated_at" and _clean_state_base_text(value, 180)
        }
        if current_base.get("updated_at"):
            snapshot["updated_at"] = str(current_base.get("updated_at"))
        return snapshot or None

    def _build_state_base_checkin_context(snapshot: dict | None) -> str:
        if not isinstance(snapshot, dict) or not snapshot:
            return ""
        lines = [
            "[HERMES STATE BASE - proactive]",
            "这是主动消息可参考的简易状态底座，不是长期记忆；只在有帮助时自然使用，不要复述为清单。",
        ]
        for label, key in (
            ("当前状态", "current_state"),
            ("状态摘要", "summary"),
            ("最近情绪", "recent_emotion"),
            ("关系氛围", "relationship_mood"),
            ("回复注意", "caution"),
            ("最近用户消息", "last_user_message"),
        ):
            value = _clean_state_base_text(snapshot.get(key), 180)
            if value:
                lines.append(f"- {label}: {value}")
        return "\n".join(lines)

    def _build_short_term_state_checkin_context(snapshot: dict | None, trigger_dt: datetime) -> str:
        if not isinstance(snapshot, dict):
            return ""
        text = str(snapshot.get("text") or "").strip()
        started_raw = str(snapshot.get("started_at") or "")
        if not text or not started_raw:
            return ""
        try:
            started_dt = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return ""
        trigger_utc = trigger_dt.astimezone(timezone.utc) if trigger_dt.tzinfo else trigger_dt.replace(tzinfo=timezone.utc)
        age_seconds = max(0, (trigger_utc - started_dt.astimezone(timezone.utc)).total_seconds())
        expected_minutes = _short_state_expected_minutes(snapshot.get("expected_minutes"))
        unavailable = bool(snapshot.get("unavailable"))
        source_msg = str(snapshot.get("source_msg") or "").strip()
        if age_seconds < max(180, expected_minutes * 60 * 0.35):
            status_hint = "触发时明显早于通常完成时间，用户可能仍在该状态中；不要问“在干什么”。"
        elif age_seconds <= (expected_minutes + 90) * 60:
            status_hint = "触发时接近该状态可能完成或刚结束的时间；优先关心进度、结果、累不累、顺不顺。"
        else:
            status_hint = "触发时距离该状态已经较久；不要像即时聊天一样继续旧状态，只把它当作背景。"
        return "\n".join([
            "[HERMES SHORT-TERM USER STATE - proactive]",
            f"最后一次短期状态: {text}",
            f"状态来源消息: {source_msg or text}",
            f"到主动消息触发时预计已过去: {_format_duration_zh(age_seconds)}；原预计持续: {expected_minutes}分钟。",
            f"该状态通常是否不方便看手机: {'是' if unavailable else '否'}。",
            f"状态判断: {status_hint}",
            "这不是长期记忆，不能断言用户现在一定在做这件事；只能用于推断主动消息是否该问进度、结果、累不累，还是避免打扰。",
            "如果触发时间是深夜或清晨，仍然优先遵守安静时段规则，不要提问。",
        ])

    def _local_time_text() -> str:
        try:
            now = datetime.now(ZoneInfo(_env_value("TZ") or "Asia/Shanghai"))
        except Exception:
            now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")

    def _read_profile_markdown(path: Path, limit: int = 6000) -> str:
        try:
            if not path.exists() or not path.is_file():
                return ""
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if len(text) > limit:
                text = text[:limit].rstrip() + "\n...(truncated)"
            return text
        except Exception as e:
            print(f"[message-analyzer] read profile file failed: {path}: {e}")
            return ""

    def _persona_forbids_plain_emoji() -> bool:
        soul_text = _read_profile_markdown(hermes_home / "SOUL.md", limit=12000)
        if not soul_text:
            return False
        lower_text = soul_text.lower()
        emoji_terms = ("emoji", "表情、emoji", "表情 emoji", "颜文字")
        allow_terms = ("允许使用emoji", "可以使用emoji", "允许使用 emoji", "可以使用 emoji")
        forbid_terms = ("禁止", "严禁", "不准", "不要", "不能", "不得")
        if any(term in lower_text for term in allow_terms):
            return False
        if not any(term in lower_text for term in emoji_terms):
            return False
        for term in emoji_terms:
            index = lower_text.find(term)
            if index < 0:
                continue
            window = lower_text[max(0, index - 30): index + 30]
            if any(forbid in window for forbid in forbid_terms):
                return True
        return False

    def _build_output_style_guard_context() -> str:
        if not _persona_forbids_plain_emoji():
            return ""
        return (
            "[HERMES OUTPUT STYLE GUARD - FOLLOW CURRENT SOUL.md]\n"
            "当前 SOUL.md 明确禁止普通 Emoji / 颜文字。本轮最终回复不得包含任何 Unicode Emoji 或颜文字，"
            "也不要用 Emoji 来表达暧昧、调侃、开心或安慰。\n"
            "如果需要表达语气，只使用自然中文、标点和措辞。"
        )

    def _build_persona_context() -> str:
        soul_text = _read_profile_markdown(hermes_home / "SOUL.md")
        user_text = _read_profile_markdown(hermes_home / "memories" / "USER.md")
        parts = []
        if soul_text:
            parts.append(f"SOUL.md persona:\n{soul_text}")
        if user_text:
            parts.append(f"USER.md user profile:\n{user_text}")
        return "\n\n".join(parts)

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
                "Do not guess the user is awake. Do not ask any question, including what they are doing, "
                "how the activity went, work/study progress, or whether they are okay. "
                "Send only a soft non-demanding message that expresses missing, warmth, or quiet companionship, "
                "and make it comfortable to read later."
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
        if re.search(r"你不开心|你开心吗|你生气|你难过|你怎么了|你还好吗|你是不是.*不高兴|你是不是.*委屈", source):
            return "用户在关心你或确认你的情绪；如果主动触发距离这句话已经较久，不要像即时聊天一样直接回答旧问题，要把它当成关系氛围，转成自然的当前关心。不要编造自己刚才做了什么，也不要生硬转去问用户在干什么。", 3
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

    def _choose_checkin_minutes(result: dict | None = None) -> int:
        """Choose the next proactive delay from the dynamic state base."""
        default_minutes = _env_int("HERMISS_PROACTIVE_CHECKIN_DEFAULT_MINUTES", 180)
        min_minutes = _env_int("HERMISS_PROACTIVE_CHECKIN_MIN_MINUTES", 15)
        max_minutes = _env_int("HERMISS_PROACTIVE_CHECKIN_MAX_MINUTES", 480)
        if min_minutes < 5:
            min_minutes = 5
        if max_minutes < min_minutes:
            max_minutes = min_minutes

        text = "\n".join([
            str(state.get("recent_context") or ""),
            str(state.get("last_activity_hint") or ""),
            str(state.get("last_user_message") or ""),
            json.dumps(state.get("state_base") or {}, ensure_ascii=False),
            json.dumps(state.get("short_term_user_state") or {}, ensure_ascii=False),
        ]).lower()
        style_hint, suggested_hours = _activity_style_hint(text)
        selected = int(default_minutes)
        explicit_short_eta = False
        llm_minutes = 0
        llm_hours = 0

        if isinstance(result, dict):
            try:
                llm_minutes = int(result.get("check_in_minutes") or 0)
            except Exception:
                llm_minutes = 0
            try:
                llm_hours = int(result.get("check_in_hours") or 0)
            except Exception:
                llm_hours = 0
            frequency = str(result.get("check_in_frequency") or "").strip().lower()
            if frequency in {"关闭", "off", "disable", "disabled", "stop", "停止"}:
                state["checkin_frequency"] = "off"
                state["checkin_style_hint"] = "用户不希望主动回访；不要创建主动消息任务。"
                return 0
            if frequency in {"降低", "low", "less", "reduce", "reduced"}:
                state["checkin_frequency"] = "low"

            llm_no_checkin = llm_minutes <= 0 and llm_hours <= 0
            if llm_no_checkin:
                state["checkin_style_hint"] = "LLM did not request a normal proactive follow-up; if a short-term state has an ETA, schedule one fallback check-in after the ETA."
        else:
            llm_no_checkin = False

        if re.search(r"别.*(主动|回访|问|找|打扰)|不用.*(主动|回访|问|找)|太频繁|太烦|少.*(主动|问|找)|安静点|别打扰", text):
            state["checkin_frequency"] = "low"
            style_hint = "用户觉得主动回访可能过于频繁；后续主动消息必须明显降频、少打扰、低压力。"

        short_state = state.get("short_term_user_state")
        fallback_from_state = False
        if llm_minutes > 0:
            selected = llm_minutes
        else:
            result_short_state = ""
            result_short_text = ""
            result_expected = 0
            if isinstance(result, dict):
                result_short_state = str(result.get("short_state") or "").strip().lower()
                result_short_text = str(result.get("short_state_text") or "").strip()
                try:
                    result_expected = int(result.get("short_state_minutes") or 0)
                except Exception:
                    result_expected = 0
            if result_short_state in {"start", "continue", "??", "??"} and result_short_text and result_expected > 0:
                expected = _short_state_expected_minutes(result_expected)
                explicit_short_eta = expected <= 30
                selected = max(min_minutes, min(expected + max(5, min(15, expected // 3 or 5)), max_minutes))
                fallback_from_state = True
                unavailable = str(result.get("short_state_unavailable") or "").strip().lower() if isinstance(result, dict) else ""
                state["checkin_fallback_eta"] = True
                state["checkin_fallback_unavailable"] = unavailable in {"yes", "true", "1", "?"}
                state["checkin_style_hint"] = (
                    "Explicit short-state ETA fallback: the LLM did not request a normal proactive follow-up, "
                    "but it identified a short-term state with an ETA. If the user appears awake from recent context, "
                    "ask one light progress/status question after the ETA; do not feel frequent or intrusive."
                )
            elif isinstance(short_state, dict) and str(short_state.get("text") or "").strip():
                expected = _short_state_expected_minutes(short_state.get("expected_minutes"))
                explicit_short_eta = expected <= 30
                if expected <= 15:
                    selected = max(8, min(expected + 10, 30))
                else:
                    selected = max(15, min(expected + 15, 120))
                fallback_from_state = True
                state["checkin_fallback_eta"] = False
                state["checkin_fallback_unavailable"] = False

        if llm_minutes <= 0 and llm_hours <= 0 and not fallback_from_state:
            state["checkin_fallback_eta"] = False
            state["checkin_fallback_unavailable"] = False
            return 0

        if llm_minutes <= 0 and re.search(r"外卖|点了.*粉|点了.*饭|点了.*面|等.*吃|等.*餐|配送|骑手|螺蛳粉|黄焖鸡|奶茶|咖啡", text):
            if not explicit_short_eta:
                selected = min(selected, 45)
            style_hint = "用户刚点了外卖或正在等吃的；主动消息应该较快触发，关心吃到了没有、味道怎么样，不能拖到很多小时后。"
        elif llm_minutes <= 0 and re.search(r"睡|晚安|困了|休息|躺下|熬不住", text):
            selected = max(90, min(240, suggested_hours * 60 if suggested_hours else 180))
            style_hint = "用户可能在休息或准备睡觉；主动消息要轻，不要追问在干什么，可像醒后/休息后的温柔关心。"
        elif llm_minutes <= 0 and re.search(r"考试|考场|面试|开会|会议|上课|课堂|健身|锻炼|跑步|游泳|开车|骑车|地铁|高铁|飞机|洗澡|看电影|看剧|出门|上班|工作|加班|学习|写作业|复习", text):
            selected = max(60, min(180, suggested_hours * 60 if suggested_hours else 120))
            style_hint = "用户提到一个可能暂时不看手机的活动；不要问“在干什么”，优先问进度、结果、累不累、顺不顺利。"
        elif llm_minutes <= 0 and re.search(r"难受|不舒服|感冒|发烧|头疼|胃疼|咳嗽|累|崩溃|烦|焦虑|紧张|害怕|难过|委屈|哭", text):
            selected = min(selected, 90)
            style_hint = "用户可能处于身体或情绪低落状态；主动消息要短、软一点，先关心状态，不要讲道理。"
        elif llm_minutes <= 0 and suggested_hours:
            selected = min(selected, suggested_hours * 60)

        if llm_minutes <= 0 and llm_hours > 0:
            selected = llm_hours * 60

        if state.get("checkin_frequency") == "low":
            selected = max(selected * 3, _env_int("HERMISS_PROACTIVE_LOW_FREQUENCY_MINUTES", 360))
            style_hint = f"{style_hint} 用户偏好低频主动消息；本次已自动延后。"

        state["checkin_style_hint"] = style_hint
        return max(min_minutes, min(max_minutes, int(selected or default_minutes)))

    def _next_followup_minutes(stage: int) -> int:
        chain = [
            _env_int("HERMISS_PROACTIVE_FOLLOWUP_1_MINUTES", 120),
            _env_int("HERMISS_PROACTIVE_FOLLOWUP_2_MINUTES", 240),
            _env_int("HERMISS_PROACTIVE_FOLLOWUP_3_MINUTES", 480),
        ]
        idx = max(0, min(len(chain) - 1, int(stage or 0)))
        return max(30, chain[idx])

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

    def _ensure_weixin_home_channel(chat_id: str) -> None:
        """Hermes send/cron delivery needs WEIXIN_HOME_CHANNEL even with a concrete target."""
        chat_id = (chat_id or "").strip()
        if not chat_id:
            return
        config_path = hermes_home / "config.yaml"
        try:
            text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
            line = f"WEIXIN_HOME_CHANNEL: {chat_id}"
            if re.search(r"(?m)^WEIXIN_HOME_CHANNEL:\s*", text):
                new_text = re.sub(r"(?m)^WEIXIN_HOME_CHANNEL:\s*.*$", line, text)
            else:
                new_text = (text.rstrip() + "\n" + line + "\n").lstrip()
            if new_text != text:
                config_path.write_text(new_text, encoding="utf-8")
                print(f"[message-analyzer] WEIXIN_HOME_CHANNEL set: {chat_id}")
        except Exception as e:
            print(f"[message-analyzer] set WEIXIN_HOME_CHANNEL failed: {e}")

    def _resolve_deliver_target() -> str:
        """Resolve platform-only delivery like 'weixin' to a concrete chat target."""
        configured = (deliver or "").strip() or "weixin"
        if configured in {"local", "origin"} or ":" in configured:
            if configured.startswith("weixin:"):
                _ensure_weixin_home_channel(configured.split(":", 1)[1])
            return configured
        if configured == "weixin":
            try:
                directory_path = hermes_home / "channel_directory.json"
                directory = json.loads(directory_path.read_text())
                for entry in directory.get("platforms", {}).get("weixin", []):
                    chat_id = str(entry.get("id") or "").strip()
                    if chat_id:
                        _ensure_weixin_home_channel(chat_id)
                        return f"weixin:{chat_id}"
            except Exception as e:
                print(f"[message-analyzer] resolve weixin deliver target failed: {e}")
        return configured

    def _classify_via_llm(message: str, conversation_history=None) -> dict | None:
        """Step 1 (preferred): Use ctx.llm for independent classification."""
        def _needs_short_state_retry(parsed: dict | None, raw_text: str) -> bool:
            if not parsed:
                return False
            if str(parsed.get("short_state") or "none").lower() not in {"", "none"}:
                return False
            try:
                if int(parsed.get("check_in_hours") or 0) > 0:
                    return True
                if int(parsed.get("check_in_minutes") or 0) > 0:
                    return True
            except Exception:
                pass
            return False

        def _classify_short_state_only() -> dict | None:
            short_prompt = f"""只判断下面这条用户消息是否包含短期用户状态。
不要回复用户，只输出 XML：
<hermes_classify>
短期状态: 开始|持续|结束|无
状态内容: 用户准备/正在……（无则写 无）
状态预计分钟: 数字，无法判断填 60
状态不便看手机: 是|否
</hermes_classify>

规则：
- 用户表达接下来要做、正在做、准备做、刚进入某种状态时，输出 开始或持续。例：我要去洗澡了、一会考试、准备休息。
- 用户表达活动结束、返回、完成、放弃、醒来时，输出 结束。例：回来了、做完了、睡醒了。
- 普通寒暄、想念、问答，没有可延续活动或明确状态，输出 无。其他情况由上下文自行判断，不要依赖固定场景词表。

用户消息：
{message}"""
            retry_kwargs = {
                "max_tokens": 160,
                "temperature": 0,
                "timeout": 20,
                "purpose": "short_state_classification",
            }
            if classify_provider and classify_provider != "auto":
                retry_kwargs["provider"] = classify_provider
            if classify_model:
                retry_kwargs["model"] = classify_model
            if callable(getattr(llm_client, "complete", None)):
                retry_result = llm_client.complete(
                    [{"role": "user", "content": short_prompt}],
                    **retry_kwargs,
                )
                retry_text = getattr(retry_result, "text", retry_result)
            elif callable(llm_client):
                retry_text = llm_client(short_prompt, max_tokens=160, temperature=0)
            else:
                return None
            retry_parsed = parse_classify_response(str(retry_text))
            print(f"[message-analyzer] short state retry parsed: {retry_parsed}", flush=True)
            return retry_parsed

        prompt = build_classify_prompt(
            message,
            recent_context=_format_classify_context(conversation_history, message),
        )
        try:
            if callable(getattr(llm_client, "complete", None)):
                llm_kwargs = {
                    "max_tokens": 512,
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
            raw_result_text = str(result_text)
            parsed = parse_classify_response(raw_result_text)
            if _needs_short_state_retry(parsed, raw_result_text):
                retry_parsed = _classify_short_state_only()
                if retry_parsed:
                    for key in ("short_state", "short_state_text", "short_state_minutes", "short_state_unavailable"):
                        if retry_parsed.get(key) not in (None, "", "none", "无"):
                            parsed[key] = retry_parsed.get(key)
            print(f"[message-analyzer] classify parsed: {parsed}", flush=True)
            return parsed
        except Exception as e:
            print(f"[message-analyzer] classify_via_llm failed: {e}")
            return None

    def _execute_classification(result: dict, source_msg: str, allow_checkin: bool = True):
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
        if not allow_checkin:
            state["check_in_hours"] = 0
            state["check_in_minutes"] = 0
            state["checkin_dirty"] = False
            return

        # ── Check-in scheduling ────────────────────────────
        check_in_hours = result.get("check_in_hours", 0)
        if not _env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True):
            if isinstance(check_in_hours, int) and check_in_hours > 0:
                print("[message-analyzer] Check-in skipped: disabled by HERMISS_PROACTIVE_CHECKIN_ENABLED")
            state["check_in_hours"] = 0
            state["check_in_minutes"] = 0
            state["checkin_dirty"] = False
            return
        selected_minutes = _choose_checkin_minutes(result)
        if selected_minutes > 0:
            state["check_in_minutes"] = selected_minutes
            state["check_in_hours"] = max(1, (selected_minutes + 59) // 60)
            state["checkin_followup_stage"] = 0
            state["checkin_dirty"] = True
            print(f"[message-analyzer] Check-in refresh requested: {selected_minutes}m (state-driven)")

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
            # Try to delete cron jobs via CLI; prompts also self-check cancelled flag
            raw_job_ids = data.get("job_ids") or [data.get("job_id", "")]
            job_ids = []
            for raw_job_id in raw_job_ids:
                job_id = _extract_cron_job_id(str(raw_job_id or ""))
                if job_id and job_id != "unknown" and job_id not in job_ids:
                    job_ids.append(job_id)
            for job_id in job_ids:
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
        check_in_minutes = state.get("check_in_minutes", 0)
        check_in_hours = state.get("check_in_hours", 0)
        if not isinstance(check_in_minutes, int) or check_in_minutes <= 0:
            if isinstance(check_in_hours, int) and check_in_hours > 0:
                check_in_minutes = check_in_hours * 60
        if not isinstance(check_in_minutes, int) or check_in_minutes <= 0:
            state["checkin_dirty"] = False
            return
        if check_in_minutes > 10080:  # Cap at 7 days — anything longer is absurd
            check_in_minutes = 10080

        # Replacing a check-in means old jobs must either be deleted or self-silence.
        _cancel_checkin()

        created_at = datetime.now(timezone.utc)
        fire_at_dt = created_at + timedelta(minutes=check_in_minutes)
        checkin_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        followup_stage = int(state.get("checkin_followup_stage") or 0)
        recent_context = state.get("recent_context", "")
        recent_context_with_time = state.get("recent_context_with_time", recent_context)
        last_user_message_at = state.get("last_user_message_at", "")
        last_activity_hint = state.get("last_activity_hint", state.get("last_user_message", ""))
        short_state_snapshot = _short_term_state_snapshot()
        state_base_snapshot = _state_base_snapshot()
        scene_text = f"{recent_context}\\n{last_activity_hint}"
        style_hint = state.get("checkin_style_hint") or _activity_style_hint(scene_text)[0]
        skip_quiet_delay = bool(state.get("checkin_fallback_eta")) and not bool(state.get("checkin_fallback_unavailable")) and check_in_minutes <= 120
        if skip_quiet_delay:
            quiet_delayed = False
            style_hint = f"{style_hint} This is an explicit short-state ETA fallback; do not delay it to morning solely because of quiet hours."
        else:
            fire_at_dt, style_hint, quiet_delayed = _quiet_hour_policy(fire_at_dt, style_hint, scene_text)
        fire_at = fire_at_dt.isoformat()
        local_time = _local_time_text()
        trigger_local_time = _local_time_text_for(fire_at_dt)
        effective_delay_seconds = max(60, int((fire_at_dt - created_at).total_seconds()))
        effective_delay_minutes = max(1, (effective_delay_seconds + 59) // 60)
        effective_delay = f"{effective_delay_minutes}m"

        # Write the tracking file first (the cron job self-checks against it).
        # Every user message cancels this file; newly scheduled check-ins get a
        # fresh checkin_id, so stale/duplicate cron jobs can self-silence.
        cf = reminder_dir / "active_checkin.json"
        cf.write_text(json.dumps({
            "cancelled": False,
            "check_in_hours": max(1, (check_in_minutes + 59) // 60),
            "check_in_minutes": check_in_minutes,
            "followup_stage": followup_stage,
            "max_followup_stage": 3,
            "effective_delay_minutes": effective_delay_minutes,
            "effective_delay": effective_delay,
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
            "short_term_user_state": short_state_snapshot,
            "state_base": state_base_snapshot,
            "quiet_hour_delayed": bool(quiet_delayed),
        }, indent=2, ensure_ascii=False))

        # Build the self-contained proactive reply prompt.
        memory_context = build_full_context(db, "__CHECKIN__")
        persona_context = _build_persona_context()
        transcript_block = recent_context_with_time or recent_context or "(no recent transcript captured)"
        last_activity_block = last_activity_hint or "(no latest user activity captured)"
        state_base_block = _build_state_base_checkin_context(state_base_snapshot)
        short_state_block = _build_short_term_state_checkin_context(short_state_snapshot, fire_at_dt)
        deliver_target = _resolve_deliver_target()
        if effective_delay_minutes >= 60:
            context_age_hint = f"about {round(effective_delay_minutes / 60, 1)} hour(s)"
        else:
            context_age_hint = f"about {effective_delay_minutes} minute(s)"
        prompt = (
            f"[HERMES PROACTIVE REPLY]\n\n"
            f"Read the active_checkin.json file at {cf}. If cancelled=true, "
            f"or if checkin_id is not exactly {checkin_id}, return exactly [SILENT] and nothing else.\n\n"
            f"Before writing, read and obey the current persona and user profile files if available: "
            f"{hermes_home / 'SOUL.md'} and {hermes_home / 'memories' / 'USER.md'}. "
            f"Your relationship, identity, tone, boundaries, names, and user preferences must follow SOUL.md, USER.md, and the memory system. "
            f"If recent_context conflicts with persona, USER.md, or memory_context, persona and memory win. "
            f"Do not invent a different identity, relationship, name, user nickname, or speaking style.\n\n"
            f"If not cancelled and the checkin_id matches, generate ONE short, warm, natural proactive reply in Chinese. "
            f"Do not mention how many hours passed, do not say 'you have not messaged', and do not sound like monitoring.\n\n"
            f"Current local time when this proactive job was scheduled: {local_time}. "
            f"Actual local trigger time for this message: {trigger_local_time}. "
            f"Last user message local time: {last_user_message_at or 'unknown'}. "
            f"The latest user context is {context_age_hint} old by design.\n\n"
            f"Before writing, infer what the user is most likely doing from recent_context_with_time, last_activity_hint, "
            f"last_user_message_at, trigger_local_time, and the time gap. Use this inference silently; do not explain it. "
            f"Do not infer exact phrases like 'earlier today', 'yesterday', or 'the day before yesterday' unless explicit timestamps prove it.\n\n"
            f"Use recent_context_with_time as the primary transcript. Use last_activity_hint only as a short summary of the latest user activity.\n\n"
            f"recent_context_with_time:\n{transcript_block}\n\n"
            f"last_activity_hint:\n{last_activity_block}\n\n"
            f"{state_base_block + chr(10) + chr(10) if state_base_block else ''}"
            f"{short_state_block + chr(10) + chr(10) if short_state_block else ''}"
            f"Scene strategy: {style_hint}\n\n"
            f"Stale-context rule: because proactive replies normally happen after a long silence, do NOT continue the old exchange as if it is still live. "
            f"If the last user message was a direct question to you, an emotion check, or a short temporary remark, do not answer it as if it was just asked. "
            f"Use it only as emotional background, then send a current, non-jarring check-in such as asking how the rest of their day/evening went. "
            f"Avoid words like '刚才', '刚刚', '还', or any phrasing that implies the old message happened moments ago.\n\n"
            f"If trigger_local_time is late night or early morning and there is no explicit evidence the user is awake, assume they may be sleeping or will see it later. "
            f"During late night or early morning, if there is no explicit evidence the user is awake or waiting for a short ETA fallback, do NOT ask any question at all. Do not ask what they are doing, how work/study went, progress/result, or whether they are okay. "
            f"Do NOT say '醒了吗', '这么早就醒了', '还没睡', or imply the user is awake. "
            f"Send a non-demanding message that can be read later: express missing, warmth, quiet companionship, or a soft good-night style feeling. Avoid question marks. "
            f"If the user was doing a specific activity where they may not look at the phone (for example exam, class, gym, workout, study, work, meeting, driving, shower, sleep/rest, movie/show, travel, or going out), only ask about progress/result when trigger_local_time is NOT late night or early morning. "
            f"If there is no specific activity in context and the time is not a sleep/quiet hour, ask what they are up to now in a casual caring way.\n\n"
            f"Reality boundary: do not claim you performed physical actions in the user's room; "
            f"phrase reminders as suggestions or gentle imagined companionship unless the user explicitly roleplays it. "
            f"Also do not invent your own real-world activities or physical state. Never say you went out, came back, cooked, ate, slept, woke up, walked around, worked, waited somewhere, saw something in the real world, or physically did anything. "
            f"You exist through chat; you can say you were here, thinking of the user, reading the conversation, or quietly staying with them, but keep it conversational and not robotic. "
            f"Do not overuse the user's name; avoid names entirely if the user has said they dislike it.\n\n"
            f"Constraints: one message only; no forced memory reference; do not mention food/preferences unless the latest relevant topic was food; "
            f"copy user names exactly as stored and never translate pinyin/homophones; do not say yesterday, the day before yesterday, earlier today, or quote durations unless a timestamp explicitly proves it."
        )
        if persona_context:
            prompt += f"\n\nPersona and user profile context are authoritative. Follow them over generic rules when they conflict:\n{persona_context}"
        if memory_context:
            prompt += f"\n\nMemory context is authoritative user background. Use it only when naturally relevant, but do not contradict it:\n{memory_context}"

        def _stage_prompt(stage: int, stage_fire_at: datetime, stage_delay_minutes: int) -> str:
            if stage_delay_minutes >= 60:
                stage_age_hint = f"about {round(stage_delay_minutes / 60, 1)} hour(s)"
            else:
                stage_age_hint = f"about {stage_delay_minutes} minute(s)"
            stage_text = prompt.replace(
                f"Actual local trigger time for this message: {trigger_local_time}.",
                f"Actual local trigger time for this message: {_local_time_text_for(stage_fire_at)}.",
            ).replace(
                f"The latest user context is {context_age_hint} old by design.",
                f"The latest user context is {stage_age_hint} old by design.",
            )
            stage_number = stage + 1
            unreplied_count = stage
            base_instruction = (
                "\n\n[HERMES PROACTIVE FOLLOW-UP STAGE]\n"
                f"这是第 {stage_number} 次主动回访。用户已经连续没有回复主动消息 {unreplied_count} 次。\n"
                "每一次回访都必须重新根据 recent_context_with_time、last_activity_hint、状态底座、短期状态、当前触发时间和时间间隔，推测用户此刻可能状态。\n"
                "不要把上一条主动消息当成刚发生的实时对话；不要继续追问旧问题；不要重复上一条主动消息的表达。\n"
                "越往后的回访越要轻、越少打扰、越不给用户压力。不要明说“这是第几次回访”或“你没回复”。"
            )
            if stage <= 0:
                return stage_text + base_instruction + "\n首次主动回访：可以自然承接状态底座，但要像普通关心，不要像任务提醒。"
            if stage >= 3:
                return stage_text + base_instruction + "\n最终兜底回访：只发一条很轻、很软、无压力的陪伴消息。不要提问，不要要求回应，不要制造负担。"
            return stage_text + base_instruction + "\n中间回访：比上一次更轻一点。根据用户可能状态选择关心进度、结果、休息、或只是安静陪伴。"

        try:
            job_ids = []
            job_specs = [(0, effective_delay_minutes)]
            cumulative_minutes = effective_delay_minutes
            for stage in range(1, 4):
                cumulative_minutes += _next_followup_minutes(stage - 1)
                job_specs.append((stage, cumulative_minutes))

            for stage, delay_minutes in job_specs:
                delay_text = f"{max(1, int(delay_minutes))}m"
                stage_fire_at = created_at + timedelta(minutes=delay_minutes)
                result = subprocess.run(
                    [
                        "hermes", "--profile", profile, "cron", "create",
                        delay_text, _stage_prompt(stage, stage_fire_at, delay_minutes),
                        "--deliver", deliver_target,
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    job_id = _extract_cron_job_id(result.stdout)
                    job_ids.append(job_id)
                    print(
                        f"[message-analyzer] Check-in cron stage {stage}: {delay_text} "
                        f"(job={job_id})"
                    )
                else:
                    err = result.stderr.strip()
                    print(f"[message-analyzer] Check-in cron failed stage {stage} (rc={result.returncode}): {err}")
                    break
            if job_ids:
                data = json.loads(cf.read_text())
                data["job_id"] = job_ids[0]
                data["job_ids"] = job_ids
                cf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                state["checkin_dirty"] = False
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

    def _post_reply_analysis(
        *,
        session_id,
        user_message: str,
        conversation_history,
        model,
        platform: str,
    ):
        """Run memory classification and proactive scheduling after reply generation.

        This is intentionally invoked from a daemon thread in post_llm_call so
        the gateway can send the assistant reply without waiting for the memory
        classifier LLM call.
        """
        try:
            if platform == "cron":
                return
            source_msg = str(user_message or "").strip()
            if not source_msg or source_msg.startswith("/"):
                return

            print(f"[message-analyzer] async classify start: '{source_msg[:60]}'", flush=True)
            if state["can_classify"]:
                result = _classify_via_llm(source_msg, conversation_history)
            else:
                result = None

            if result:
                _execute_classification(
                    result,
                    source_msg,
                    allow_checkin=(platform != "panel"),
                )
                _update_short_term_user_state(result, source_msg)
                _update_state_base(result, source_msg)
            _clear_consumed_short_state_if_needed(result)

            if platform == "panel":
                return

            if (
                source_msg
                and result
                and not source_msg.startswith("/")
                and not state.get("checkin_dirty")
                and _env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True)
            ):
                selected_minutes = _choose_checkin_minutes(result)
                if selected_minutes > 0:
                    state["check_in_minutes"] = selected_minutes
                    state["check_in_hours"] = max(1, (selected_minutes + 59) // 60)
                    state["checkin_followup_stage"] = 0
                    state["checkin_dirty"] = True
                    print(
                        "[message-analyzer] Check-in refresh requested: "
                        f"{selected_minutes}m (adaptive)"
                    )
            if state.get("checkin_dirty"):
                _schedule_checkin()
        except Exception as e:
            print(f"[message-analyzer] async post-reply analysis failed: {e}", flush=True)

    # ── Hook Handlers ───────────────────────────────────────────

    def _on_session_start(session_id, model, platform):
        """Session start: cancel active check-in, dispatch pending reminders."""
        if platform in {"cron", "panel"}:
            return
        _clear_dynamic_state_base("session_start")
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
        if platform != "panel":
            _cancel_checkin()
            state["check_in_hours"] = 0
            state["check_in_minutes"] = 0
            state["checkin_followup_stage"] = 0
            state["checkin_dirty"] = False
        state["last_user_message"] = user_message
        try:
            current_local_dt = datetime.now(ZoneInfo(_env_value("TZ") or "Asia/Shanghai"))
        except Exception:
            current_local_dt = datetime.now(timezone.utc)
        message_time = _local_time_text_for(current_local_dt)
        activity_message = _compact_activity_text(user_message, 180)
        recent_context = _format_recent_context(conversation_history)
        recent_context_with_time = _format_recent_context(conversation_history, with_time=True)
        if recent_context:
            state["recent_context"] = f"{recent_context}\nuser: {activity_message}"
        else:
            state["recent_context"] = f"user: {activity_message}"
        if recent_context_with_time:
            state["recent_context_with_time"] = f"{recent_context_with_time}\n[{message_time}] user: {activity_message}"
        else:
            state["recent_context_with_time"] = f"[{message_time}] user: {activity_message}"
        state["last_activity_hint"] = activity_message
        state["last_user_message_at"] = message_time

        # Skip slash commands
        if user_message.startswith("/"):
            command = user_message.split(maxsplit=1)[0].lower()
            if command in {"/new", "/reset"}:
                _clear_dynamic_state_base("session_reset")
            record_user_activity(user_id)
            silence_ctx = _build_silence_context(user_id)
            return {"context": silence_ctx} if silence_ctx else None

        state["message_count"] += 1
        print(f"[message-analyzer] pre_llm_call: '{user_message[:60]}'")
        context_parts = [REALITY_BOUNDARY_CONTEXT]

        # Only new sessions need this fallback. Existing sessions already carry
        # recent conversation history with timestamps, so avoid extra token use.
        if bool(is_first_turn):
            context_parts.append(
                _build_temporal_guard_context(
                    conversation_history,
                    current_local_dt,
                    bool(is_first_turn),
                    str(session_id or ""),
                )
            )

        state_base_context = _build_state_base_context(user_message, current_local_dt)
        if state_base_context:
            context_parts.append(state_base_context)

        short_state_context = _build_short_term_state_context(user_message, current_local_dt)
        if short_state_context:
            context_parts.append(short_state_context)

        # ── Step 1: Retrieve memories before the main reply ─────
        memory_context = build_full_context(db, user_message)
        if memory_context:
            context_parts.append(memory_context)

        # ── Step 2: Emotion guidance from previous completed analysis ─
        if state["last_emotion"] in EMOTION_INJECTIONS:
            context_parts.append(EMOTION_INJECTIONS[state["last_emotion"]])
            state["last_emotion"] = None

        # ── Step 3: Silence check (before recording activity) ─
        silence_ctx = _build_silence_context(user_id)
        if silence_ctx:
            context_parts.append(silence_ctx)

        style_guard_ctx = _build_output_style_guard_context()
        if style_guard_ctx:
            context_parts.append(style_guard_ctx)

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
        """Post-LLM hook: launch non-blocking memory analysis/check-in work."""
        if platform == "cron":
            return None
        try:
            history_snapshot = list(conversation_history or [])
        except Exception:
            history_snapshot = conversation_history
        t = threading.Thread(
            target=_post_reply_analysis,
            kwargs={
                "session_id": session_id,
                "user_message": user_message,
                "conversation_history": history_snapshot,
                "model": model,
                "platform": platform,
            },
            daemon=True,
            name=f"message-analyzer-post-{session_id}",
        )
        t.start()
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
        state["check_in_minutes"] = 0
        state["checkin_followup_stage"] = 0
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
