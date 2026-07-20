from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


STATE_FILE = Path("/root/.hermes/profiles/hermiss/memory/short_term_user_state.json")
ACTIVE_CHECKIN_FILE = Path("/root/.hermes/profiles/hermiss/reminders/active_checkin.json")


def _clear_dynamic_state(reason: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "status": "empty",
                "reason": reason,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "state": None,
                "base": None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _extract_job_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    for line in value.splitlines():
        line = line.strip()
        if line.startswith("Job ID:"):
            return line.split(":", 1)[1].strip()
    return value.split()[0].strip() if value.split() else ""


def _cancel_active_checkin() -> None:
    if not ACTIVE_CHECKIN_FILE.exists():
        return
    try:
        data = json.loads(ACTIVE_CHECKIN_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    raw_job_ids = data.get("job_ids") or [data.get("job_id", "")]
    job_ids = []
    for raw_job_id in raw_job_ids:
        job_id = _extract_job_id(str(raw_job_id or ""))
        if job_id and job_id != "unknown" and job_id not in job_ids:
            job_ids.append(job_id)

    data["cancelled"] = True
    data["cancelled_reason"] = "session_reset"
    data["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    data["recent_context"] = ""
    data["recent_context_with_time"] = ""
    data["last_activity_hint"] = ""
    data["short_term_user_state"] = None
    data["state_base"] = None
    ACTIVE_CHECKIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_CHECKIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for job_id in job_ids:
        try:
            subprocess.run(
                ["hermes", "--profile", "hermiss", "cron", "delete", job_id],
                capture_output=True,
                timeout=8,
            )
        except Exception:
            pass


def handle(event_type: str, context: dict | None = None) -> None:
    if event_type in {"session:reset", "command:new", "command:reset"}:
        _cancel_active_checkin()
        _clear_dynamic_state(event_type)
