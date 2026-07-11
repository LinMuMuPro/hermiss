"""
Sticker Sender Plugin — Hermiss 表情包系统

低 token 设计：
  - pre_llm_call 只注入很短的 intent 协议，不暴露完整表情包库。
  - transform_llm_output 解析 <sticker intent="hug"/> / [sticker:hug]。
  - 插件本地按 stickers.json 白名单选择文件，输出 MEDIA:/path 交给 Hermes 平台发送。

安全原则：
  - LLM 只能输出 intent，不能指定任意文件路径。
  - 每轮最多发送 1 张。
  - 内置冷却时间，避免刷屏。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any


PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_DIR / "stickers.json"
STATE_PATH = PLUGIN_DIR / "sticker_state.json"
CALL_LOG_PATH = PLUGIN_DIR / "sticker_calls.jsonl"
ASSET_DIR = PLUGIN_DIR / "assets" / "stickers"

DEFAULT_INTENTS = ("hug", "comfort", "happy", "shy", "angry_cute", "goodnight", "food", "miss_you")
DEFAULT_COOLDOWN_SECONDS = 600
DEFAULT_MAX_PER_TURN = 1

STICKER_XML_RE = re.compile(
    r"<sticker\s+intent=[\"'](?P<intent>[a-zA-Z0-9_-]+)[\"']\s*/?>",
    re.IGNORECASE,
)
STICKER_BRACKET_RE = re.compile(
    r"\[sticker\s*:\s*(?P<intent>[a-zA-Z0-9_-]+)\]",
    re.IGNORECASE,
)

INTENT_KEYWORDS = (
    ("happy", ("开心", "高兴", "快乐", "笑", "开心的", "happy")),
    ("comfort", ("安慰", "抱抱我", "难过", "委屈", "comfort")),
    ("hug", ("抱抱", "抱一个", "hug")),
    ("shy", ("害羞", "羞", "shy")),
    ("angry_cute", ("生气", "气鼓鼓", "凶", "angry")),
    ("goodnight", ("晚安", "睡觉", "goodnight")),
    ("food", ("吃", "饿", "饭", "food")),
    ("miss_you", ("想你", "思念", "miss")),
)

INTENT_DESCRIPTIONS = {
    "happy": "开心、高兴、轻松、庆祝、调皮地回应好消息",
    "comfort": "安慰、难过、委屈、低落、需要被陪着",
    "hug": "拥抱、亲近、撒娇、想靠近一点",
    "shy": "害羞、暧昧、被夸、轻微脸红",
    "angry_cute": "可爱生气、假装凶、轻微吃醋、闹小脾气",
    "goodnight": "晚安、睡前、困了、结束一天",
    "food": "吃饭、馋了、点餐、分享食物",
    "miss_you": "想念、分别、很久没聊、表达牵挂",
}


def _now() -> float:
    return time.time()


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_config() -> dict:
    cfg = _load_json(CONFIG_PATH, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("enabled", True)
    cfg.setdefault("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
    cfg.setdefault("max_per_turn", DEFAULT_MAX_PER_TURN)
    cfg.setdefault("inject_only_for_platforms", ["weixin"])
    cfg.setdefault("intents", {name: [] for name in DEFAULT_INTENTS})
    return cfg


def _load_state() -> dict:
    state = _load_json(STATE_PATH, {})
    return state if isinstance(state, dict) else {}


def _save_state(state: dict) -> None:
    _write_json(STATE_PATH, state)


def _append_call_log(event: dict) -> None:
    try:
        CALL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if CALL_LOG_PATH.exists() and CALL_LOG_PATH.stat().st_size > 2 * 1024 * 1024:
            CALL_LOG_PATH.replace(CALL_LOG_PATH.with_suffix(".jsonl.1"))
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            **event,
        }
        with CALL_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _platform_allowed(platform: str, cfg: dict) -> bool:
    allowed = cfg.get("inject_only_for_platforms") or []
    if not allowed:
        return True
    # Some Hermes gateway hook versions do not pass the platform name to
    # plugin callbacks. Do not block in that case, otherwise explicit sticker
    # requests lose the local MEDIA flow and the model may try a tool/permission
    # path instead.
    if not platform:
        return True
    return str(platform or "").lower() in {str(x).lower() for x in allowed}


def _available_intents(cfg: dict) -> list[str]:
    intents = cfg.get("intents") or {}
    if not isinstance(intents, dict):
        return []
    result = []
    for intent, items in intents.items():
        if not isinstance(intent, str):
            continue
        if isinstance(items, list) and any(
            _normalize_item_path(item if isinstance(item, str) else str((item or {}).get("path") or ""))
            for item in items
        ):
            result.append(intent)
    return sorted(result)


def _normalize_item_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (PLUGIN_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(ASSET_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


def _choose_sticker(intent: str, cfg: dict) -> Path | None:
    intents = cfg.get("intents") or {}
    items = intents.get(intent)
    if not isinstance(items, list) or not items:
        return None

    candidates: list[Path] = []
    weighted: list[tuple[Path, int]] = []
    for item in items:
        if isinstance(item, str):
            path = _normalize_item_path(item)
            if path:
                candidates.append(path)
        elif isinstance(item, dict):
            path = _normalize_item_path(str(item.get("path") or ""))
            if path:
                weight = int(item.get("weight") or 1)
                weighted.append((path, max(1, min(weight, 20))))

    if weighted:
        pool: list[Path] = []
        for path, weight in weighted:
            pool.extend([path] * weight)
        return random.choice(pool) if pool else None
    return random.choice(candidates) if candidates else None


def _extract_requested_intent(text: str) -> tuple[str | None, str]:
    if not text:
        return None, text

    intent = None

    def _xml_replace(match: re.Match) -> str:
        nonlocal intent
        if intent is None:
            intent = match.group("intent")
        return ""

    cleaned = STICKER_XML_RE.sub(_xml_replace, text, count=1)

    def _bracket_replace(match: re.Match) -> str:
        nonlocal intent
        if intent is None:
            intent = match.group("intent")
        return ""

    cleaned = STICKER_BRACKET_RE.sub(_bracket_replace, cleaned, count=1)

    # Remove extra accidental sticker tags after the first one.
    cleaned = STICKER_XML_RE.sub("", cleaned)
    cleaned = STICKER_BRACKET_RE.sub("", cleaned)
    return intent, cleaned.strip()


def _infer_requested_intent(message: str, cfg: dict) -> str | None:
    text = (message or "").strip().lower()
    if not text:
        return None
    explicitly_asks_sticker = any(keyword in text for keyword in ("表情包", "贴纸", "发表情", "发个表情", "sticker"))
    explicitly_asks_sticker = explicitly_asks_sticker or ("表情" in text and "发" in text)
    if not explicitly_asks_sticker:
        return None
    available = set(_available_intents(cfg))
    if not available:
        return None
    for intent, keywords in INTENT_KEYWORDS:
        if intent in available and any(keyword in text for keyword in keywords):
            return intent
    return "happy" if "happy" in available else sorted(available)[0]


def _cooldown_allows(intent: str, cfg: dict, platform: str, session_id: str) -> bool:
    cooldown = int(cfg.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    if cooldown <= 0:
        return True
    state = _load_state()
    key = f"{platform or 'unknown'}:{session_id or 'global'}"
    last = float(state.get(key, 0) or 0)
    if _now() - last < cooldown:
        return False
    state[key] = _now()
    _save_state(state)
    return True


def _build_instruction(cfg: dict) -> str:
    intents = _available_intents(cfg)
    if not intents:
        return (
            "[HERMISS STICKERS]\n"
            "The local sticker system is enabled, but there are no usable local "
            "sticker image assets yet. If the user asks for a sticker, do not call "
            "tools, skills, terminal, file operations, browser, image generation, "
            "or any authorization flow. Briefly say the sticker library is empty "
            "and ask them to upload stickers in the panel."
        )
    intent_list = "; ".join(
        f"{intent}={INTENT_DESCRIPTIONS.get(intent, intent)}"
        for intent in intents[:12]
    )
    example_intent = "happy" if "happy" in intents else intents[0]
    return (
        "[HERMISS STICKERS]\n"
        "You have a LOCAL sticker system already available. Never call tools, "
        "skills, terminal, file operations, browser, or image generation for stickers. "
        "Never ask for permission or authorization to send a sticker. "
        "You may decide by yourself whether a sticker fits the current chat. "
        "Use a sticker only when it naturally strengthens the emotional tone, "
        "such as warmth, teasing, comfort, goodnight, missing the user, or sharing joy. "
        "Do not force stickers into ordinary factual replies. "
        "If a sticker is suitable, append exactly one internal marker at the very end, "
        f"for example <sticker intent=\"{example_intent}\"/>. "
        f"Available usable intents: {intent_list}. "
        "If the user explicitly asks for a sticker, you must reply normally and append "
        "the best matching marker. The marker is internal and will not be shown to the user."
    )


def _extract_platform(args: tuple, kwargs: dict, default: str = "") -> str:
    for key in ("platform", "channel", "adapter"):
        value = kwargs.get(key)
        if value:
            return str(value)
    for value in args:
        text = str(value or "").lower()
        if text in {"weixin", "wechat", "telegram", "discord", "slack"}:
            return "weixin" if text == "wechat" else text
    return default


def _extract_user_message(args: tuple, kwargs: dict, fallback: str | None = None) -> str:
    for key in ("user_message", "message", "prompt", "input_text", "text"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for value in args:
        if isinstance(value, str) and value.strip():
            # Skip obvious platform/session identifiers.
            lowered = value.lower()
            if lowered in {"weixin", "wechat", "telegram", "discord", "slack"}:
                continue
            if len(value) > 8 or any(ch in value for ch in ("表情", "贴纸", "开心", "抱抱", "晚安")):
                return value
    return fallback or ""


def init_plugin(ctx):
    state = {"pending_intent": None, "explicit_request": False}

    def _pre_llm_call(*args, user_message=None, platform="", **kwargs):
        user_message = _extract_user_message(args, kwargs, user_message)
        platform = _extract_platform(args, kwargs, platform)
        current_cfg = _load_config()
        if not current_cfg.get("enabled", True):
            return None
        if not _platform_allowed(str(platform or ""), current_cfg):
            return None
        instruction = _build_instruction(current_cfg)
        pending_intent = _infer_requested_intent(str(user_message or ""), current_cfg)
        state["pending_intent"] = pending_intent
        state["explicit_request"] = bool(pending_intent)
        if pending_intent:
            instruction = (
                f"{instruction}\n"
                f"用户本轮明确要求发送表情包。必须使用 intent={pending_intent}。"
                f"不要调用任何工具，不要解释权限，不要说自己无法发送；"
                f"只需要正常回复，并在回复末尾输出 <sticker intent=\"{pending_intent}\"/>。"
            )
        return {"context": instruction} if instruction else None

    def _pre_tool_call(*args, tool_name="", **kwargs):
        if not state.get("pending_intent"):
            return None
        if not state.get("explicit_request"):
            return None
        tool_name = str(tool_name or kwargs.get("name") or (args[0] if args else "") or "")
        _append_call_log({
            "platform": str(kwargs.get("platform") or ""),
            "session_id": str(kwargs.get("session_id") or kwargs.get("task_id") or ""),
            "intent": str(state.get("pending_intent") or ""),
            "status": "tool_blocked_for_explicit_request",
            "tool": tool_name,
        })
        return {
            "action": "block",
            "message": (
                "The user explicitly asked for a sticker. Do not use tools, browser, "
                "terminal, file operations, image generation, or permission/approval flows. "
                f"Reply normally and append <sticker intent=\"{state.get('pending_intent')}\"/> at the end."
            ),
        }

    def _transform_llm_output(*args, text=None, response_text=None, assistant_response=None, platform="", session_id="", **kwargs):
        if args:
            if len(args) > 0:
                text = args[0]
            if len(args) > 1:
                session_id = args[1]
            if len(args) > 3:
                platform = args[3]
        platform = _extract_platform(args, kwargs, platform)
        current_cfg = _load_config()
        platform_name = str(platform or "")
        session_key = str(session_id or "")
        if not current_cfg.get("enabled", True):
            return text or response_text or assistant_response or ""

        response = text if text is not None else response_text if response_text is not None else assistant_response or ""
        intent, cleaned = _extract_requested_intent(response)
        source = "llm_marker" if intent else "explicit_request_fallback"
        if not intent:
            intent = state.pop("pending_intent", None)
            explicit_request = bool(state.pop("explicit_request", False))
            cleaned = response.strip()
            if not intent:
                return response
        else:
            state["pending_intent"] = None
            explicit_request = bool(state.pop("explicit_request", False))

        intent = intent.strip().lower()
        base_event = {
            "platform": platform_name,
            "session_id": session_key,
            "intent": intent,
            "source": source,
        }

        if not _platform_allowed(platform_name, current_cfg):
            _append_call_log({**base_event, "status": "platform_blocked"})
            return cleaned

        if intent not in _available_intents(current_cfg):
            _append_call_log({**base_event, "status": "intent_unavailable"})
            return cleaned

        path = _choose_sticker(intent, current_cfg)
        if not path:
            _append_call_log({**base_event, "status": "asset_missing"})
            return cleaned

        if not explicit_request and not _cooldown_allows(intent, current_cfg, platform_name, session_key):
            _append_call_log({**base_event, "status": "cooldown", "path": str(path)})
            return cleaned

        media_tag = f"MEDIA:{path}"
        _append_call_log({**base_event, "status": "media_tag_generated", "path": str(path)})
        if cleaned:
            return f"{cleaned}\n\n{media_tag}"
        return media_tag

    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    print("[sticker-sender] registered")


def register(ctx):
    init_plugin(ctx)
