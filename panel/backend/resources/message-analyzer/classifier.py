"""
Message Classifier — Step 1 独立消息分类

在 pre_llm_call 中调用 ctx.llm（轻量模型）对用户消息做独立分类，
不寄生在主 LLM 回复里。分类结果用于：存记忆 / 情绪响应 / 提醒创建。

输出格式为 <hermes_classify> XML 块，解析后执行对应动作。

v3.0 inline prompt — 极简，强制 LLM 输出 XML 块。
"""

CLASSIFY_PROMPT = """[HERMES ANALYSIS — 只做分类，不要回复用户]

你必须只输出下面这个 <hermes_classify> 块，不要输出解释、寒暄或用户可见回复。
所有字段都必须出现；没有内容也要写“无”或 0，尤其不要省略“主动回复/主动回复分钟/主动回复频率/短期状态/状态内容/状态预计分钟/状态不便看手机/状态底座/关系氛围/回复注意”。

<hermes_classify>
记忆: 事实|偏好|里程碑|规律|无
记忆内容: "关于用户需要记住的一句话，用中文写"（无则写 无）
记忆列表:
- 偏好|中|用户很少去电影院
- 事实|低|用户正在看《怪奇物语》第二季
重要性: 高|中|低
情绪: 积极|中性|消极|强烈
提醒: 定时|场景|无
提醒时间: "ISO时间 如2026-05-21T15:00"（无则写 无）
提醒内容: "要提醒什么"（无则写 无）
主动回复: 0 或建议几小时后主动回复，0=不主动回复
主动回复分钟: 0 或建议几分钟后主动回复，0=不主动回复；如果能判断具体状态，优先填分钟
主动回复频率: 正常|降低|关闭
短期状态: 开始|持续|结束|无
状态内容: "用户当前/即将进行的短期状态，用简短中文概括即可"（无则写 无）
状态预计分钟: 数字，无法判断则 0
状态不便看手机: 是|否
状态底座: "一句话概括当前用户状态/正在做的事/最近上下文，只写确定信息，无则写 无"
关系氛围: "一句话概括当前关系氛围或用户对回复的感受，如轻松、暧昧、认真质疑、不舒服，无则写 无"
回复注意: "下一轮回复最应该注意的一句话，如不要贬低用户选择、不要强行调侃、不要问在干嘛，无则写 无"
</hermes_classify>

规则（全部用中文）:
- 你是陪伴 AI。以下信息必须记忆：姓名/称呼、喜好/厌恶、健康状况（生病/不适/情绪低落）、生活事件、重要日期。记忆=无 仅用于纯寒暄和无关闲聊
- 轻微但长期有用的信息也要记：用户很少/经常做什么、正在追的剧/游戏/书、看到第几季/第几章、对某类活动的习惯、聊天里的昵称/关系梗、用户对你们关系的比喻
- 如果同一句话里有多条可记信息，使用“记忆列表”逐条输出；每条格式固定为：- 类别|重要性|内容
- 最近上下文里的“你”表示助手/角色自己说的话，不是用户说的话。不要把“你”的调侃、评价、比喻、建议或口癖存成用户事实/偏好。
- 长期记忆必须来自用户明确表达、用户主动确认，或多次稳定行为；不能根据“你”的回复倒推出用户偏好。例：你说“螺蛳粉这种东西，仪式感到了才香”，不能记成“用户注重仪式感”。
- 用户只说“点了/买了/在吃/等某个食物”，最多可作为短期状态；只有用户明确说喜欢、好吃、美味、经常吃等，才可低重要性记为偏好。
- 情绪: 用户表达的真实情绪，不要猜测
- 单次“想你/想你了/晚安/困了/饿了”等即时情绪或短暂状态，通常不要写入长期记忆；除非用户明确表达稳定偏好、习惯或要求你以后都这样回应
- 提醒=定时: 用户明确要求未来提醒
- 提醒=场景: 用户提到稍后可能触发的情景
- 短期状态用于下一轮对话连续性，不是长期记忆。它必须独立于“记忆”判断：即使记忆=无，也仍然要判断短期状态
- 用户表达接下来要做、正在做、准备做、刚进入某种状态时，输出短期状态=开始或持续。例：我要去洗澡了、一会考试、准备休息。除此之外不要依赖固定场景词表，由上下文自行判断。
- 用户表达活动结束、返回、完成、放弃、醒来时，输出短期状态=结束。例：回来了、做完了、睡醒了。其他表达由上下文判断。
- 用户只是表达情绪、寒暄、普通问答，没有可延续活动或明确状态，才输出短期状态=无。例：想你了、哈哈、你是谁。
- 短期状态内容要概括成“用户准备/正在……”，不要写成长篇分析；状态预计分钟由上下文估计，不确定填 60-90。
- 状态底座是“当前对话的简易记忆底座”，不是长期记忆。它只记录最近一两轮对当前回复有用的状态、情绪、关系氛围和禁忌，必须短、准、克制。
- 如果用户指出你的回复不舒服、不合理、被冒犯、被贬低，关系氛围要记录“用户对刚才回复不舒服/认真质疑”，回复注意要写清楚下一轮避免点。
- 不要在状态底座里写未经确认的猜测，不要把单句“想你”升格成长期事实。
- 主动回复调度由你根据上下文决定，不要机械套固定时长。优先填写“主动回复分钟”。
- 如果用户明确说“还有X分钟/几点到/一会儿就到/马上开始/正在吃/刚吃上/准备睡/要考试”等，主动回复分钟要贴合这个状态：可以用“状态预计分钟 + 合理缓冲”，但不要拖到明显不自然的很久以后。
- 如果用户正在吃饭/等外卖/刚收到外卖，判断应该何时轻轻关心“吃到了没/味道怎么样/吃完没”，由当前上下文决定；不要默认固定 25 或 45 分钟。
- 如果用户说“别老问/太频繁/别主动找/安静点/不用回访/别打扰/烦”等，主动回复频率=降低或关闭；明显拒绝主动消息时填 关闭，主动回复分钟=0。
- 普通闲聊如果没有必要主动回访，主动回复分钟=0。情绪未收束、状态有后续、或用户可能期待陪伴时才设置。
- 主动回复仍可兼容旧字段“主动回复”：如果你只能粗略判断小时，再填主动回复小时；能判断分钟时，主动回复填对应小时的近似值或 0 都可以。
- 不确定时不要写长期记忆；宁可少记，也不要把场景、玩笑、助手话术、一次性状态扩写成用户偏好。明确的用户个人信息（姓名/稳定称呼/明确喜好/习惯）才至少记为事实或偏好。
- 重要: <hermes_classify> 块是必须的，每次都要包含
"""

