"""Persona and output-style context helpers."""


def read_profile_markdown(path, limit: int = 6000, logger=print) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n...(truncated)"
        return text
    except Exception as exc:
        logger(f"[message-analyzer] read profile file failed: {path}: {exc}")
        return ""


def persona_forbids_plain_emoji(soul_text: str) -> bool:
    if not soul_text:
        return False
    lower_text = soul_text.lower()
    emoji_terms = ("emoji", "表情、emoji", "表情 emoji", "颜文字")
    allow_terms = ("允许使用emoji", "可以使用emoji", "允许使用 emoji", "可以使用 emoji")
    forbid_terms = ("禁止", "严禁", "不准", "不要", "不能", "不得")
    if any(term in lower_text for term in allow_terms):
        return False
    if not any(term in lower_text for term in emoji_terms):
        return False
    for term in emoji_terms:
        index = lower_text.find(term)
        if index < 0:
            continue
        window = lower_text[max(0, index - 30): index + 30]
        if any(forbid in window for forbid in forbid_terms):
            return True
    return False


def build_output_style_guard_context(soul_text: str) -> str:
    if not persona_forbids_plain_emoji(soul_text):
        return ""
    return (
        "[HERMES OUTPUT STYLE GUARD - FOLLOW CURRENT SOUL.md]\n"
        "当前 SOUL.md 明确禁止普通 Emoji / 颜文字。本轮最终回复不得包含任何 Unicode Emoji 或颜文字，"
        "也不要用 Emoji 来表达暧昧、调侃、开心或安慰。\n"
        "如果需要表达语气，只使用自然中文、标点和措辞。"
    )


def build_persona_context(*, soul_text: str, user_text: str) -> str:
    parts = []
    if soul_text:
        parts.append(f"SOUL.md persona:\n{soul_text}")
    if user_text:
        parts.append(f"USER.md user profile:\n{user_text}")
    return "\n\n".join(parts)
