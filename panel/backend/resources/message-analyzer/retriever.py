import re
"""
Memory Retriever — Step 3 记忆检索与注入

两级检索：
  A. SQLite broad_recall()  — 粗筛 30 条候选（FTS5 + 最近 + 重要）
  B. 注入到 system prompt   — 主 LLM 回复时自然判断相关性

不额外调用 LLM 做精排（省延迟），直接让主 LLM 在回复时筛选。
"""

from .db import MemoryDB


CASUAL_SHORT_MESSAGES = {
    "\u4f60\u5728\u5e72\u561b", "\u5728\u5e72\u561b", "\u5e72\u561b\u5462", "\u4f60\u5e72\u561b\u5462", "\u4f60\u5728\u505a\u4ec0\u4e48", "\u5728\u505a\u4ec0\u4e48",
    "\u65e9", "\u65e9\u5b89", "\u665a\u5b89", "\u7761\u4e86\u5417", "\u9192\u4e86\u5417", "\u55ef", "\u54e6", "\u597d", "\u884c",
}


def _is_casual_short_message(message: str) -> bool:
    text = "".join(str(message or "").split())
    if _is_food_related_message(text):
        return False
    if _is_style_related_message(text):
        return False
    return text in CASUAL_SHORT_MESSAGES or (len(text) <= 8 and not any(ch.isdigit() for ch in text))


def _filter_casual_memories(memories: list[dict]) -> list[dict]:
    keep = []
    for memory in memories:
        entry = str(memory.get("entry") or "")
        category = str(memory.get("category") or "")
        if "assistant_name_is" in entry or "\u7528\u6237\u7684\u540d\u5b57" in entry or "\u4e0d\u559c\u6b22\u603b\u662f\u88ab\u53eb\u540d\u5b57" in entry:
            keep.append(memory)
            continue
        if category in {"milestone", "pattern"}:
            keep.append(memory)
            continue
    return keep[:6]


FOOD_TERMS = ("\u996d", "\u83dc", "\u997f", "\u9910", "\u897f\u7ea2\u67ff", "\u756a\u8304", "\u9e21\u86cb", "\u96ea\u7cd5", "\u98df\u7269", "\u505a\u996d", "\u4e1c\u897f")
STYLE_TERMS = ("\u4e0d\u559c\u6b22", "\u88ab\u53eb", "\u522b\u603b", "\u4e0d\u8981\u603b", "\u53eb\u540d\u5b57", "\u53eb\u6211\u540d\u5b57", "\u5168\u540d", "\u540d\u5b57", "\u79f0\u547c", "\u8bed\u6c14", "\u98ce\u683c")
HEALTH_TERMS = (
    "\u611f\u5192", "\u53d1\u70e7", "\u54b3\u55fd", "\u55d3\u5b50", "\u5589\u5499", "\u5934\u75bc", "\u5934\u75db",
    "\u80c3\u75bc", "\u80c3\u75db", "\u809a\u5b50\u75bc", "\u62c9\u809a\u5b50", "\u8179\u6cfb", "\u751f\u75c5",
    "\u4e0d\u8212\u670d", "\u8fc7\u654f", "\u5931\u7720", "\u71ac\u591c", "\u7ecf\u671f", "\u59e8\u5988",
)


def _is_food_related_message(message: str) -> bool:
    text = str(message or "")
    if any(term in text for term in FOOD_TERMS):
        return True
    return "\u5403" in text and "\u836f" not in text


def _is_style_related_message(message: str) -> bool:
    text = str(message or "")
    return any(term in text for term in STYLE_TERMS) or any(term in text.lower() for term in ("emoji", "ai"))


def _filter_topic_memories(memories: list[dict], message: str) -> list[dict]:
    text = str(message or "")
    food_related = _is_food_related_message(text)
    style_related = _is_style_related_message(text)
    filtered = []
    for memory in memories:
        entry = str(memory.get("entry") or "")
        category = str(memory.get("category") or "")
        is_style = any(term in entry for term in STYLE_TERMS)
        is_food_pref = category == "preference" and any(term in entry for term in FOOD_TERMS)
        is_health = any(term in entry for term in HEALTH_TERMS)
        if food_related:
            if is_food_pref or is_health:
                filtered.append(memory)
            continue
        if style_related:
            if is_style or "\u540d\u5b57" in entry or "\u79f0\u547c" in entry:
                filtered.append(memory)
            continue
        if is_food_pref and not is_style:
            continue
        filtered.append(memory)
    return filtered


def _cap_context_memories(memories: list[dict], max_items: int = 8) -> list[dict]:
    if len(memories) <= max_items:
        return memories
    high = [m for m in memories if m.get("importance") == "high"]
    rest = [m for m in memories if m.get("importance") != "high"]
    capped = []
    seen = set()
    for memory in high + rest:
        memory_id = memory.get("id")
        if memory_id in seen:
            continue
        seen.add(memory_id)
        capped.append(memory)
        if len(capped) >= max_items:
            break
    return capped


