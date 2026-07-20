"""State-base helper functions for message-analyzer."""


def clean_state_base_text(value, limit: int = 160) -> str:
    text = " ".join(str(value or "").strip().split())
    if text in {"", "无", "none", "None", "null", "省略"}:
        return ""
    return text[:limit]


def compact_activity_text(content: str, limit: int = 160) -> str:
    text = " ".join(str(content or "").split())
    if not text:
        return ""
    if "The user sent an image" in text or "image_url:" in text or "Here's what I can see" in text:
        lower = text.lower()
        details = []
        for word in ("螺蛳粉", "外卖", "takeout", "soup", "shrimp", "noodles", "泡椒凤爪", "荔枝", "meal"):
            if word.lower() in lower and word not in details:
                details.append(word)
        if details:
            return f"用户发了一张图片，内容和{('、'.join(details[:5]))}有关。"
        return "用户发了一张图片。"
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def format_duration_zh(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分钟"
    if minutes:
        return f"{minutes}分钟"
    return "不到1分钟"


def short_state_expected_minutes(value) -> int:
    try:
        minutes = int(value or 0)
    except Exception:
        minutes = 0
    if minutes <= 0:
        return 90
    return max(5, min(minutes, 480))
