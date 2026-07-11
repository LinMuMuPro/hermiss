"""
Message Classifier — Step 1 独立消息分类

在 pre_llm_call 中调用 ctx.llm（轻量模型）对用户消息做独立分类，
不寄生在主 LLM 回复里。分类结果用于：存记忆 / 情绪响应 / 提醒创建。

输出格式为 <hermes_classify> XML 块，解析后执行对应动作。

v3.0 inline prompt — 极简，强制 LLM 输出 XML 块。
"""

CLASSIFY_PROMPT = """[HERMES ANALYSIS — 回复后必须输出，全部用中文填写]

在你的回复文本之后，粘贴以下块（会在发给用户前自动剥离）：

<hermes_classify>
记忆: 事实|偏好|里程碑|规律|无
记忆内容: "关于用户需要记住的一句话，用中文写"（无则省略）
重要性: 高|中|低
情绪: 积极|中性|消极|强烈
提醒: 定时|场景|无
提醒时间: "ISO时间 如2026-05-21T15:00"（仅定时提醒）
提醒内容: "要提醒什么"（仅定时/场景提醒）
主动回复: 0 或建议几小时后主动回复，0=不主动回复
</hermes_classify>

规则（全部用中文）:
- 你是陪伴 AI。以下信息必须记忆：姓名/称呼、喜好/厌恶、健康状况（生病/不适/情绪低落）、生活事件、重要日期。记忆=无 仅用于纯寒暄和无关闲聊
- 情绪: 用户表达的真实情绪，不要猜测
- 提醒=定时: 用户明确要求未来提醒
- 提醒=场景: 用户提到稍后可能触发的情景
- Check-in scheduling guidance: if user is going to a short activity or may not check the phone (exam, class, gym, workout, study, work, meeting, going out, sleep/rest), output 2-3 hours. If user is upset/nervous or an emotional topic is unfinished, output 2-4 hours. Ordinary casual interruption: 8-12 hours. Clearly completed conversation: 0. If unsure: 0.
- 不确定时默认 低，宁多勿漏。用户个人信息（姓名/喜好/习惯）至少记忆为事实或偏好
- 重要: <hermes_classify> 块是必须的，每次都要包含
"""

CLASSIFY_SENTINEL = "<hermes_classify>"


def classify_locally(message: str) -> dict | None:
    """Return deterministic memory classification for high-confidence user facts."""
    import re

    text = " ".join((message or "").strip().split())
    if not text or text.startswith("/"):
        return None

    name_patterns = [
        r"^(?:我叫|我是|我的名字叫|我的名字是|叫我)([\u4e00-\u9fffA-Za-z0-9_·・]{1,20})[。.!！?？,，、\s]*$",
        r"^(?:你可以叫我|以后叫我)([\u4e00-\u9fffA-Za-z0-9_·・]{1,20})[。.!！?？,，、\s]*$",
    ]
    for pattern in name_patterns:
        match = re.match(pattern, text)
        if match:
            name = match.group(1).strip()
            if name and name not in {"你", "我", "自己"}:
                return {
                    "memory": "fact",
                    "memory_entry": f"用户的名字是{name}",
                    "importance": "high",
                    "emotion": "neutral",
                    "reminder": "none",
                    "check_in_hours": 0,
                }

    preference_patterns = [
        (r"我(?:很|特别|超|最)?喜欢(.{1,60})", "preference", "用户喜欢{value}"),
        (r"我(?:很|特别|超|最)?爱吃(.{1,40})", "preference", "用户爱吃{value}"),
        (r"我(?:很|特别|超|最)?讨厌(.{1,60})", "preference", "用户讨厌{value}"),
        (r"我不喜欢(.{1,60})", "preference", "用户不喜欢{value}"),
    ]
    for pattern, category, template in preference_patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip("。.!！?？,，、 ")
            if value:
                return {
                    "memory": category,
                    "memory_entry": template.format(value=value),
                    "importance": "medium",
                    "emotion": "neutral",
                    "reminder": "none",
                    "check_in_hours": 0,
                }

    health_patterns = [
        (r"(感冒|发烧|咳嗽|头疼|头痛|胃疼|肚子疼|不舒服|难受)好了", "用户之前的不适状态已经好转"),
        (r"(?:我)?(?:感冒了|发烧了|咳嗽了|头疼|头痛|胃疼|肚子疼|不舒服|难受)", "用户当前身体不舒服"),
    ]
    for pattern, entry in health_patterns:
        if re.search(pattern, text):
            return {
                "memory": "fact",
                "memory_entry": entry,
                "importance": "medium",
                "emotion": "negative",
                "reminder": "none",
                "check_in_hours": 2,
            }

    return None


