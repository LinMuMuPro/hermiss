"""
Proactive reply timing policy for message-analyzer.

This module is intentionally pure-ish: it receives the mutable plugin state and
environment helpers from __init__.py, then returns timing decisions without
touching Hermes hooks or cron directly.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def activity_style_hint(text: str) -> tuple[str, int]:
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


def choose_checkin_hours(state: dict, env_int) -> int:
    default_hours = env_int("HERMISS_PROACTIVE_CHECKIN_DEFAULT_HOURS", 3)
    min_hours = env_int("HERMISS_PROACTIVE_CHECKIN_MIN_HOURS", 2)
    max_hours = env_int("HERMISS_PROACTIVE_CHECKIN_MAX_HOURS", 8)
    if min_hours < 1:
        min_hours = 1
    if max_hours < min_hours:
        max_hours = min_hours
    text = "\n".join([
        str(state.get("recent_context") or ""),
        str(state.get("last_activity_hint") or ""),
        str(state.get("last_user_message") or ""),
    ])
    style_hint, suggested = activity_style_hint(text)
    state["checkin_style_hint"] = style_hint
    selected = suggested if suggested else default_hours
    return max(min_hours, min(max_hours, int(selected or default_hours)))


def choose_checkin_minutes(
    state: dict,
    result: dict | None,
    env_int,
    short_state_expected_minutes,
) -> int:
    """Choose the next proactive delay from the dynamic state base."""
    default_minutes = env_int("HERMISS_PROACTIVE_CHECKIN_DEFAULT_MINUTES", 180)
    min_minutes = env_int("HERMISS_PROACTIVE_CHECKIN_MIN_MINUTES", 15)
    max_minutes = env_int("HERMISS_PROACTIVE_CHECKIN_MAX_MINUTES", 480)
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
    style_hint, suggested_hours = activity_style_hint(text)
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
        if result_short_state in {"start", "continue", "开始", "持续"} and result_short_text and result_expected > 0:
            expected = short_state_expected_minutes(result_expected)
            explicit_short_eta = expected <= 30
            selected = max(min_minutes, min(expected + max(5, min(15, expected // 3 or 5)), max_minutes))
            fallback_from_state = True
            unavailable = str(result.get("short_state_unavailable") or "").strip().lower() if isinstance(result, dict) else ""
            state["checkin_fallback_eta"] = True
            state["checkin_fallback_unavailable"] = unavailable in {"yes", "true", "1", "是"}
            state["checkin_style_hint"] = (
                "Explicit short-state ETA fallback: the LLM did not request a normal proactive follow-up, "
                "but it identified a short-term state with an ETA. If the user appears awake from recent context, "
                "ask one light progress/status question after the ETA; do not feel frequent or intrusive."
            )
        elif isinstance(short_state, dict) and str(short_state.get("text") or "").strip():
            expected = short_state_expected_minutes(short_state.get("expected_minutes"))
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
        selected = max(selected * 3, env_int("HERMISS_PROACTIVE_LOW_FREQUENCY_MINUTES", 360))
        style_hint = f"{style_hint} 用户偏好低频主动消息；本次已自动延后。"

    state["checkin_style_hint"] = style_hint
    return max(min_minutes, min(max_minutes, int(selected or default_minutes)))


def next_followup_minutes(stage: int, env_int) -> int:
    chain = [
        env_int("HERMISS_PROACTIVE_FOLLOWUP_1_MINUTES", 120),
        env_int("HERMISS_PROACTIVE_FOLLOWUP_2_MINUTES", 240),
        env_int("HERMISS_PROACTIVE_FOLLOWUP_3_MINUTES", 480),
    ]
    idx = max(0, min(len(chain) - 1, int(stage or 0)))
    return max(30, chain[idx])


def quiet_hour_policy(
    target_dt_utc: datetime,
    style_hint: str,
    source_text: str,
    *,
    env_int,
    tz_name: str | None,
) -> tuple[datetime, str, bool]:
    """Delay generic proactive replies during likely sleep hours."""
    try:
        tz = ZoneInfo(tz_name or "Asia/Shanghai")
    except Exception:
        tz = None
    local_dt = target_dt_utc.astimezone(tz) if tz else target_dt_utc
    start_hour = env_int("HERMISS_PROACTIVE_SLEEP_START_HOUR", 0)
    end_hour = env_int("HERMISS_PROACTIVE_SLEEP_END_HOUR", 8)
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
