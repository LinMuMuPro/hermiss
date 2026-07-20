"""
Temporal context builders for message-analyzer.

Keep this module side-effect free so the plugin entry file only orchestrates
state and Hermes hooks.
"""


def build_temporal_guard_context(
    *,
    conversation_history,
    current_local_dt,
    is_first_turn: bool,
    session_id: str = "",
    item_value,
    timestamp_to_local_dt,
    local_time_text_for,
    format_duration_zh,
    is_usable_chat_content,
    contains_temporal_gap_terms,
    find_global_last_chat_message,
) -> str:
    last_dt = None
    last_role = ""
    last_content = ""
    try:
        items = list(conversation_history or [])
    except Exception:
        items = []
    for item in reversed(items):
        role = str(item_value(item, "role", "sender") or "unknown")
        if role == "tool":
            continue
        content = " ".join(str(item_value(item, "content", "message") or "").split())
        if not is_usable_chat_content(content):
            continue
        dt = timestamp_to_local_dt(item_value(item, "timestamp", "created_at", "time"))
        if dt:
            last_dt = dt
            last_role = role
            last_content = content[:80]
            break

    lines = [
        "[HERMES TEMPORAL CONTEXT - anti-hallucination]",
        f"当前本地时间: {local_time_text_for(current_local_dt)}.",
        "不要根据“新会话/新对话”推断用户和你很久没聊；新会话只表示上下文重置，不表示现实时间过去很多天。",
        "禁止无时间证据时说“几天没见”“好久不见”“两天没聊”“这么久没见”等时长判断。",
        "如果用户说“想你”，只回应情绪本身，不要自行编造分离时长。",
    ]
    if last_dt:
        diff = max(0, (current_local_dt - last_dt).total_seconds())
        lines.append(
            f"当前可见聊天里上一条消息是 {last_role} 在 {local_time_text_for(last_dt)} 发出的，距离现在约 {format_duration_zh(diff)}。"
        )
        if diff < 86400:
            lines.append("可见证据显示间隔不到一天，所以不能说几天没见或两天没见。")
        lines.append(f"上一条可见消息摘录: {last_content}")
    elif is_first_turn:
        global_last = find_global_last_chat_message(session_id=str(session_id or ""))
        if global_last:
            global_dt = global_last["timestamp"]
            diff = max(0, (current_local_dt - global_dt).total_seconds())
            lines.append(
                f"当前会话没有可见历史，但全局最近用户消息在 {local_time_text_for(global_dt)}，距离现在约 {format_duration_zh(diff)}。"
            )
            if diff < 86400:
                lines.append("全局真实聊天间隔不到一天，所以不能说几天没见、两天没聊、好久不见，也不要反问用户“几天了”。")
            if contains_temporal_gap_terms(str(global_last["content"])):
                lines.append("全局上一条用户消息涉及询问聊天间隔；不要沿用其中的时间猜测，也不要反问具体几天。")
            else:
                lines.append(f"全局上一条用户消息摘录: {global_last['content']}")
        else:
            lines.append("当前可见聊天历史没有上一条消息；这不能证明现实中已经隔了几天。不要主动谈论分离时长，也不要反问“几天了”。")
    return "\n".join(lines)
