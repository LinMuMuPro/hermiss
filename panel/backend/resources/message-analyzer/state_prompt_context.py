"""
State prompt context builders for message-analyzer.

These helpers format already-prepared state snapshots. They do not mutate
plugin state or talk to Hermes directly.
"""

from datetime import datetime, timezone


def build_state_base_context(
    *,
    current_base: dict,
    state_text: str,
    user_message: str,
    clean_state_base_text,
    format_duration_zh,
) -> str:
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
        ("当前状态", "current_state"),
        ("状态摘要", "summary"),
        ("最近情绪", "recent_emotion"),
        ("关系氛围", "relationship_mood"),
        ("回复注意", "caution"),
    ):
        value = clean_state_base_text(current_base.get(key), 180)
        if value:
            lines.append(f"- {label}: {value}")
    state_at_raw = str(current_base.get("state_at") or current_base.get("updated_at") or "")
    if state_at_raw:
        try:
            state_at_dt = datetime.fromisoformat(state_at_raw.replace("Z", "+00:00"))
            if state_at_dt.tzinfo is None:
                state_at_dt = state_at_dt.replace(tzinfo=timezone.utc)
            age = max(0, (datetime.now(timezone.utc) - state_at_dt.astimezone(timezone.utc)).total_seconds())
            lines.append(f"- 状态判断时间: 约{format_duration_zh(age)}前")
        except Exception:
            pass
    lines.extend([
        f"- 当前用户消息: {user_message}",
        "使用规则: 优先回应当前用户消息；如果底座和当前消息冲突，以当前消息为准；只在有帮助时自然带入。",
    ])
    return "\n".join(lines)


def build_state_base_checkin_context(*, snapshot: dict | None, clean_state_base_text) -> str:
    if not isinstance(snapshot, dict) or not snapshot:
        return ""
    lines = [
        "[HERMES STATE BASE - proactive]",
        "这是主动消息可参考的简易状态底座，不是长期记忆；只在有帮助时自然使用，不要复述为清单。",
    ]
    for label, key in (
        ("当前状态", "current_state"),
        ("状态摘要", "summary"),
        ("状态判断时间", "state_at"),
        ("最近情绪", "recent_emotion"),
        ("关系氛围", "relationship_mood"),
        ("回复注意", "caution"),
    ):
        value = clean_state_base_text(snapshot.get(key), 180)
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def build_short_term_state_checkin_context(
    *,
    snapshot: dict | None,
    trigger_dt,
    short_state_expected_minutes,
    format_duration_zh,
) -> str:
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
    expected_minutes = short_state_expected_minutes(snapshot.get("expected_minutes"))
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
        f"到主动消息触发时预计已过去: {format_duration_zh(age_seconds)}；原预计持续: {expected_minutes}分钟。",
        f"该状态通常是否不方便看手机: {'是' if unavailable else '否'}。",
        f"状态判断: {status_hint}",
        "这不是长期记忆，不能断言用户现在一定在做这件事；只能用于推断主动消息是否该问进度、结果、累不累，还是避免打扰。",
        "如果触发时间是深夜或清晨，仍然优先遵守安静时段规则，不要提问。",
    ])
