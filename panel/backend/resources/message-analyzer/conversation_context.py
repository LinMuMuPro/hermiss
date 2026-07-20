"""Conversation context formatting helpers for message-analyzer."""


def item_value(item, *names):
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


def is_usable_chat_content(content: str) -> bool:
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


def format_recent_context(
    conversation_history,
    *,
    limit: int = 6,
    with_time: bool = False,
    timestamp_to_local_text,
    compact_activity_text,
) -> str:
    """Return a compact recent-chat transcript for classification and proactive scene inference."""
    if not conversation_history:
        return ""
    rows = []
    try:
        items = list(conversation_history)[-limit:]
    except Exception:
        return ""
    for item in items:
        role = str(item_value(item, "role", "sender") or "unknown")
        display_role = "你" if role == "assistant" else ("用户" if role == "user" else role)
        content = str(item_value(item, "content", "message") or "")
        content = " ".join(content.split())
        content = compact_activity_text(content, 180)
        if not content:
            continue
        if role == "tool":
            continue
        if not is_usable_chat_content(content):
            continue
        if len(content) > 180:
            content = content[:177] + "..."
        if with_time:
            ts = timestamp_to_local_text(item_value(item, "timestamp", "created_at", "time"))
            rows.append(f"[{ts or 'time unknown'}] {display_role}: {content}")
        else:
            rows.append(f"{display_role}: {content}")
    return "\n".join(rows)


def format_classify_context(
    conversation_history,
    current_user_message: str,
    *,
    limit: int = 6,
    format_recent_context_func,
) -> str:
    """Return recent context for classification without duplicating the current user message."""
    if not conversation_history:
        return ""
    current = " ".join(str(current_user_message or "").split())
    try:
        items = list(conversation_history)
    except Exception:
        return ""
    if current:
        for index in range(len(items) - 1, -1, -1):
            role = str(item_value(items[index], "role", "sender") or "unknown")
            content = " ".join(str(item_value(items[index], "content", "message") or "").split())
            if role == "user" and content == current:
                items.pop(index)
                break
    return format_recent_context_func(items, limit=limit, with_time=True)


def message_needs_temporal_guard(message: str) -> bool:
    text = message or ""
    terms = (
        "想你", "想我", "几天", "多久", "没见", "好久", "久别", "上次", "刚才",
        "新会话", "新对话", "多久没", "几天没", "多长时间",
    )
    return any(term in text for term in terms)


def contains_temporal_gap_terms(message: str) -> bool:
    text = message or ""
    terms = ("几天", "多久", "没见", "好久", "久别", "多久没", "几天没", "多长时间")
    return any(term in text for term in terms)