CLASSIFY_SENTINEL = "<hermes_classify>"


NAME_REJECT_TERMS = (
    "问", "几天", "多久", "没见", "好久", "我俩", "我们", "你", "吗", "嘛", "呢",
    "什么", "谁", "哪个", "哪位", "哪里", "怎么", "为什么", "是不是", "有没有",
)


def _looks_like_user_name_candidate(name: str) -> bool:
    value = " ".join((name or "").strip().split())
    if not value:
        return False
    if value in {"你", "我", "自己", "谁", "什么", "什么角色", "哪个", "哪位"}:
        return False
    if any(term in value for term in NAME_REJECT_TERMS):
        return False
    if value.endswith(("？", "?", "吗", "嘛", "呢")):
        return False
    if len(value) > 12:
        return False
    return True


def _extract_user_name_from_memory(entry: str) -> str | None:
    import re

    text = " ".join((entry or "").strip().split())
    patterns = (
        r"^用户(?:的)?(?:名字|姓名|名称|称呼)(?:是|叫)\s*([^：；，。！？;,.!?:\s]+)",
        r"^用户叫\s*([^：；，。！？;,.!?:\s]+)",
        r"^user[_ ]?name[_ ]?is[:：]?\s*([^：；，。！？;,.!?:\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _is_invalid_user_name_memory(entry: str) -> bool:
    name = _extract_user_name_from_memory(entry)
    return bool(name and not _looks_like_user_name_candidate(name))


def classify_locally(message: str) -> dict | None:
    """Return deterministic memory classification for high-confidence user facts."""
    return None
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
            if _looks_like_user_name_candidate(name):
                return _classification_with_memories([
                    ("fact", "high", f"用户的名字是{name}"),
                ])

    local_memories: list[tuple[str, str, str]] = []
    emotion = "neutral"
    check_in_hours = 0

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
            value = re.split(r"[，,。.!！?？]\s*(?:也|但|不过|然后|还|而且)", value, maxsplit=1)[0].strip()
            if value:
                local_memories.append((category, "medium", template.format(value=value)))

    habit_patterns = [
        (r"(?:我)?(?:很|挺|比较|还)?(?:少|少去|很少|挺少|不常|不怎么|基本不|几乎不)(?:去)?(电影院|影院)", "preference", "用户很少去电影院"),
        (r"(?:我)?(?:很|挺|比较)?(?:少|很少|不常|不怎么|基本不|几乎不)(?:出门|出去玩)", "pattern", "用户不常出门"),
        (r"(?:我)?(?:经常|常常|总是|一般|通常)(?:在家|窝在家|家里)(?:看剧|看电影|刷剧)", "pattern", "用户经常在家看剧或看电影"),
        (r"(?:我)?(?:经常|常常|总是|一般|通常)(?:去)?(电影院|影院)", "pattern", "用户经常去电影院"),
    ]
    for pattern, category, entry in habit_patterns:
        if re.search(pattern, text):
            local_memories.append((category, "low", entry))

    watching_patterns = [
        r"(?:我)?(?:最近|这几天|现在|目前)?(?:在)?(?:看|追|刷)(?:剧)?[《「]?([\u4e00-\u9fffA-Za-z0-9 _·・:-]{2,40}?)[》」]?(?:第([一二三四五六七八九十0-9]+)[季部集章])?[。.!！?？,，、\s]*$",
        r"(?:我)?看到第([一二三四五六七八九十0-9]+)[季部集章]了",
    ]
    match = re.search(watching_patterns[0], text)
    if match:
        title = match.group(1).strip(" 。.!！?？,，、")
        season = match.group(2)
        if title and title not in {"什么剧", "什么电影"}:
            entry = f"用户正在看《{title}》"
            if season:
                entry += f"第{season}季"
            local_memories.append(("fact", "low", entry))

    role_memories = _extract_roleplay_memories(text)
    if role_memories:
        local_memories.extend(("milestone", "low", item) for item in role_memories)
        emotion = "positive"

    health_patterns = [
        (r"(感冒|发烧|咳嗽|头疼|头痛|胃疼|肚子疼|不舒服|难受)好了", "用户之前的不适状态已经好转"),
        (r"(?:我)?(?:感冒了|发烧了|咳嗽了|头疼|头痛|胃疼|肚子疼|不舒服|难受)", "用户当前身体不舒服"),
    ]
    for pattern, entry in health_patterns:
        if re.search(pattern, text):
            local_memories.append(("fact", "medium", entry))
            emotion = "negative"
            check_in_hours = max(check_in_hours, 2)

    if local_memories:
        return _classification_with_memories(local_memories, emotion=emotion, check_in_hours=check_in_hours)

    return None


def _clean_role_name(value: str) -> str:
    import re

    role = (value or "").strip()
    role = re.sub(r"^(?:那个|这个|一?个)\s*", "", role)
    role = role.strip(" 。.!！?？,，、；;：:（）()[]【】\"'“”‘’")
    role = re.sub(r"(?:咯|吗|吧|呢|啦|啊|呀)$", "", role).strip()
    if not role:
        return ""
    if role in {"你", "我", "自己", "谁", "什么", "哪个", "角色", "人物", "男的", "女的"}:
        return ""
    if any(word in role for word in ("什么", "哪个", "哪位", "谁")):
        return ""
    if any(word in role for word in NAME_REJECT_TERMS):
        return ""
    if len(role) > 24:
        return ""
    return role


def _extract_roleplay_memories(text: str) -> list[str]:
    """Extract generic relationship/role analogy memories from short user utterances."""
    import re

    source = text or ""
    memories = []

    pair_patterns = [
        r"你(?:就是|是|当|做|像|扮演)([^，,。.!！?？；;]{1,24}).{0,16}?我(?:就是|是|当|做|像|扮演)([^，,。.!！?？；;]{1,24})",
        r"我(?:就是|是|当|做|像|扮演)([^，,。.!！?？；;]{1,24}).{0,16}?你(?:就是|是|当|做|像|扮演)([^，,。.!！?？；;]{1,24})",
    ]
    for index, pattern in enumerate(pair_patterns):
        match = re.search(pattern, source, re.IGNORECASE)
        if not match:
            continue
        first = _clean_role_name(match.group(1))
        second = _clean_role_name(match.group(2))
        if not first or not second:
            continue
        if index == 0:
            memories.append(f"用户把助手比作{first}，把自己比作{second}")
        else:
            memories.append(f"用户把自己比作{first}，把助手比作{second}")

    self_patterns = [
        r"^(?:那|所以|这么说|这样的话)?我(?:就是|是|当|做|像|扮演)([^，,。.!！?？；;]{1,24})[。.!！?？,，、\s]*$",
        r"^(?:那|所以|这么说|这样的话)?我是([^，,。.!！?？；;]{1,24})[。.!！?？,，、\s]*$",
    ]
    for pattern in self_patterns:
        match = re.search(pattern, source, re.IGNORECASE)
        if not match:
            continue
        role = _clean_role_name(match.group(1))
        if role:
            memories.append(f"用户把自己比作{role}")

    nickname_patterns = [
        r"^(?:那|所以|以后)?(?:叫我|喊我|称呼我)([^，,。.!！?？；;]{1,24})[。.!！?？,，、\s]*$",
    ]
    for pattern in nickname_patterns:
        match = re.search(pattern, source, re.IGNORECASE)
        if not match:
            continue
        role = _clean_role_name(match.group(1))
        if role:
            memories.append(f"用户希望被称作{role}")

    deduped = []
    for item in memories:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _classification_with_memories(
    memories: list[tuple[str, str, str]],
    *,
    emotion: str = "neutral",
    check_in_hours: int = 0,
) -> dict:
    items = [
        {"memory": category, "importance": importance, "memory_entry": entry}
        for category, importance, entry in memories
        if entry
    ]
    first = items[0] if items else {"memory": "none", "importance": "low", "memory_entry": ""}
    return {
        "memory": first["memory"],
        "memory_entry": first["memory_entry"],
        "importance": first["importance"],
        "memories": items,
        "emotion": emotion,
        "reminder": "none",
        "check_in_hours": check_in_hours,
    }


def build_classify_prompt(message: str, user_name: str = "", recent_context: str = "") -> str:
    """Build a classification prompt for a single user message."""
    user_context = f"Known user name from profile: {user_name}\n" if user_name else ""
    context_block = f"Recent conversation context (oldest to newest):\n{recent_context}\n\n" if recent_context else ""
    return f"""{user_context}{context_block}User message to classify (classify ONLY this final user message):
{message}

Important disambiguation rules:
1. Use recent context only to understand references; in recent context, "你" means the assistant/role. Do not store facts, preferences, jokes, metaphors, or style from "你"/assistant messages as user memory.
2. If the user explicitly says their own name (for example: wo jiao X / wo shi X / my name is X / Chinese equivalents), store it as: user_name_is:X.
3. If the assistant just asked what the assistant should be called, and the user replies with a short naming phrase (for example: call yourself X / ni jiao X / jiao X), store it as: assistant_name_is:X. Do NOT store it as user_name_is:X.
4. Never convert an assistant name or nickname into a user-name memory, and never convert the assistant's wording into a user preference.
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
    memories = []

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
        cn_key_map = {"记忆": "memory", "记忆内容": "memory_entry", "记忆列表": "memory_list", "记忆条目": "memory_list",
                       "重要性": "importance",
                       "情绪": "emotion", "提醒": "reminder", "提醒时间": "reminder_time",
                       "提醒内容": "reminder_text", "主动回复": "check_in_hours", "主动问候": "check_in_hours",
                       "主动回复分钟": "check_in_minutes", "主动问候分钟": "check_in_minutes",
                       "主动回复频率": "check_in_frequency", "主动问候频率": "check_in_frequency",
                       "短期状态": "short_state", "状态内容": "short_state_text",
                       "状态预计分钟": "short_state_minutes", "状态不便看手机": "short_state_unavailable",
                       "状态底座": "state_base_summary", "关系氛围": "state_base_mood",
                       "回复注意": "state_base_caution"}
        key = cn_key_map.get(key, key).lower()

        if key == "memory_list":
            parsed_items = _parse_memory_items(value)
            if parsed_items:
                memories.extend(parsed_items)
            continue

        # 中→英 value 映射（只对枚举类型）
        cn_value_map = {
            "memory": {"事实": "fact", "偏好": "preference", "里程碑": "milestone", "规律": "pattern", "无": "none"},
            "importance": {"高": "high", "中": "medium", "低": "low"},
            "emotion": {"积极": "positive", "中性": "neutral", "消极": "negative", "强烈": "intense"},
            "reminder": {"定时": "timed", "场景": "contextual", "无": "none"},
            "short_state": {"开始": "start", "持续": "continue", "结束": "end", "无": "none"},
            "short_state_unavailable": {"是": "yes", "否": "no"},
        }
        if key in cn_value_map and value in cn_value_map[key]:
            value = cn_value_map[key][value]
            result[key] = value
            continue

        # 原有英文逻辑作为 fallback

        # Normalize enum values to lowercase
        if key in ("memory", "importance", "emotion", "reminder", "short_state", "short_state_unavailable"):
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
            elif key == "short_state":
                if value not in ("start", "continue", "end", "none"):
                    value = "none"
            elif key == "short_state_unavailable":
                if value not in ("yes", "no"):
                    value = "no"
        elif key == "check_in_hours":
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = 0
        elif key == "check_in_minutes":
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = 0
        elif key == "short_state_minutes":
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = 0
        result[key] = value

    for item in _parse_memory_items(raw):
        if item not in memories:
            memories.append(item)

    if memories:
        memories = [
            item for item in memories
            if not _is_invalid_user_name_memory(str(item.get("memory_entry") or ""))
        ]
        result["memories"] = memories
        if memories:
            first = memories[0]
            result["memory"] = first["memory"]
            result["memory_entry"] = first["memory_entry"]
            result["importance"] = first["importance"]

    if _is_invalid_user_name_memory(str(result.get("memory_entry") or "")):
        result["memory"] = "none"
        result["memory_entry"] = ""
        result["importance"] = "low"
        result.pop("memories", None)

    # Set defaults
    result.setdefault("importance", "low")
    result.setdefault("memory", "none")
    result.setdefault("emotion", "neutral")
    result.setdefault("reminder", "none")
    result.setdefault("check_in_hours", 0)
    result.setdefault("check_in_minutes", 0)
    result.setdefault("check_in_frequency", "normal")
    result.setdefault("short_state", "none")
    result.setdefault("short_state_text", "")
    result.setdefault("short_state_minutes", 0)
    result.setdefault("short_state_unavailable", "no")
    result.setdefault("state_base_summary", "")
    result.setdefault("state_base_mood", "")
    result.setdefault("state_base_caution", "")

    return result


def _parse_memory_items(raw: str) -> list[dict]:
    """Parse multi-memory lines like '- 偏好|中|用户很少去电影院'."""
    import re

    if not raw:
        return []

    memory_map = {"事实": "fact", "偏好": "preference", "里程碑": "milestone", "规律": "pattern"}
    importance_map = {"高": "high", "中": "medium", "低": "low"}
    valid_memory = {"fact", "preference", "milestone", "pattern"}
    valid_importance = {"high", "medium", "low"}
    items = []

    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        text = re.sub(r"^(?:[-*•]|\d+[.)、])\s*", "", text)
        parts = [p.strip().strip('"').strip("'") for p in re.split(r"[|｜]", text, maxsplit=2)]
        if len(parts) != 3:
            continue
        memory_type = memory_map.get(parts[0], parts[0].lower())
        importance = importance_map.get(parts[1], parts[1].lower())
        entry = parts[2].strip()
        if memory_type not in valid_memory or importance not in valid_importance or not entry:
            continue
        items.append({
            "memory": memory_type,
            "importance": importance,
            "memory_entry": entry,
        })

    return items
