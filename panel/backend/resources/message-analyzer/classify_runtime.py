"""Runtime helpers for message classification calls."""

import json


SHORT_STATE_KEYS = ("short_state", "short_state_text", "short_state_minutes", "short_state_unavailable")


def needs_short_state_retry(parsed: dict | None, raw_text: str) -> bool:
    if not parsed:
        return False
    if str(parsed.get("short_state") or "none").lower() not in {"", "none"}:
        return False
    try:
        if int(parsed.get("check_in_hours") or 0) > 0:
            return True
        if int(parsed.get("check_in_minutes") or 0) > 0:
            return True
    except Exception:
        pass
    return False


def merge_short_state_retry(parsed: dict, retry_parsed: dict | None) -> dict:
    if not retry_parsed:
        return parsed
    for key in SHORT_STATE_KEYS:
        if retry_parsed.get(key) not in (None, "", "none", "无"):
            parsed[key] = retry_parsed.get(key)
    return parsed


def build_short_state_retry_prompt(message: str, current_short_state: dict | None) -> str:
    current_short_state_text = json.dumps(current_short_state, ensure_ascii=False) if current_short_state else "无"
    return f"""只判断下面这条用户消息是否包含短期用户状态。
不要回复用户，只输出 XML：
<hermes_classify>
短期状态: 开始|持续|结束|无
状态内容: 用户准备/正在……（无则写 无）
状态预计分钟: 数字，无法判断填 60
状态不便看手机: 是|否
</hermes_classify>

规则：
- 用户表达接下来要做、正在做、准备做、刚进入某种状态时，输出 开始或持续。例：我要去洗澡了、一会考试、准备休息。
- 用户表达活动结束、返回、完成、放弃、醒来时，输出 结束。例：回来了、做完了、睡醒了。
- 如果“当前已有短期状态”存在，用户中途发来普通聊天、吐槽、战况、情绪、问答或简短回应，不代表状态结束或切换；输出 持续，并沿用原状态内容。只有明确结束、返回、放弃，或开始另一个互斥活动，才改变状态。
- 普通寒暄、想念、问答，没有可延续活动或明确状态，输出 无。其他情况由上下文自行判断，不要依赖固定场景词表。

当前已有短期状态：
{current_short_state_text}

用户消息：
{message}"""


def classify_with_llm(
    *,
    llm_client,
    prompt: str,
    provider: str,
    model: str,
    purpose: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
):
    if callable(getattr(llm_client, "complete", None)):
        llm_kwargs = {
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": timeout,
            "purpose": purpose,
        }
        if provider and provider != "auto":
            llm_kwargs["provider"] = provider
        if model:
            llm_kwargs["model"] = model
        result = llm_client.complete(
            [{"role": "user", "content": prompt}],
            **llm_kwargs,
        )
        return getattr(result, "text", result)
    if callable(llm_client):
        return llm_client(prompt, max_tokens=max_tokens, temperature=temperature)
    return None
