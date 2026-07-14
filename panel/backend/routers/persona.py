# backend/routers/persona.py
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
import json
import re
import requests
from pathlib import Path

from models.user import User
from config import MOCK_MODE
from routers.auth import get_current_user
from dependencies import get_db
from services import docker_service as docker_svc
from routers.settings import (
    _default_base_url,
    _env_values,
    _resolve_model_test_key,
)

def _default_persona_soul() -> str:
    path = Path(__file__).resolve().parents[1] / "resources" / "default_persona" / "SOUL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


router = APIRouter(prefix="/api/persona", tags=["persona"])


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "无效的认证头")
    return authorization[7:]


def _check_container(user: User):
    if MOCK_MODE: return
    if not user.container_id or user.container_status not in ("running", "created"):
        raise HTTPException(400, "请先创建并启动容器")


class PersonaUpdate(BaseModel):
    soul: str | None = None; user: str | None = None


class PersonaGenerate(BaseModel):
    name: str = ""
    relationship: str = "虚拟恋人"
    personality: str = ""
    speaking_style: str = ""
    background: str = ""
    boundaries: str = ""
    user_info: str = ""
    extra: str = ""


TEMPLATES = {
    "clear": {
        "name": "清空人设", "type": "none",
        "soul": "",
    },
    "default": {
        "name": "默认人设", "type": "none",
        "soul": _default_persona_soul(),
    },
}


@router.get("/current")
def get_persona(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE: return {"soul": "", "user": "", "note": "[MOCK]"}

    soul = docker_svc.read_file(user.container_id, "/root/.hermes/profiles/hermiss/SOUL.md")
    user_md = docker_svc.read_file(user.container_id, "/root/.hermes/profiles/hermiss/memories/USER.md")
    return {"soul": soul, "user": user_md}


def _strip_json_fence(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _persona_generate_prompt(data: PersonaGenerate) -> str:
    name = data.name.strip() or "未命名角色"
    relationship = data.relationship.strip() or "虚拟恋人"
    return f"""
你是 Hermiss 的人设生成器。请根据用户给出的关键词生成一份可直接写入 Hermes/Hermiss 的人设配置。

必须只输出 JSON，不要输出 Markdown 代码块，不要解释。
JSON 格式：
{{
  "soul": "写入 SOUL.md 的 Bot 人设 Markdown",
  "user": "写入 memories/USER.md 的用户信息 Markdown"
}}

生成要求：
1. soul 使用中文 Markdown，结构清晰但不要太长。
2. 必须使用用户输入的角色名称“{name}”，禁止擅自改名。
3. 必须使用用户输入的关系定位“{relationship}”，禁止擅自改成朋友、邻居、客服、老师等其他关系。
4. 角色不是 AI 助手，不是客服，不是老师，不是心理咨询师。
5. 关系优先，回答问题其次，重点是长期陪伴感。
6. 如果关系定位包含“恋人”，人设必须体现长期稳定、亲近但不油腻的恋人关系。
7. 默认自然、口语化、短回复、像微信聊天，不要长篇说教。
8. 只有用户明确寻求建议时才给建议。
9. 禁止自称“作为AI”“我无法”“根据您的描述”“希望对您有所帮助”。
10. 禁止编造现实动作，例如“我给你泡好了”“我放你桌上了”。
11. 禁止未经用户允许频繁使用 Emoji、颜文字、客服话术、官方话术。
12. 记忆规则要写明：自然使用记忆，不要生硬复述，不要强行拼凑所有记忆。
13. user 只写用户相关信息；如果用户信息为空，写一个很短的占位说明。

用户输入：
- 角色名称：{name}
- 关系定位：{relationship}
- 性格关键词：{data.personality or "温柔、有主见、自然"}
- 说话风格：{data.speaking_style or "短句、自然、像熟人聊天"}
- 背景设定：{data.background or "未指定"}
- 边界/禁忌：{data.boundaries or "不能编造现实动作；不能像客服或AI助手"}
- 用户信息：{data.user_info or "未指定"}
- 额外要求：{data.extra or "无"}
""".strip()


@router.post("/generate")
def generate_persona(data: PersonaGenerate, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)

    provider = user.model_provider or "deepseek"
    model = user.model_name or "deepseek-v4-flash"
    container_id = user.container_id if not MOCK_MODE else None
    api_key = _resolve_model_test_key(user, provider, None, container_id)
    base_url = ""
    if container_id:
        try:
            base_url = _env_values(container_id).get("CUSTOM_BASE_URL", "")
        except Exception:
            base_url = ""
    base_url = (base_url or _default_base_url(provider)).rstrip("/")
    if not api_key:
        raise HTTPException(400, "请先在设置中配置可用的 API Key")
    if not base_url:
        raise HTTPException(400, "请先在设置中配置可用的 Base URL")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON，不输出解释。"},
            {"role": "user", "content": _persona_generate_prompt(data)},
        ],
        "temperature": 0.7,
        "max_tokens": 2200,
    }
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
    except requests.RequestException as exc:
        raise HTTPException(400, f"人设生成连接失败：{exc}")
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            parsed = response.json()
            detail = parsed.get("error", {}).get("message") or parsed.get("message") or detail
        except Exception:
            pass
        raise HTTPException(400, f"人设生成失败：{detail}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        raise HTTPException(400, "人设生成失败：模型返回格式异常")
    try:
        generated = json.loads(_strip_json_fence(content))
    except Exception:
        generated = {"soul": content.strip(), "user": data.user_info.strip()}

    soul = str(generated.get("soul") or "").strip()
    user_md = str(generated.get("user") or "").strip()
    if len(soul) < 80:
        raise HTTPException(400, "人设生成失败：生成内容过短，请补充关键词后重试")
    expected_name = data.name.strip()
    expected_relationship = data.relationship.strip()
    if expected_name and expected_name not in soul:
        raise HTTPException(400, "人设生成失败：模型没有使用指定角色名称，请重试")
    if expected_relationship and expected_relationship not in soul:
        raise HTTPException(400, "人设生成失败：模型没有使用指定关系定位，请重试")
    return {"soul": soul, "user": user_md, "provider": provider, "model": model}


@router.post("/update")
def update_persona(data: PersonaUpdate, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)

    if data.soul is not None:
        docker_svc.write_file(user.container_id, "/root/.hermes/profiles/hermiss/SOUL.md", data.soul)
    if data.user is not None:
        docker_svc.write_file(user.container_id, "/root/.hermes/profiles/hermiss/memories/USER.md", data.user)

    docker_svc.restart_container(user.container_id)
    return {"status": "updated"}


@router.get("/templates")
def list_templates():
    return {"templates": [{"id": k, "name": v["name"], "type": v["type"]} for k, v in TEMPLATES.items()]}


@router.post("/apply-template/{template_id}")
def apply_template(template_id: str, token: str = Depends(get_token), db: Session = Depends(get_db)):
    if template_id not in TEMPLATES: raise HTTPException(404, f"模板 {template_id} 不存在")

    user = get_current_user(token, db)
    _check_container(user)

    template = TEMPLATES[template_id]
    docker_svc.write_file(user.container_id, "/root/.hermes/profiles/hermiss/SOUL.md", template["soul"])
    docker_svc.write_file(user.container_id, "/root/.hermes/profiles/hermiss/memories/USER.md", "")
    docker_svc.restart_container(user.container_id)

    return {"status": "applied", "template": template["name"]}