def build_memory_context(
    db: MemoryDB,
    message: str,
    *,
    broad_limit: int = 24,
) -> str:
    """
    Query memories, format as system prompt context block.

    Returns empty string if no memories found.
    """
    candidates = db.broad_recall(message, limit=broad_limit)
    candidates = _filter_topic_memories(candidates, message)
    if _is_casual_short_message(message):
        candidates = _filter_casual_memories(candidates)
    candidates = _cap_context_memories(candidates, max_items=8)
    if not candidates:
        return ""

    # Log retrieval (P19)
    try:
        import json, os, sqlite3
        from datetime import datetime, timezone
        cid = os.environ.get("HERMES_PANEL_PORT", "unknown")
        keywords = [t for t in re.findall(r"[\w一-鿿]+", message.lower()) if len(t)>=2][:10]
        entries = [{"id":m.get("id"),"entry":m.get("entry","")[:100],"category":m.get("category",""),"created_at":m.get("created_at","")} for m in candidates[:20]]
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO memory_retrieval_logs (container_id,user_message,keywords,match_count,matched_entries,created_at) VALUES (?,?,?,?,?,?)",
            (str(cid), message[:200], ",".join(keywords[:10]), len(candidates), json.dumps(entries,ensure_ascii=False), now))
        conn.commit(); conn.close()
    except: pass

    return _format_memories(candidates)


def build_full_context(
    db: MemoryDB,
    message: str,
) -> str:
    """
    Build memory context for system prompt injection.
    Returns a single string ready for injection.
    """
    return build_memory_context(db, message)


def _format_memories(memories: list[dict]) -> str:
    """Format memories for system prompt injection."""
    lines = [
        "[HERMES MEMORY - retrieved optional context]",
        "\u4ee5\u4e0b\u662f\u7cfb\u7edf\u6839\u636e\u7528\u6237\u5f53\u524d\u6d88\u606f\u68c0\u7d22\u5230\u7684\u8bb0\u5fc6\uff0c\u53ea\u662f\u53ef\u9009\u53c2\u8003\uff0c\u4e0d\u662f\u5fc5\u987b\u5199\u8fdb\u56de\u590d\u7684\u5185\u5bb9\u3002",
        "\u8bf7\u5148\u5224\u65ad\u5f53\u524d\u804a\u5929\u573a\u666f\u3001\u7528\u6237\u8bed\u6c14\u548c\u4e0a\u4e0b\u6587\uff0c\u518d\u51b3\u5b9a\u662f\u5426\u4f7f\u7528\u5176\u4e2d\u67d0\u4e00\u6761\u3002",
        "\u4e0d\u8981\u4e3a\u4e86\u8868\u73b0\u4f60\u8bb0\u5f97\u800c\u5f3a\u884c\u62fc\u51d1\u6240\u6709\u8bb0\u5fc6\uff1b\u4e0d\u8981\u628a\u65e0\u5173\u504f\u597d\u3001\u65e7\u72b6\u6001\u6216\u8eab\u4efd\u4fe1\u606f\u786c\u585e\u8fdb\u56de\u590d\u3002",
        "\u5982\u679c\u8bb0\u5fc6\u4e0e\u5f53\u524d\u6d88\u606f\u6ca1\u6709\u81ea\u7136\u5173\u7cfb\uff0c\u5c31\u5ffd\u7565\u5b83\uff0c\u50cf\u6b63\u5e38\u804a\u5929\u4e00\u6837\u56de\u590d\u3002",
        "\u5982\u679c\u4f7f\u7528\u8bb0\u5fc6\uff0c\u53ea\u9700\u81ea\u7136\u5730\u878d\u5165\u4e00\u5c0f\u70b9\uff0c\u4e0d\u8981\u590d\u8ff0\u8bb0\u5fc6\u5217\u8868\uff0c\u4e0d\u8981\u8bf4\u2018\u6211\u8bb0\u5f97\u8d44\u6599\u91cc\u5199\u7740\u2019\u3002",
        "\u98ce\u683c\u504f\u597d\u548c\u7528\u6237\u660e\u786e\u4e0d\u559c\u6b22\u7684\u8868\u8fbe\u65b9\u5f0f\u4f18\u5148\u7ea7\u6700\u9ad8\uff1b\u5065\u5eb7/\u5b89\u5168\u72b6\u6001\u53ea\u6709\u5728\u76f8\u5173\u65f6\u6e29\u548c\u63d0\u9192\u3002",
        "",
    ]

    # Group by category for readability
    by_cat: dict[str, list[str]] = {}
    cat_labels = {
        "fact": "Personal Facts",
        "preference": "Preferences & Habits",
        "milestone": "Relationship Moments",
        "pattern": "Behavior Patterns",
    }

    for m in memories:
        cat = m.get("category", "fact")
        if cat not in by_cat:
            by_cat[cat] = []
        tag = "🔴" if m.get("importance") == "high" else ""
        created_at = str(m.get("created_at") or "").strip()
        date_hint = f" (recorded_at: {created_at[:10]})" if created_at else ""
        by_cat[cat].append(f"- {tag} {m['entry']}{date_hint}".strip())

    for cat, label in cat_labels.items():
        entries = by_cat.get(cat, [])
        if entries:
            lines.append(f"### {label}")
            lines.extend(entries)
            lines.append("")

    return "\n".join(lines)

