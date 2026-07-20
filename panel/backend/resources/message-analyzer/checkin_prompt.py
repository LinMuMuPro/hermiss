"""Prompt builders for proactive check-in jobs."""


def build_checkin_prompt(
    *,
    checkin_file,
    checkin_id: str,
    soul_path,
    user_path,
    local_time: str,
    trigger_local_time: str,
    last_user_message_at: str,
    context_age_hint: str,
    transcript_block: str,
    last_activity_block: str,
    state_base_block: str,
    short_state_block: str,
    style_hint: str,
    persona_context: str,
    memory_context: str,
) -> str:
    prompt = (
        f"[HERMES PROACTIVE REPLY]\n\n"
        f"Read the active_checkin.json file at {checkin_file}. If cancelled=true, "
        f"or if checkin_id is not exactly {checkin_id}, return exactly [SILENT] and nothing else.\n\n"
        f"Before writing, read and obey the current persona and user profile files if available: "
        f"{soul_path} and {user_path}. "
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
    return prompt


def build_stage_prompt(
    *,
    base_prompt: str,
    stage: int,
    stage_trigger_local_time: str,
    stage_age_hint: str,
    original_trigger_local_time: str,
    original_age_hint: str,
) -> str:
    stage_text = base_prompt.replace(
        f"Actual local trigger time for this message: {original_trigger_local_time}.",
        f"Actual local trigger time for this message: {stage_trigger_local_time}.",
    ).replace(
        f"The latest user context is {original_age_hint} old by design.",
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
