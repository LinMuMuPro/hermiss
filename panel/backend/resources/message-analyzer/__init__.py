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
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import MemoryDB
from .classifier import CLASSIFY_SENTINEL, build_classify_prompt, parse_classify_response
from .classify_runtime import (
    build_short_state_retry_prompt,
    classify_with_llm,
    merge_short_state_retry,
    needs_short_state_retry,
)
from .checkin_prompt import build_checkin_prompt, build_stage_prompt
from .checkin_scheduler import dispatch_reminders as scheduler_dispatch_reminders
from .checkin_scheduler import schedule_checkin_jobs
from .classification_executor import execute_classification
from .conversation_context import (
    contains_temporal_gap_terms as context_contains_temporal_gap_terms,
    format_classify_context as context_format_classify_context,
    format_recent_context as context_format_recent_context,
    is_usable_chat_content as context_is_usable_chat_content,
    item_value as context_item_value,
    message_needs_temporal_guard as context_message_needs_temporal_guard,
)
from .state_prompt_context import (
    build_short_term_state_checkin_context as state_prompt_build_short_term_checkin_context,
    build_state_base_checkin_context as state_prompt_build_base_checkin_context,
    build_state_base_context as state_prompt_build_base_context,
)
from .temporal_context import build_temporal_guard_context as temporal_build_guard_context
from .proactive_policy import (
    activity_style_hint as policy_activity_style_hint,
    choose_checkin_hours as policy_choose_checkin_hours,
    choose_checkin_minutes as policy_choose_checkin_minutes,
    next_followup_minutes as policy_next_followup_minutes,
    quiet_hour_policy as policy_quiet_hour_policy,
)
from .persona_context import (
    build_output_style_guard_context as persona_build_output_style_guard_context,
    build_persona_context as persona_build_context,
    persona_forbids_plain_emoji,
    read_profile_markdown as persona_read_profile_markdown,
)
from .retriever import build_full_context
from .state_context import (
    clean_state_base_text as state_clean_text,
    compact_activity_text as state_compact_activity_text,
    format_duration_zh as state_format_duration_zh,
    short_state_expected_minutes as state_short_state_expected_minutes,
)
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
    profile = os.environ.get("HERMES_PROFILE", "hermiss")
    try:
        from hermes_constants import get_hermes_home
        hermes_home = Path(get_hermes_home())
    except ImportError:
        hermes_home = Path.home() / ".hermes"
    profile_home = hermes_home / "profiles" / profile
    if profile_home.exists() and not (hermes_home / "config.yaml").exists():
        hermes_home = profile_home

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

    def _runtime_mode() -> str:
        raw = os.environ.get("HERMISS_RUNTIME_MODE") or _profile_env_value("HERMISS_RUNTIME_MODE") or "companion"
        normalized = str(raw).strip().lower()
        if normalized in {"roleplay", "role_play", "story", "rp", "剧情", "剧情扮演", "剧情扮演模式"}:
            return "roleplay"
        return "companion"

    if _runtime_mode() == "roleplay":
        print("[message-analyzer] roleplay mode: dynamic memory, state base and proactive check-ins disabled")
        return

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
        "current_session_id": "",
    }
    short_state_file = hermes_home / "memory" / "short_term_user_state.json"

    def _clean_state_base_text(value, limit: int = 160) -> str:
        return state_clean_text(value, limit)

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
            saved_session_id = str(data.get("session_id") or "")
            if saved_session_id:
                state["current_session_id"] = saved_session_id
        except Exception as e:
            print(f"[message-analyzer] load state base failed: {e}", flush=True)

    def _persist_short_term_user_state(reason: str = "updated") -> None:
        try:
            current_state = state.get("short_term_user_state")
            current_base = state.get("state_base")
            payload = {
                "status": "none",
                "reason": reason,
                "session_id": str(state.get("current_session_id") or ""),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "state": None,
                "base": current_base if isinstance(current_base, dict) else None,
            }
            if isinstance(current_state, dict) and str(current_state.get("text") or "").strip():
                payload["status"] = "active"
                payload["state"] = current_state
            if isinstance(current_base, dict) and (
                str(current_base.get("current_state") or "").strip()
                or str(current_base.get("summary") or "").strip()
            ):
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
        return context_item_value(item, *names)

    def _format_recent_context(conversation_history, limit: int = 6, with_time: bool = False) -> str:
        return context_format_recent_context(
            conversation_history,
            limit=limit,
            with_time=with_time,
            timestamp_to_local_text=_timestamp_to_local_text,
            compact_activity_text=_compact_activity_text,
        )

    def _format_classify_context(conversation_history, current_user_message: str, limit: int = 6) -> str:
        return context_format_classify_context(
            conversation_history,
            current_user_message,
            limit=limit,
            format_recent_context_func=_format_recent_context,
        )

    def _compact_activity_text(content: str, limit: int = 160) -> str:
        return state_compact_activity_text(content, limit)

    def _message_needs_temporal_guard(message: str) -> bool:
        return context_message_needs_temporal_guard(message)

    def _contains_temporal_gap_terms(message: str) -> bool:
        return context_contains_temporal_gap_terms(message)

    def _format_duration_zh(seconds: float) -> str:
        return state_format_duration_zh(seconds)

    def _is_usable_chat_content(content: str) -> bool:
        return context_is_usable_chat_content(content)

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
        return temporal_build_guard_context(
            conversation_history=conversation_history,
            current_local_dt=current_local_dt,
            is_first_turn=is_first_turn,
            session_id=session_id,
            item_value=_item_value,
            timestamp_to_local_dt=_timestamp_to_local_dt,
            local_time_text_for=_local_time_text_for,
            format_duration_zh=_format_duration_zh,
            is_usable_chat_content=_is_usable_chat_content,
            contains_temporal_gap_terms=_contains_temporal_gap_terms,
            find_global_last_chat_message=_find_global_last_chat_message,
        )

    def _short_state_expected_minutes(value) -> int:
        return state_short_state_expected_minutes(value)

    def _clear_dynamic_state_base(reason: str = "cleared") -> None:
        try:
            _cancel_checkin()
        except Exception:
            pass
        try:
            cf = reminder_dir / "active_checkin.json"
            if cf.exists():
                data = json.loads(cf.read_text(encoding="utf-8"))
                data["cancelled"] = True
                data["cancelled_reason"] = reason
                data["cancelled_at"] = datetime.now(timezone.utc).isoformat()
                data["recent_context"] = ""
                data["recent_context_with_time"] = ""
                data["last_activity_hint"] = ""
                data["short_term_user_state"] = None
                data["state_base"] = None
                cf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        state["check_in_hours"] = 0
        state["check_in_minutes"] = 0
        state["checkin_followup_stage"] = 0
        state["checkin_dirty"] = False
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

    def _set_current_session(session_id, reason: str = "session_change") -> None:
        next_session_id = str(session_id or "")
        if not next_session_id:
            return
        if next_session_id != str(state.get("current_session_id") or ""):
            state["current_session_id"] = next_session_id
            _clear_dynamic_state_base(reason)

    def _latest_interactive_session() -> dict | None:
        state_db = hermes_home / "state.db"
        if not state_db.exists():
            return None
        try:
            with sqlite3.connect(str(state_db)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT id, source, COALESCE(message_count, 0) AS message_count, started_at, ended_at
                    FROM sessions
                    WHERE source IS NULL OR source NOT LIKE 'cron%'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            print(f"[message-analyzer] session watcher query failed: {e}", flush=True)
            return None

    def _start_session_watcher() -> None:
        def _watch() -> None:
            while True:
                try:
                    latest = _latest_interactive_session()
                    latest_id = str((latest or {}).get("id") or "")
                    if latest_id and latest_id != str(state.get("current_session_id") or ""):
                        message_count = int((latest or {}).get("message_count") or 0)
                        state["current_session_id"] = latest_id
                        if message_count <= 0:
                            _clear_dynamic_state_base("empty_session_created")
                            print(
                                "[message-analyzer] Empty new session detected; dynamic state cleared: "
                                f"{latest_id}",
                                flush=True,
                            )
                        else:
                            _persist_short_term_user_state("session_watcher_sync")
                    time.sleep(2)
                except Exception as e:
                    print(f"[message-analyzer] session watcher failed: {e}", flush=True)
                    time.sleep(5)

        threading.Thread(
            target=_watch,
            daemon=True,
            name="message-analyzer-session-watcher",
        ).start()

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
        current_state = state.get("short_term_user_state")
        if action == "continue" and isinstance(current_state, dict) and str(current_state.get("text") or "").strip():
            existing_text = str(current_state.get("text") or "").strip()
            next_text = text[:120] if text else existing_text[:120]
            existing_minutes = _short_state_expected_minutes(current_state.get("expected_minutes"))
            state["short_term_user_state"] = {
                "text": next_text,
                "source_msg": str(current_state.get("source_msg") or _compact_activity_text(source_msg, 120))[:120],
                "started_at": str(current_state.get("started_at") or now.isoformat()),
                "expected_minutes": max(existing_minutes, minutes),
                "unavailable": bool(current_state.get("unavailable")) or unavailable,
                "last_interaction": _compact_activity_text(source_msg, 120)[:120],
                "last_interaction_at": now.isoformat(),
            }
            print(
                "[message-analyzer] Short state continued: "
                f"{next_text[:60]} ({max(existing_minutes, minutes)}m, unavailable={bool(current_state.get('unavailable')) or unavailable})",
                flush=True,
            )
            _persist_short_term_user_state("continued")
            return
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

        inferred_state = _clean_state_base_text(result.get("state_base_summary"), 120)
        mood = _clean_state_base_text(result.get("state_base_mood"), 140)
        caution = _clean_state_base_text(result.get("state_base_caution"), 180)

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
        current_short_state = state.get("short_term_user_state")
        existing_state_text = ""
        if isinstance(current_short_state, dict):
            existing_state_text = _clean_state_base_text(current_short_state.get("text"), 120)
        if short_state == "continue" and existing_state_text:
            current_base["current_state"] = existing_state_text
        elif short_state == "start" and short_text:
            current_base["current_state"] = short_text
        elif short_state in {"end", "ended", "finish", "finished"}:
            current_base["current_state"] = ""
        elif inferred_state:
            current_base["current_state"] = inferred_state

        current_state_text = _clean_state_base_text(current_base.get("current_state"), 120)
        if current_state_text:
            current_base["summary"] = current_state_text
        elif inferred_state:
            current_base["summary"] = inferred_state
        else:
            current_base["summary"] = "用户现在正在与你闲聊"

        current_base["state_at"] = now.isoformat()

        if recent_emotion:
            current_base["recent_emotion"] = recent_emotion
        elif not current_base.get("recent_emotion"):
            current_base["recent_emotion"] = ""

        if mood:
            current_base["relationship_mood"] = mood
        if caution:
            current_base["caution"] = caution

        current_base.pop("last_user_message", None)
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
        return state_prompt_build_base_context(
            current_base=current_base,
            state_text=state_text,
            user_message=user_message,
            clean_state_base_text=_clean_state_base_text,
            format_duration_zh=_format_duration_zh,
        )

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
        return state_prompt_build_base_checkin_context(
            snapshot=snapshot,
            clean_state_base_text=_clean_state_base_text,
        )

    def _build_short_term_state_checkin_context(snapshot: dict | None, trigger_dt: datetime) -> str:
        return state_prompt_build_short_term_checkin_context(
            snapshot=snapshot,
            trigger_dt=trigger_dt,
            short_state_expected_minutes=_short_state_expected_minutes,
            format_duration_zh=_format_duration_zh,
        )

    def _local_time_text() -> str:
        try:
            now = datetime.now(ZoneInfo(_env_value("TZ") or "Asia/Shanghai"))
        except Exception:
            now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S %Z")

    def _read_profile_markdown(path: Path, limit: int = 6000) -> str:
        return persona_read_profile_markdown(path, limit=limit)

    def _persona_forbids_plain_emoji() -> bool:
        return persona_forbids_plain_emoji(_read_profile_markdown(hermes_home / "SOUL.md", limit=12000))

    def _build_output_style_guard_context() -> str:
        return persona_build_output_style_guard_context(_read_profile_markdown(hermes_home / "SOUL.md", limit=12000))

    def _build_persona_context() -> str:
        soul_text = _read_profile_markdown(hermes_home / "SOUL.md")
        user_text = _read_profile_markdown(hermes_home / "memories" / "USER.md")
        return persona_build_context(soul_text=soul_text, user_text=user_text)

    def _quiet_hour_policy(target_dt_utc: datetime, style_hint: str, source_text: str) -> tuple[datetime, str, bool]:
        return policy_quiet_hour_policy(
            target_dt_utc,
            style_hint,
            source_text,
            env_int=_env_int,
            tz_name=_env_value("TZ") or "Asia/Shanghai",
        )

    def _activity_style_hint(text: str) -> tuple[str, int]:
        return policy_activity_style_hint(text)

    def _choose_checkin_hours() -> int:
        return policy_choose_checkin_hours(state, _env_int)

    def _choose_checkin_minutes(result: dict | None = None) -> int:
        return policy_choose_checkin_minutes(state, result, _env_int, _short_state_expected_minutes)

    def _next_followup_minutes(stage: int) -> int:
        return policy_next_followup_minutes(stage, _env_int)

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
        def _classify_short_state_only() -> dict | None:
            short_prompt = build_short_state_retry_prompt(message, _short_term_state_snapshot())
            retry_text = classify_with_llm(
                llm_client=llm_client,
                prompt=short_prompt,
                provider=classify_provider,
                model=classify_model,
                purpose="short_state_classification",
                max_tokens=160,
                temperature=0,
                timeout=20,
            )
            if retry_text is None:
                return None
            retry_parsed = parse_classify_response(str(retry_text))
            print(f"[message-analyzer] short state retry parsed: {retry_parsed}", flush=True)
            return retry_parsed

        prompt = build_classify_prompt(
            message,
            recent_context=_format_classify_context(conversation_history, message),
            current_short_state=json.dumps(_short_term_state_snapshot(), ensure_ascii=False) if _short_term_state_snapshot() else "",
            current_local_time=_local_time_text(),
        )
        try:
            result_text = classify_with_llm(
                llm_client=llm_client,
                prompt=prompt,
                provider=classify_provider,
                model=classify_model,
                purpose="memory_classification",
                max_tokens=512,
                temperature=0.1,
                timeout=30,
            )
            if result_text is None:
                print("[message-analyzer] classify_via_llm skipped: ctx.llm has no supported API")
                return None
            raw_result_text = str(result_text)
            parsed = parse_classify_response(raw_result_text)
            if needs_short_state_retry(parsed, raw_result_text):
                parsed = merge_short_state_retry(parsed, _classify_short_state_only())
            print(f"[message-analyzer] classify parsed: {parsed}", flush=True)
            return parsed
        except Exception as e:
            print(f"[message-analyzer] classify_via_llm failed: {e}")
            return None

    def _execute_classification(result: dict, source_msg: str, allow_checkin: bool = True):
        """Execute actions from classification result."""
        execute_classification(
            db=db,
            state=state,
            result=result,
            source_msg=source_msg,
            allow_checkin=allow_checkin,
            proactive_enabled=_env_bool("HERMISS_PROACTIVE_CHECKIN_ENABLED", True),
            choose_checkin_minutes=_choose_checkin_minutes,
            emotion_injections=EMOTION_INJECTIONS,
        )

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

    def _ensure_checkin_observer_script() -> Path:
        scripts_dir = hermes_home / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "message_analyzer_checkin_observer.py"
        script = r'''
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return datetime.now(timezone.utc)


def compact(value, limit=180):
    text = ' '.join(str(value or '').split())
    return text[:limit]

profile = os.environ.get('HERMES_PROFILE', 'hermiss')
hermes_home = os.environ.get('HERMES_HOME')
root = Path(hermes_home) if hermes_home else Path.home() / '.hermes' / 'profiles' / profile
reminder_dir = root / 'reminders'
observer_file = reminder_dir / 'checkin_observer.json'
active_file = reminder_dir / 'active_checkin.json'
state_file = root / 'memory' / 'short_term_user_state.json'
state_db = root / 'state.db'

if not observer_file.exists() or not active_file.exists():
    raise SystemExit(0)

observer = json.loads(observer_file.read_text(encoding='utf-8'))
active = json.loads(active_file.read_text(encoding='utf-8'))
checkin_id = str(observer.get('checkin_id') or '')
if not checkin_id or str(active.get('checkin_id') or '') != checkin_id:
    raise SystemExit(0)
if active.get('cancelled'):
    raise SystemExit(0)

created_at = parse_dt(observer.get('created_at'))
created_ts = created_at.timestamp()
user_replied = False
try:
    conn = sqlite3.connect(state_db)
    row = conn.execute(
        "select id, content, timestamp from messages where role='user' and timestamp > ? order by timestamp desc limit 1",
        (created_ts,),
    ).fetchone()
    if row and row[1] and not str(row[1]).startswith('[IMPORTANT: You are running as a scheduled cron job'):
        user_replied = True
except Exception:
    user_replied = False

if user_replied:
    raise SystemExit(0)

now = datetime.now(timezone.utc).isoformat()
try:
    saved = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
except Exception:
    saved = {}
base = saved.get('base') if isinstance(saved.get('base'), dict) else None
if not base:
    base = active.get('state_base') if isinstance(active.get('state_base'), dict) else {}
if not isinstance(base, dict):
    base = {}

base['current_state'] = '用户暂时没有回复主动消息'
base['summary'] = '用户暂时没有回复主动消息'
base['state_at'] = now
base['relationship_mood'] = compact(base.get('relationship_mood') or '安静')
base['caution'] = '不要连续催问；下一次回访要更轻、更少打扰'
base.pop('last_user_message', None)
base['updated_at'] = now

state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps({
    'status': 'active',
    'reason': 'checkin_unreplied_2m',
    'session_id': saved.get('session_id') or '',
    'updated_at': now,
    'state': None,
    'base': {k: v for k, v in base.items() if isinstance(v, bool) or str(v or '').strip()},
}, ensure_ascii=False, indent=2), encoding='utf-8')

active['observer_status'] = 'no_user_reply_after_2m'
active['observer_updated_at'] = now
active['short_term_user_state'] = None
active['state_base'] = {k: v for k, v in base.items() if isinstance(v, bool) or str(v or '').strip()}
active_file.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding='utf-8')
'''
        if not script_path.exists() or script_path.read_text(encoding="utf-8", errors="ignore") != script:
            script_path.write_text(script, encoding="utf-8")
        return script_path

    def _schedule_checkin_observer(session_id, assistant_response: str) -> None:
        text = " ".join(str(assistant_response or "").split())
        if not text or text == "[SILENT]":
            return
        cf = reminder_dir / "active_checkin.json"
        try:
            active = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            return
        if active.get("cancelled"):
            return
        checkin_id = str(active.get("checkin_id") or "")
        if not checkin_id:
            return
        observer_file = reminder_dir / "checkin_observer.json"
        observer_file.write_text(json.dumps({
            "checkin_id": checkin_id,
            "cron_session_id": str(session_id or ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "assistant_response": _compact_activity_text(text, 200),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            script_path = _ensure_checkin_observer_script()
            result = subprocess.run(
                [
                    "hermes", "--profile", profile, "cron", "create", "2m",
                    "--name", f"HERMES CHECKIN OBSERVER {checkin_id}",
                    "--script", script_path.name,
                    "--no-agent",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                print(f"[message-analyzer] Check-in observer scheduled: {checkin_id}", flush=True)
            else:
                print(f"[message-analyzer] Check-in observer schedule failed: {result.stderr.strip()}", flush=True)
        except Exception as e:
            print(f"[message-analyzer] Check-in observer error: {e}", flush=True)

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
        prompt = build_checkin_prompt(
            checkin_file=cf,
            checkin_id=checkin_id,
            soul_path=hermes_home / "SOUL.md",
            user_path=hermes_home / "memories" / "USER.md",
            local_time=local_time,
            trigger_local_time=trigger_local_time,
            last_user_message_at=last_user_message_at,
            context_age_hint=context_age_hint,
            transcript_block=transcript_block,
            last_activity_block=last_activity_block,
            state_base_block=state_base_block,
            short_state_block=short_state_block,
            style_hint=style_hint,
            persona_context=persona_context,
            memory_context=memory_context,
        )

        def _stage_prompt(stage: int, stage_fire_at: datetime, stage_delay_minutes: int) -> str:
            if stage_delay_minutes >= 60:
                stage_age_hint = f"about {round(stage_delay_minutes / 60, 1)} hour(s)"
            else:
                stage_age_hint = f"about {stage_delay_minutes} minute(s)"
            return build_stage_prompt(
                base_prompt=prompt,
                stage=stage,
                stage_trigger_local_time=_local_time_text_for(stage_fire_at),
                stage_age_hint=stage_age_hint,
                original_trigger_local_time=trigger_local_time,
                original_age_hint=context_age_hint,
            )

        try:
            job_ids = schedule_checkin_jobs(
                profile=profile,
                deliver_target=deliver_target,
                checkin_file=cf,
                created_at=created_at,
                effective_delay_minutes=effective_delay_minutes,
                next_followup_minutes=_next_followup_minutes,
                stage_prompt_builder=_stage_prompt,
                extract_cron_job_id=_extract_cron_job_id,
            )
            if job_ids:
                state["checkin_dirty"] = False
        except FileNotFoundError:
            print("[message-analyzer] hermes CLI not on PATH ? proactive reply not scheduled")
        except Exception as e:
            print(f"[message-analyzer] Check-in schedule error: {e}")

    def _dispatch_reminders():
        """Wire pending reminders to hermes cron jobs."""
        scheduler_dispatch_reminders(
            reminder_dir=reminder_dir,
            profile=profile,
            deliver=deliver,
            get_undispatched_reminders=get_undispatched_reminders,
            mark_reminder_dispatched=mark_reminder_dispatched,
            compute_delay=_compute_delay,
            extract_cron_job_id=_extract_cron_job_id,
        )

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
            analysis_session_id = str(session_id or "")
            if analysis_session_id and analysis_session_id != str(state.get("current_session_id") or ""):
                print(
                    "[message-analyzer] async analysis skipped: stale session "
                    f"{analysis_session_id}",
                    flush=True,
                )
                return

            print(f"[message-analyzer] async classify start: '{source_msg[:60]}'", flush=True)
            if state["can_classify"]:
                result = _classify_via_llm(source_msg, conversation_history)
            else:
                result = None

            if analysis_session_id and analysis_session_id != str(state.get("current_session_id") or ""):
                print(
                    "[message-analyzer] async analysis result discarded: stale session "
                    f"{analysis_session_id}",
                    flush=True,
                )
                return

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
        if platform == "cron":
            return
        state["current_session_id"] = str(session_id or "")
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
        if is_first_turn or str(session_id or "") != str(state.get("current_session_id") or ""):
            _set_current_session(session_id, "session_first_turn")
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
            _schedule_checkin_observer(session_id, assistant_response)
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
    _start_session_watcher()

    print(
        f"[message-analyzer] v1.0 registered "
        f"(classify={'llm' if can_classify else 'inline'}, "
        f"memories={db.memory_count()})"
    )
