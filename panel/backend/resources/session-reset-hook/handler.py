from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


STATE_FILE = Path("/root/.hermes/profiles/hermiss/memory/short_term_user_state.json")


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


def handle(event_type: str, context: dict | None = None) -> None:
    if event_type in {"session:reset", "command:new", "command:reset"}:
        _clear_dynamic_state(event_type)
