"""Execute parsed classification results for message-analyzer."""


EMPTY_VALUES = {"无", "none", "None", "NONE"}


def normalize_memory_entry(value) -> str:
    entry = str(value or "")
    return "" if entry.strip() in EMPTY_VALUES else entry


def memory_items_from_result(result: dict) -> tuple[list[dict], str]:
    memory_type = result.get("memory", "none")
    memory_entry = normalize_memory_entry(result.get("memory_entry", ""))
    importance = result.get("importance", "low")
    memory_items = result.get("memories") or []
    if not memory_items and memory_type != "none" and memory_entry:
        memory_items = [{
            "memory": memory_type,
            "memory_entry": memory_entry,
            "importance": importance,
        }]
    return memory_items, importance


def store_memories(db, result: dict, source_msg: str) -> None:
    emotion = result.get("emotion", "neutral")
    memory_items, default_importance = memory_items_from_result(result)
    for item in memory_items:
        item_type = item.get("memory", "none")
        item_entry = normalize_memory_entry(item.get("memory_entry", ""))
        item_importance = item.get("importance", default_importance)
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


def execute_classification(
    *,
    db,
    state: dict,
    result: dict,
    source_msg: str,
    allow_checkin: bool,
    proactive_enabled: bool,
    choose_checkin_minutes,
    emotion_injections: dict,
) -> None:
    store_memories(db, result, source_msg)

    emotion = result.get("emotion", "neutral")
    if emotion in emotion_injections:
        state["last_emotion"] = emotion

    if not allow_checkin:
        state["check_in_hours"] = 0
        state["check_in_minutes"] = 0
        state["checkin_dirty"] = False
        return

    check_in_hours = result.get("check_in_hours", 0)
    if not proactive_enabled:
        if isinstance(check_in_hours, int) and check_in_hours > 0:
            print("[message-analyzer] Check-in skipped: disabled by HERMISS_PROACTIVE_CHECKIN_ENABLED")
        state["check_in_hours"] = 0
        state["check_in_minutes"] = 0
        state["checkin_dirty"] = False
        return

    selected_minutes = choose_checkin_minutes(result)
    if selected_minutes > 0:
        state["check_in_minutes"] = selected_minutes
        state["check_in_hours"] = max(1, (selected_minutes + 59) // 60)
        state["checkin_followup_stage"] = 0
        state["checkin_dirty"] = True
        print(f"[message-analyzer] Check-in refresh requested: {selected_minutes}m (state-driven)")
