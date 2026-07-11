"""
Reminder Manager — 提醒调度

三类提醒：
  1. timed      — 用户明确要求的定时提醒（fireAt 一次性触发）
  2. contextual — 情境提醒，由 LLM 在后续对话中交叉匹配
  3. silence    — 断联检测，N 分钟内无回复时主动回复

所有函数显式接收 base_path，不依赖模块级全局变量。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path


# Silence detection: how long (seconds) to wait before proactive reply
SILENCE_THRESHOLD = 300  # 5 minutes

# In-memory silence tracker: {user_id: last_message_timestamp}
_silence_tracker: dict[str, float] = {}


def ensure_reminder_dir(base_path: Path) -> Path:
    """Ensure reminder directory exists. Returns the path."""
    base_path.mkdir(parents=True, exist_ok=True)
    return base_path


def record_user_activity(user_id: str):
    """Update the last activity timestamp for a user (silence detection)."""
    _silence_tracker[user_id] = time.time()


def check_silence(user_id: str) -> bool:
    """
    Check if user has been silent beyond the threshold.
    Returns True if Hermes should proactively reach out.
    """
    last = _silence_tracker.get(user_id, 0)
    if last == 0:
        return False
    return (time.time() - last) > SILENCE_THRESHOLD


def create_timed_reminder(
    base_path: Path, reminder_time: str, reminder_text: str, user_id: str = ""
) -> dict:
    """
    Create a one-shot timed reminder.

    Uses ISO 8601 fire_at (not cron) so there is no yearly-repeat bug.
    The reminder is written to pending_reminders.jsonl and will be dispatched
    by the plugin via ``hermes cron create`` at the appropriate delay.

    reminder_time: ISO 8601 datetime string
    Returns: dict with status and reminder details
    """
    try:
        parsed = datetime.fromisoformat(reminder_time)
        now = datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed <= now:
            return {"status": "skipped", "reason": "Reminder time is in the past"}

        task_file = base_path / "pending_reminders.jsonl"
        entry = json.dumps({
            "reminder_text": reminder_text,
            "fire_at": parsed.isoformat(),
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dispatched": False,
        })
        with open(task_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")

        return {
            "status": "created",
            "fire_at": parsed.isoformat(),
            "text": reminder_text,
        }

    except ValueError as e:
        return {"status": "error", "reason": f"Invalid datetime: {e}"}


def get_undispatched_reminders(base_path: Path) -> list[dict]:
    """Retrieve reminders not yet dispatched to the Hermes task scheduler."""
    task_file = base_path / "pending_reminders.jsonl"
    if not task_file.exists():
        return []
    reminders = []
    for line in task_file.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                data = json.loads(line)
                if not data.get("dispatched", False):
                    reminders.append(data)
            except json.JSONDecodeError:
                continue
    return reminders


def mark_reminder_dispatched(base_path: Path, fire_at: str, reminder_text: str):
    """Mark a reminder as dispatched to the scheduler."""
    task_file = base_path / "pending_reminders.jsonl"
    if not task_file.exists():
        return

    lines = task_file.read_text(encoding="utf-8").strip().split("\n")
    updated = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if data.get("reminder_text") == reminder_text and data.get("fire_at") == fire_at:
                data["dispatched"] = True
            updated.append(json.dumps(data))
        except json.JSONDecodeError:
            updated.append(line)

    task_file.write_text("\n".join(updated) + "\n", encoding="utf-8")