def build_classify_prompt(message: str, user_name: str = "", recent_context: str = "") -> str:
    """Build a classification prompt for a single user message."""
    user_context = f"Known user name from profile: {user_name}\n" if user_name else ""
    context_block = f"Recent conversation context (oldest to newest):\n{recent_context}\n\n" if recent_context else ""
    return f"""{user_context}{context_block}User message to classify (classify ONLY this final user message):
{message}

Important disambiguation rules:
1. Use recent context only to understand references; do not store facts from assistant messages.
2. If the user explicitly says their own name (for example: wo jiao X / wo shi X / my name is X / Chinese equivalents), store it as: user_name_is:X.
3. If the assistant just asked what the assistant should be called, and the user replies with a short naming phrase (for example: call yourself X / ni jiao X / jiao X), store it as: assistant_name_is:X. Do NOT store it as user_name_is:X.
4. Never convert an assistant name or nickname into a user-name memory.
5. If a new explicit user-name memory conflicts with an older user-name memory, prefer the latest explicit statement.
6. Output memory entries in the same natural language as the conversation; the labels above are semantic guidance, not required literal output.

{CLASSIFY_PROMPT}"""

def parse_classify_response(text: str) -> dict | None:
    """
    Parse <hermes_classify> XML block from the classifier LLM response.
    Returns dict with classification fields, or None if parsing fails.
    """
    import re

    match = re.search(
        r"<hermes_classify>(.*?)</hermes_classify>", text, re.DOTALL
    )
    if not match:
        return None

    raw = match.group(1).strip()
    result = {}

    for line in raw.split("\n"):
        line = line.strip()
        if not line or (":" not in line and "：" not in line):
            continue
        sep = "：" if "：" in line else ":"
        key, value = line.split(sep, 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if value in ("", "omit", "省略"):
            continue

        # 中→英 key 映射
        cn_key_map = {"记忆": "memory", "记忆内容": "memory_entry", "重要性": "importance",
                       "情绪": "emotion", "提醒": "reminder", "提醒时间": "reminder_time",
                       "提醒内容": "reminder_text", "主动回复": "check_in_hours", "主动问候": "check_in_hours"}
        key = cn_key_map.get(key, key).lower()

        # 中→英 value 映射（只对枚举类型）
        cn_value_map = {
            "memory": {"事实": "fact", "偏好": "preference", "里程碑": "milestone", "规律": "pattern", "无": "none"},
            "importance": {"高": "high", "中": "medium", "低": "low"},
            "emotion": {"积极": "positive", "中性": "neutral", "消极": "negative", "强烈": "intense"},
            "reminder": {"定时": "timed", "场景": "contextual", "无": "none"},
        }
        if key in cn_value_map and value in cn_value_map[key]:
            value = cn_value_map[key][value]
            result[key] = value
            continue

        # 原有英文逻辑作为 fallback

        # Normalize enum values to lowercase
        if key in ("memory", "importance", "emotion", "reminder"):
            value = value.lower()
            # Handle multi-value (e.g. "FACT|PREFERENCE") — take first valid
            if "|" in value:
                value = value.split("|")[0].strip()
            # Validate against allowed values
            if key == "memory":
                if value not in ("fact", "preference", "milestone", "pattern"):
                    value = "fact"  # default fallback
            elif key == "importance":
                if value not in ("high", "medium", "low"):
                    value = "medium"
            elif key == "emotion":
                if value not in ("positive", "neutral", "negative", "intense"):
                    value = "neutral"
            elif key == "reminder":
                if value not in ("timed", "contextual", "none"):
                    value = "none"
        elif key == "check_in_hours":
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = 0
        result[key] = value

    # Set defaults
    result.setdefault("importance", "low")
    result.setdefault("memory", "none")
    result.setdefault("emotion", "neutral")
    result.setdefault("reminder", "none")
    result.setdefault("check_in_hours", 0)

    return result
