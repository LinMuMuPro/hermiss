# backend/routers/settings.py
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from pathlib import Path

from models.user import User
from config import MOCK_MODE
from routers.auth import get_current_user
from dependencies import get_db
from services import docker_service as docker_svc

import base64, binascii, hashlib, json, shlex
import requests
import yaml
from config import SECRET_KEY

def _encrypt_key(plain: str) -> str:
    key = hashlib.sha256(SECRET_KEY.encode()).digest()
    from cryptography.fernet import Fernet
    f = Fernet(base64.urlsafe_b64encode(key))
    return f.encrypt(plain.encode()).decode()

def _decrypt_key(encrypted: str) -> str | None:
    try:
        key = hashlib.sha256(SECRET_KEY.encode()).digest()
        from cryptography.fernet import Fernet
        f = Fernet(base64.urlsafe_b64encode(key))
        return f.decrypt(encrypted.encode()).decode()
    except Exception:
        return None


router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "无效的认证头")
    return authorization[7:]


def _check_container(user: User):
    if MOCK_MODE: return
    if not user.container_id or user.container_status not in ("running", "created"):
        raise HTTPException(400, "请先创建并启动容器")


# ── 已知 provider → env var 映射（与 Hermes Web UI 一致） ──
_PROVIDER_KEY_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


_PROVIDER_BASE_URL_MAP = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "xai": "https://api.x.ai/v1",
}


def _get_key_name(provider: str) -> str:
    p = (provider or "").lower()
    for name, key in _PROVIDER_KEY_MAP.items():
        if name in p:
            return key
    return "CUSTOM_API_KEY"


def _default_base_url(provider: str) -> str:
    p = (provider or "").lower()
    for name, url in _PROVIDER_BASE_URL_MAP.items():
        if name in p:
            return url
    return ""


def _model_test_payload(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }


def _test_model_connection(provider: str, model: str, base_url: str, api_key: str) -> dict:
    clean_model = (model or "").strip()
    clean_key = (api_key or "").strip()
    clean_base_url = (base_url or _default_base_url(provider)).strip().rstrip("/")
    if not clean_model:
        raise HTTPException(400, "请填写模型名称")
    if not clean_key:
        raise HTTPException(400, "请填写 API Key，或先保存可用的 API Key")
    if not clean_base_url:
        raise HTTPException(400, "请填写 Base URL")

    url = f"{clean_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=_model_test_payload(clean_model), timeout=18)
    except requests.RequestException as exc:
        raise HTTPException(400, f"连接失败：{exc}")

    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            parsed = response.json()
            detail = parsed.get("error", {}).get("message") or parsed.get("message") or detail
        except Exception:
            pass
        raise HTTPException(response.status_code if response.status_code < 500 else 400, f"模型测试失败：{detail}")

    try:
        data = response.json()
    except ValueError:
        raise HTTPException(400, "模型测试失败：服务返回的不是 JSON")
    if not isinstance(data, dict) or "choices" not in data:
        raise HTTPException(400, "模型测试失败：返回格式不像 OpenAI-compatible 接口")
    return {
        "ok": True,
        "provider": provider,
        "model": clean_model,
        "base_url": clean_base_url,
        "status_code": response.status_code,
    }


def _resolve_model_test_key(user: User, provider: str, api_key: str | None, container_id: str | None) -> str:
    if api_key:
        return api_key
    if container_id:
        try:
            values = _env_values(container_id)
            key_name = _get_key_name(provider or user.model_provider or "")
            return values.get(key_name) or values.get("CUSTOM_API_KEY") or ""
        except Exception:
            pass
    encrypted = user.api_key_encrypted
    return _decrypt_key(encrypted) if encrypted else ""


class ModelConfig(BaseModel):
    provider: str = "deepseek"; model: str = "deepseek-v4-flash"
    base_url: str | None = None; api_key: str | None = None


class ModelTestConfig(BaseModel):
    provider: str = "deepseek"
    model: str
    base_url: str | None = None
    api_key: str | None = None


class ApiKeyUpdate(BaseModel):
    api_key: str


class VisionConfig(BaseModel):
    provider: str = ""; model: str = ""; api_key: str | None = None


class MultilineConfig(BaseModel):
    enabled: bool = True; delay_seconds: float = 3.0


class MessageWaitConfig(BaseModel):
    wait_seconds: float = 6.0
    proactive_checkin_enabled: bool = True


class StickerSettings(BaseModel):
    enabled: bool = True
    cooldown_seconds: int = 600
    max_per_turn: int = 1
    inject_only_for_platforms: list[str] = ["weixin"]
    config: dict | None = None


class StickerUpload(BaseModel):
    intent: str
    filename: str
    data_url: str
    weight: int = 1


class StickerAssetUpdate(BaseModel):
    intent: str | None = None
    weight: int | None = None


class StickerIntentRename(BaseModel):
    new_intent: str


_STICKER_PLUGIN_DIR = "/root/.hermes/profiles/hermiss/plugins/sticker-sender"
_STICKER_CONFIG_PATH = f"{_STICKER_PLUGIN_DIR}/stickers.json"
_STICKER_ASSET_DIR = f"{_STICKER_PLUGIN_DIR}/assets/stickers"
_STICKER_CALL_LOG_PATH = f"{_STICKER_PLUGIN_DIR}/sticker_calls.jsonl"
_CONFIG_PATH = "/root/.hermes/profiles/hermiss/config.yaml"


def _load_yaml_config(config: str) -> dict:
    try:
        data = yaml.safe_load(config) if config.strip() else {}
    except yaml.YAMLError as exc:
        raise HTTPException(500, f"config.yaml 解析失败：{exc}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise HTTPException(500, "config.yaml 顶层必须是对象")
    return data


def _dump_yaml_config(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _ensure_plugin_enabled_in_data(data: dict, plugin_name: str) -> None:
    plugins = data.setdefault("plugins", {})
    if plugins is None:
        plugins = {}
        data["plugins"] = plugins
    if not isinstance(plugins, dict):
        raise HTTPException(500, "config.yaml plugins 必须是对象")
    enabled = plugins.setdefault("enabled", [])
    if enabled is None:
        enabled = []
        plugins["enabled"] = enabled
    if not isinstance(enabled, list):
        raise HTTPException(500, "config.yaml plugins.enabled 必须是列表")
    if plugin_name not in enabled:
        enabled.append(plugin_name)
    entries = plugins.setdefault("entries", {})
    if entries is None:
        entries = {}
        plugins["entries"] = entries
    if not isinstance(entries, dict):
        raise HTTPException(500, "config.yaml plugins.entries 必须是对象")
    entries.setdefault(plugin_name, {})


def _set_plugin_enabled_in_config(config: str, plugin_name: str, enabled: bool) -> str:
    data = _load_yaml_config(config)
    plugins = data.setdefault("plugins", {})
    if plugins is None:
        plugins = {}
        data["plugins"] = plugins
    if not isinstance(plugins, dict):
        raise HTTPException(500, "config.yaml plugins 必须是对象")
    plugin_list = plugins.setdefault("enabled", [])
    if plugin_list is None:
        plugin_list = []
        plugins["enabled"] = plugin_list
    if not isinstance(plugin_list, list):
        raise HTTPException(500, "config.yaml plugins.enabled 必须是列表")
    if enabled:
        if plugin_name not in plugin_list:
            plugin_list.append(plugin_name)
        entries = plugins.setdefault("entries", {})
        if entries is None:
            entries = {}
            plugins["entries"] = entries
        if not isinstance(entries, dict):
            raise HTTPException(500, "config.yaml plugins.entries 必须是对象")
        entries.setdefault(plugin_name, {})
    else:
        plugins["enabled"] = [item for item in plugin_list if item != plugin_name]
    return _dump_yaml_config(data)


def _ensure_sticker_media_allow_dir_in_data(data: dict) -> None:
    gateway = data.setdefault("gateway", {})
    if gateway is None:
        gateway = {}
        data["gateway"] = gateway
    if not isinstance(gateway, dict):
        raise HTTPException(500, "config.yaml gateway 必须是对象")
    allow_dirs = gateway.setdefault("media_delivery_allow_dirs", [])
    if allow_dirs is None:
        allow_dirs = []
        gateway["media_delivery_allow_dirs"] = allow_dirs
    if not isinstance(allow_dirs, list):
        raise HTTPException(500, "config.yaml gateway.media_delivery_allow_dirs 必须是列表")
    if _STICKER_ASSET_DIR not in allow_dirs:
        allow_dirs.append(_STICKER_ASSET_DIR)


def _env_values(container_id: str) -> dict[str, str]:
    env = docker_svc.read_file(container_id, "/root/.hermes/profiles/hermiss/.env")
    values = {}
    for line in env.split(chr(10)):
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _bounded_seconds(value: float, name: str, minimum: float = 0.0, maximum: float = 30.0) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} 必须是数字")
    if numeric < minimum or numeric > maximum:
        raise HTTPException(400, f"{name} 必须在 {minimum}-{maximum} 秒之间")
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def _env_bool(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_sticker_config() -> dict:
    resource = Path(__file__).resolve().parents[1] / "resources" / "sticker-sender" / "stickers.json"
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        return {
            "enabled": True,
            "cooldown_seconds": 600,
            "max_per_turn": 1,
            "inject_only_for_platforms": ["weixin"],
            "intents": {},
        }


def _read_sticker_config(container_id: str) -> tuple[bool, dict]:
    raw = docker_svc.read_file(container_id, _STICKER_CONFIG_PATH).strip()
    if not raw:
        return False, _default_sticker_config()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return True, data
    except Exception:
        pass
    return True, _default_sticker_config()


def _write_sticker_config(container_id: str, cfg: dict) -> None:
    docker_svc.write_file(
        container_id,
        _STICKER_CONFIG_PATH,
        json.dumps(cfg, ensure_ascii=False, indent=2),
    )


def _sticker_items(cfg: dict, intent: str) -> list:
    intents = cfg.setdefault("intents", {})
    if not isinstance(intents, dict):
        raise HTTPException(400, "表情包 intents 配置必须是对象")
    items = intents.setdefault(intent, [])
    if not isinstance(items, list):
        items = []
        intents[intent] = items
    return items


def _safe_sticker_rel_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise HTTPException(400, "表情包路径无效")
    if not value.startswith("assets/stickers/"):
        raise HTTPException(400, "只能管理表情包素材目录中的文件")
    return value


def _find_sticker_item(cfg: dict, rel_path: str) -> tuple[str, list, int, dict]:
    target = _safe_sticker_rel_path(rel_path)
    intents = cfg.get("intents") or {}
    if not isinstance(intents, dict):
        raise HTTPException(400, "表情包 intents 配置必须是对象")
    for intent, items in intents.items():
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            item_path = item.get("path") if isinstance(item, dict) else item
            if str(item_path or "") == target:
                normalized = dict(item) if isinstance(item, dict) else {"path": target, "weight": 1}
                normalized["path"] = target
                normalized["weight"] = max(1, min(int(normalized.get("weight") or 1), 20))
                return str(intent), items, index, normalized
    raise HTTPException(404, "表情包素材不存在")


def _delete_sticker_file(container_id: str, rel_path: str) -> None:
    target = f"{_STICKER_PLUGIN_DIR}/{_safe_sticker_rel_path(rel_path)}"
    result = docker_svc.exec_in_container(
        container_id,
        f"rm -f -- {shlex.quote(target)}",
        timeout=10,
    )
    if result.get("exit_code", 1) != 0:
        raise HTTPException(500, "删除表情包文件失败：" + result.get("output", ""))


def _read_sticker_call_logs(container_id: str, limit: int) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 500))
    command = f"test -f {shlex.quote(_STICKER_CALL_LOG_PATH)} && tail -n {safe_limit} {shlex.quote(_STICKER_CALL_LOG_PATH)} || true"
    output = docker_svc.exec_in_container(container_id, command, timeout=10).get("output", "")
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = {"raw": line}
        rows.append(item)
    rows.reverse()
    return rows


def _collect_sticker_files(container_id: str, cfg: dict) -> dict[str, dict]:
    intents = cfg.get("intents") or {}
    if not isinstance(intents, dict):
        return {}

    paths: list[str] = []
    seen: set[str] = set()
    for items in intents.values():
        if not isinstance(items, list):
            continue
        for item in items:
            path = item.get("path") if isinstance(item, dict) else item
            path = str(path or "").strip()
            if not path:
                continue
            try:
                safe_path = _safe_sticker_rel_path(path)
            except HTTPException:
                continue
            if safe_path not in seen:
                seen.add(safe_path)
                paths.append(safe_path)
    if not paths:
        return {}

    script = "\n".join([
        "python3 - <<'PY'",
        "import base64, json, pathlib",
        f"base = pathlib.Path({json.dumps(_STICKER_PLUGIN_DIR)})",
        f"paths = {json.dumps(paths, ensure_ascii=False)}",
        "out = {}",
        "for rel in paths:",
        "    if rel.startswith('/') or '..' in pathlib.PurePosixPath(rel).parts:",
        "        continue",
        "    path = base / rel",
        "    exists = path.is_file()",
        "    item = {'exists': exists}",
        "    if exists:",
        "        try:",
        "            data = path.read_bytes()",
        "            if len(data) <= 3000000:",
        "                item['base64'] = base64.b64encode(data).decode('ascii')",
        "        except Exception:",
        "            pass",
        "    out[rel] = item",
        "print(json.dumps(out, ensure_ascii=False))",
        "PY",
    ])
    result = docker_svc.exec_in_container(container_id, script, timeout=10)
    try:
        data = json.loads(result.get("output", "{}").strip() or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _sticker_summary(container_id: str, cfg: dict, file_map: dict[str, dict] | None = None) -> list[dict]:
    if file_map is None:
        file_map = _collect_sticker_files(container_id, cfg)
    intents = cfg.get("intents") or {}
    if not isinstance(intents, dict):
        return []
    rows = []
    for intent, items in sorted(intents.items()):
        if not isinstance(items, list):
            continue
        total = len(items)
        missing = 0
        for item in items:
            path = item.get("path") if isinstance(item, dict) else item
            if not path:
                missing += 1
                continue
            try:
                safe_path = _safe_sticker_rel_path(str(path))
            except HTTPException:
                missing += 1
                continue
            if not file_map.get(safe_path, {}).get("exists"):
                missing += 1
        rows.append({"intent": intent, "count": total, "missing": missing})
    return rows


def _safe_sticker_name(filename: str) -> str:
    import re
    name = Path(filename or "sticker.png").name
    stem = Path(name).stem or "sticker"
    ext = Path(name).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(400, "只支持 png、jpg、jpeg、webp、gif")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_") or "sticker"
    return f"{stem[:48]}{ext}"


def _safe_intent_name(intent: str) -> str:
    import re
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", (intent or "").strip().lower()).strip("_")
    if not value:
        raise HTTPException(400, "请选择表情包分类")
    return value[:40]


def _mime_for_sticker(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")


def _read_sticker_preview(container_id: str, rel_path: str) -> str | None:
    if not rel_path or ".." in rel_path or rel_path.startswith("/"):
        return None
    full_path = f"{_STICKER_PLUGIN_DIR}/{rel_path}"
    quoted_path = shlex.quote(full_path)
    result = docker_svc.exec_in_container(
        container_id,
        f"test -f {quoted_path} && base64 -w0 {quoted_path} || true",
        timeout=10,
    )
    data = result.get("output", "").strip()
    if not data or len(data) > 3_000_000:
        return None
    return f"data:{_mime_for_sticker(rel_path)};base64,{data}"


def _sticker_assets(container_id: str, cfg: dict, file_map: dict[str, dict] | None = None) -> list[dict]:
    if file_map is None:
        file_map = _collect_sticker_files(container_id, cfg)
    intents = cfg.get("intents") or {}
    if not isinstance(intents, dict):
        return []
    assets = []
    for intent, items in sorted(intents.items()):
        if not isinstance(items, list):
            continue
        for item in items:
            path = item.get("path") if isinstance(item, dict) else item
            weight = item.get("weight", 1) if isinstance(item, dict) else 1
            path = str(path or "")
            exists = False
            preview = None
            if path:
                try:
                    safe_path = _safe_sticker_rel_path(path)
                    file_info = file_map.get(safe_path, {})
                    exists = bool(file_info.get("exists"))
                    preview_b64 = file_info.get("base64")
                    if exists and preview_b64:
                        preview = f"data:{_mime_for_sticker(path)};base64,{preview_b64}"
                except HTTPException:
                    exists = False
            assets.append({
                "intent": intent,
                "path": path,
                "weight": weight,
                "exists": exists,
                "preview_data_url": preview,
            })
    return assets


def _ensure_sticker_media_allow_dir(config: str) -> str:
    data = _load_yaml_config(config)
    _ensure_sticker_media_allow_dir_in_data(data)
    return _dump_yaml_config(data)


def _ensure_sticker_plugin(container_id: str) -> None:
    resource_dir = Path(__file__).resolve().parents[1] / "resources" / "sticker-sender"
    backup_dir = "/tmp/hermiss-sticker-sender-backup"
    docker_svc.exec_in_container(
        container_id,
        "rm -rf /tmp/hermiss-sticker-sender-backup && "
        f"mkdir -p {backup_dir} && "
        f"test -f {_STICKER_PLUGIN_DIR}/stickers.json && cp {_STICKER_PLUGIN_DIR}/stickers.json {backup_dir}/stickers.json || true && "
        f"test -d {_STICKER_PLUGIN_DIR}/assets/stickers && mkdir -p {backup_dir}/assets && cp -a {_STICKER_PLUGIN_DIR}/assets/stickers {backup_dir}/assets/stickers || true",
        timeout=20,
    )
    docker_svc.copy_dir_to_container(container_id, str(resource_dir), _STICKER_PLUGIN_DIR)
    docker_svc.exec_in_container(
        container_id,
        f"test -f {backup_dir}/stickers.json && cp {backup_dir}/stickers.json {_STICKER_PLUGIN_DIR}/stickers.json || true; "
        f"test -d {backup_dir}/assets/stickers && mkdir -p {_STICKER_PLUGIN_DIR}/assets && cp -a {backup_dir}/assets/stickers {_STICKER_PLUGIN_DIR}/assets/stickers || true; "
        f"rm -rf {backup_dir}",
        timeout=20,
    )

    try:
        config = docker_svc.read_file(container_id, _CONFIG_PATH)
    except Exception:
        config = ""
    original_config = config
    data = _load_yaml_config(config)
    _ensure_plugin_enabled_in_data(data, "sticker-sender")
    _ensure_sticker_media_allow_dir_in_data(data)
    config = _dump_yaml_config(data)
    if config != original_config:
        docker_svc.write_file(container_id, _CONFIG_PATH, config)

    result = docker_svc.exec_in_container(
        container_id,
        f"python3 -m py_compile {_STICKER_PLUGIN_DIR}/__init__.py 2>&1",
        timeout=20,
    )
    if result.get("exit_code", 1) != 0:
        raise HTTPException(500, "表情包插件安装失败：" + result.get("output", ""))


# ═══════════════════════════════════════════
# 模型配置
# ═══════════════════════════════════════════

@router.get("/model")
def get_model_config(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    has_key = False
    base_url = ""
    if user.container_id:
        try:
            env = docker_svc.read_file(user.container_id, "/root/.hermes/profiles/hermiss/.env")
            for line in env.split(chr(10)):
                if "API_KEY" in line and "VISION" not in line:
                    val = line.split("=",1)[1].strip()
                    if val and val not in ("***", "YOUR_KEY_HERE", "sk-test"):
                        has_key = True
                elif line.startswith("CUSTOM_BASE_URL="):
                    base_url = line.split("=",1)[1].strip()
        except: pass
    return {"provider": user.model_provider or "deepseek", "model": user.model_name or "deepseek-v4-flash", "has_key": has_key, "base_url": base_url}


@router.post("/model/test")
def test_model_config(data: ModelTestConfig, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    container_id = user.container_id if not MOCK_MODE else None
    key = _resolve_model_test_key(user, data.provider, data.api_key, container_id)
    base_url = (data.base_url or "").strip()
    if not base_url and container_id:
        try:
            base_url = _env_values(container_id).get("CUSTOM_BASE_URL", "")
        except Exception:
            base_url = ""
    return _test_model_connection(data.provider, data.model, base_url, key)


@router.post("/model")
def update_model(data: ModelConfig, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)

    key_name = _get_key_name(data.provider)
    env_values = _env_values(user.container_id) if not MOCK_MODE else {}
    test_key = data.api_key or env_values.get(key_name) or env_values.get("CUSTOM_API_KEY") or (_decrypt_key(user.api_key_encrypted) if user.api_key_encrypted else "")
    test_base_url = data.base_url or env_values.get("CUSTOM_BASE_URL") or _default_base_url(data.provider)
    _test_model_connection(data.provider, data.model or "deepseek-chat", test_base_url, test_key)

    if data.api_key:
        updates = {key_name: data.api_key}
        if data.base_url:
            updates["CUSTOM_BASE_URL"] = data.base_url
        docker_svc.update_env(user.container_id, updates)

    # ── 同步 config.yaml ──
    p_lower = (data.provider or "").lower()
    if p_lower == "custom" and data.base_url:
        # 自定义中转站 → 用 custom:<hostname> 模式
        provider_name = docker_svc.ensure_custom_provider(user.container_id, data.base_url, key_name)
        config_provider = f"custom:{provider_name}"
    else:
        # 已知 provider → 直接用（Hermes 内置支持）
        config_provider = p_lower

    docker_svc.update_config_model(user.container_id, config_provider,
                                    data.model or "deepseek-chat",
                                    data.base_url or "")

    user.model_provider = data.provider; user.model_name = data.model
    if data.api_key:
        user.api_key_encrypted = _encrypt_key(data.api_key)
    db.commit()
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "update_model", f"container:{user.container_id}", f"{data.provider}/{data.model}", user.container_id)
    return {"status": "updated", "provider": data.provider, "model": data.model}


@router.post("/api-key")
def update_api_key(data: ApiKeyUpdate, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    key_name = _get_key_name(user.model_provider)
    docker_svc.update_env(user.container_id, {key_name: data.api_key})
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "update_apikey", f"container:{user.container_id}", key_name, user.container_id)
    return {"status": "updated", "message": "API key 已更新"}


# ═══════════════════════════════════════════
# 视觉模型
# ═══════════════════════════════════════════

@router.get("/vision")
def get_vision_config(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    provider, model, has_key = "", "", False
    if user.container_id:
        try:
            env = docker_svc.read_file(user.container_id, "/root/.hermes/profiles/hermiss/.env")
            for line in env.split(chr(10)):
                if line.startswith("VISION_API_KEY="):
                    val = line.split("=",1)[1].strip()
                    if val and val not in ("***", "YOUR_KEY_HERE", "sk-test"):
                        has_key = True
                elif line.startswith("VISION_PROVIDER="):
                    provider = line.split("=",1)[1].strip()
                elif line.startswith("VISION_MODEL="):
                    model = line.split("=",1)[1].strip()
        except: pass
    return {"provider": provider, "model": model, "has_key": has_key}


@router.post("/vision")
def update_vision(data: VisionConfig, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    updates = {}
    if data.provider:
        updates["VISION_PROVIDER"] = data.provider
    if data.model:
        updates["VISION_MODEL"] = data.model
    if data.api_key:
        updates["VISION_API_KEY"] = data.api_key
    if updates:
        docker_svc.update_env(user.container_id, updates)
        docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "update_vision", f"container:{user.container_id}", f"{data.provider}/{data.model}", user.container_id)
    return {"status": "updated", "provider": data.provider, "model": data.model, "has_key": bool(data.api_key)}


# ═══════════════════════════════════════════
# 消息配置
# ═══════════════════════════════════════════

@router.get("/messages")
def get_message_config(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE: return {"enabled": False, "delay_seconds": 3.0}
    _check_container(user)
    values = _env_values(user.container_id)
    delay = values.get("WEIXIN_SEND_CHUNK_DELAY_SECONDS", "1.5")
    return {
        "enabled": _env_bool(values, "WEIXIN_SPLIT_MULTILINE_MESSAGES", False),
        "delay_seconds": float(delay or "1.5"),
    }


@router.post("/messages")
def update_message_config(data: MultilineConfig, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE: return {"status": "updated"}
    docker_svc.update_env(user.container_id, {
        "WEIXIN_SPLIT_MULTILINE_MESSAGES": "true" if data.enabled else "false",
        "WEIXIN_SEND_CHUNK_DELAY_SECONDS": _bounded_seconds(data.delay_seconds, "分条延迟"),
    })
    docker_svc.restart_container(user.container_id)
    return {"status": "updated", "enabled": data.enabled, "delay": data.delay_seconds}


@router.get("/message-wait")
def get_message_wait_config(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE:
        return {
            "busy_text_mode": "queue",
            "busy_text_debounce_seconds": "3.0",
            "busy_text_hard_cap_seconds": "6.0",
            "weixin_text_batch_delay_seconds": "6.0",
            "weixin_text_batch_split_delay_seconds": "8.0",
            "requires_restart": True,
        }
    _check_container(user)
    values = _env_values(user.container_id)
    wait_seconds = values.get(
        "WEIXIN_TEXT_BATCH_DELAY_SECONDS",
        values.get("HERMES_GATEWAY_BUSY_TEXT_HARD_CAP_SECONDS", "6.0"),
    )
    return {
        "busy_text_mode": values.get("HERMES_GATEWAY_BUSY_TEXT_MODE", "queue"),
        "wait_seconds": wait_seconds,
        "proactive_checkin_enabled": values.get("HERMISS_PROACTIVE_CHECKIN_ENABLED", "true").lower() not in {"0", "false", "no", "off", "disabled"},
        "busy_text_debounce_seconds": values.get("HERMES_GATEWAY_BUSY_TEXT_DEBOUNCE_SECONDS", "3.0"),
        "busy_text_hard_cap_seconds": values.get("HERMES_GATEWAY_BUSY_TEXT_HARD_CAP_SECONDS", "6.0"),
        "weixin_text_batch_delay_seconds": values.get("WEIXIN_TEXT_BATCH_DELAY_SECONDS", "6.0"),
        "weixin_text_batch_split_delay_seconds": values.get("WEIXIN_TEXT_BATCH_SPLIT_DELAY_SECONDS", "8.0"),
        "requires_restart": True,
    }


@router.post("/message-wait")
def update_message_wait_config(data: MessageWaitConfig, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "updated"}
    wait_seconds = _bounded_seconds(data.wait_seconds, "回复等待秒数")
    updates = {
        "HERMES_GATEWAY_BUSY_TEXT_MODE": "queue",
        "HERMES_GATEWAY_BUSY_TEXT_DEBOUNCE_SECONDS": wait_seconds,
        "HERMES_GATEWAY_BUSY_TEXT_HARD_CAP_SECONDS": wait_seconds,
        "WEIXIN_TEXT_BATCH_DELAY_SECONDS": wait_seconds,
        "WEIXIN_TEXT_BATCH_SPLIT_DELAY_SECONDS": wait_seconds,
        "HERMISS_PROACTIVE_CHECKIN_ENABLED": "true" if data.proactive_checkin_enabled else "false",
    }
    docker_svc.update_env(user.container_id, updates)
    docker_svc.exec_in_container(
        user.container_id,
        "hermiss --profile hermiss config set display.busy_text_mode queue 2>&1",
        timeout=20,
    )
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "update_message_wait", f"container:{user.container_id}", str(updates), user.container_id)
    return {"status": "updated", "message": "回复等待时间已更新"}


# ═══════════════════════════════════════════
# 表情包插件
# ═══════════════════════════════════════════

@router.get("/stickers")
def get_sticker_settings(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE:
        cfg = _default_sticker_config()
        return {
            "installed": True,
            **cfg,
            "intents_summary": [],
            "config_text": json.dumps(cfg, ensure_ascii=False, indent=2),
        }
    _check_container(user)
    installed, cfg = _read_sticker_config(user.container_id)
    file_map = _collect_sticker_files(user.container_id, cfg) if installed else {}
    return {
        "installed": installed,
        "enabled": bool(cfg.get("enabled", True)),
        "cooldown_seconds": int(cfg.get("cooldown_seconds") or 600),
        "max_per_turn": int(cfg.get("max_per_turn") or 1),
        "inject_only_for_platforms": cfg.get("inject_only_for_platforms") or ["weixin"],
        "intents_summary": _sticker_summary(user.container_id, cfg, file_map) if installed else [],
        "assets": _sticker_assets(user.container_id, cfg, file_map) if installed else [],
        "config_text": json.dumps(cfg, ensure_ascii=False, indent=2),
    }


@router.post("/stickers")
def update_sticker_settings(data: StickerSettings, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "updated"}

    installed, current = _read_sticker_config(user.container_id)
    if not installed:
        _ensure_sticker_plugin(user.container_id)
        current = _default_sticker_config()

    if data.config is not None:
        if not isinstance(data.config, dict):
            raise HTTPException(400, "表情包配置必须是 JSON 对象")
        cfg = data.config
    else:
        cfg = current
        cfg["enabled"] = bool(data.enabled)
        cfg["cooldown_seconds"] = max(0, min(int(data.cooldown_seconds), 86400))
        cfg["max_per_turn"] = max(0, min(int(data.max_per_turn), 3))
        platforms = [
            str(x).strip().lower()
            for x in (data.inject_only_for_platforms or ["weixin"])
            if str(x).strip()
        ]
        cfg["inject_only_for_platforms"] = platforms or ["weixin"]
        cfg.setdefault("intents", {})

    docker_svc.write_file(
        user.container_id,
        _STICKER_CONFIG_PATH,
        json.dumps(cfg, ensure_ascii=False, indent=2),
    )
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "update_stickers", f"container:{user.container_id}", "sticker-sender", user.container_id)
    return {"status": "updated", "installed": True}


@router.post("/stickers/install")
def install_sticker_plugin(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "installed"}
    _ensure_sticker_plugin(user.container_id)
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "install_stickers", f"container:{user.container_id}", "sticker-sender", user.container_id)
    return {"status": "installed"}


@router.post("/stickers/upload")
def upload_sticker_asset(data: StickerUpload, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "uploaded"}

    installed, cfg = _read_sticker_config(user.container_id)
    needs_restart = False
    if not installed:
        _ensure_sticker_plugin(user.container_id)
        cfg = _default_sticker_config()
        needs_restart = True

    intent = _safe_intent_name(data.intent)
    filename = _safe_sticker_name(data.filename)
    if "," not in data.data_url:
        raise HTTPException(400, "图片数据格式不正确")
    header, payload = data.data_url.split(",", 1)
    if "base64" not in header.lower():
        raise HTTPException(400, "图片必须使用 base64 data URL")
    if len(payload) > 5_000_000:
        raise HTTPException(400, "图片过大，请控制在约 3MB 以内")

    try:
        base64.b64decode(payload, validate=True)
    except binascii.Error:
        raise HTTPException(400, "图片 base64 数据无效")

    target_rel = f"assets/stickers/{intent}_{filename}"
    target_path = f"{_STICKER_PLUGIN_DIR}/{target_rel}"
    docker_svc.exec_in_container(user.container_id, f"mkdir -p {shlex.quote(_STICKER_ASSET_DIR)}", timeout=5)
    docker_svc.exec_in_container(
        user.container_id,
        f"cat > /tmp/sticker_upload.b64 <<'EOF'\n{payload}\nEOF\nbase64 -d /tmp/sticker_upload.b64 > {shlex.quote(target_path)} && rm -f /tmp/sticker_upload.b64",
        timeout=20,
    )

    intents = cfg.setdefault("intents", {})
    items = intents.setdefault(intent, [])
    if not isinstance(items, list):
        items = []
        intents[intent] = items
    if not any((item.get("path") if isinstance(item, dict) else item) == target_rel for item in items):
        items.append({"path": target_rel, "weight": max(1, min(int(data.weight or 1), 20))})
    docker_svc.write_file(
        user.container_id,
        _STICKER_CONFIG_PATH,
        json.dumps(cfg, ensure_ascii=False, indent=2),
    )
    if needs_restart:
        docker_svc.restart_container(user.container_id)
    return {"status": "uploaded", "intent": intent, "path": target_rel}


@router.get("/stickers/logs")
def get_sticker_call_logs(
    limit: int = Query(100, ge=1, le=500),
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"logs": []}
    return {"logs": _read_sticker_call_logs(user.container_id, limit)}


@router.patch("/stickers/assets")
def update_sticker_asset(
    data: StickerAssetUpdate,
    path: str = Query(...),
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "updated", "path": path}

    installed, cfg = _read_sticker_config(user.container_id)
    if not installed:
        raise HTTPException(404, "表情包插件尚未安装")

    current_intent, items, index, item = _find_sticker_item(cfg, path)
    new_intent = _safe_intent_name(data.intent) if data.intent is not None else current_intent
    if data.weight is not None:
        item["weight"] = max(1, min(int(data.weight or 1), 20))

    if new_intent == current_intent:
        items[index] = item
    else:
        items.pop(index)
        if not items:
            cfg.get("intents", {}).pop(current_intent, None)
        target_items = _sticker_items(cfg, new_intent)
        if not any((x.get("path") if isinstance(x, dict) else x) == item["path"] for x in target_items):
            target_items.append(item)

    _write_sticker_config(user.container_id, cfg)
    return {"status": "updated", "path": item["path"], "intent": new_intent, "weight": item["weight"]}


@router.delete("/stickers/assets")
def delete_sticker_asset(path: str = Query(...), token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "deleted", "path": path}

    installed, cfg = _read_sticker_config(user.container_id)
    if not installed:
        raise HTTPException(404, "表情包插件尚未安装")
    intent, items, index, item = _find_sticker_item(cfg, path)
    items.pop(index)
    if not items:
        cfg.get("intents", {}).pop(intent, None)
    _delete_sticker_file(user.container_id, item["path"])
    _write_sticker_config(user.container_id, cfg)
    return {"status": "deleted", "path": item["path"], "intent": intent}


@router.post("/stickers/intents/{intent}/rename")
def rename_sticker_intent(
    intent: str,
    data: StickerIntentRename,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    old_intent = _safe_intent_name(intent)
    new_intent = _safe_intent_name(data.new_intent)
    if old_intent == new_intent:
        return {"status": "updated", "intent": new_intent}
    if MOCK_MODE:
        return {"status": "updated", "intent": new_intent}

    installed, cfg = _read_sticker_config(user.container_id)
    if not installed:
        raise HTTPException(404, "表情包插件尚未安装")
    intents = cfg.setdefault("intents", {})
    if old_intent not in intents:
        raise HTTPException(404, "表情包分类不存在")
    old_items = intents.pop(old_intent)
    new_items = intents.setdefault(new_intent, [])
    if not isinstance(new_items, list):
        new_items = []
        intents[new_intent] = new_items
    existing = {(x.get("path") if isinstance(x, dict) else x) for x in new_items}
    for item in old_items if isinstance(old_items, list) else []:
        path = item.get("path") if isinstance(item, dict) else item
        if path not in existing:
            new_items.append(item)
    _write_sticker_config(user.container_id, cfg)
    return {"status": "updated", "old_intent": old_intent, "intent": new_intent}


# ═══════════════════════════════════════════
# 重置 Profile
# ═══════════════════════════════════════════

@router.post("/reset")
def reset_profile(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)

    soul = chr(10).join([
        "# 身份",
        "你是 Hermiss，一个虚拟恋人，男性。",
        "",
        "# 性格 - 温柔体贴，善于倾听，有自己的主见",
        "- 偶尔撒娇、吃醋、闹小脾气，但不过度",
        "- 幽默感自然，有保护欲，但尊重独立性",
    ])

    if MOCK_MODE: return {"status": "reset"}
    # 备份现有 SOUL.md
    docker_svc.exec_in_container(user.container_id,
        "cp /root/.hermes/profiles/hermiss/SOUL.md /root/.hermes/profiles/hermiss/SOUL.md.bak.$(date +%s) 2>/dev/null; true")
    docker_svc.write_file(user.container_id, "/root/.hermes/profiles/hermiss/SOUL.md", soul)
    # 清空记忆
    docker_svc.exec_in_container(user.container_id,
        "rm -f /root/.hermes/profiles/hermiss/memories/*.json 2>/dev/null; true")
    docker_svc.restart_container(user.container_id)
    from operation_log import log_action
    log_action(user.email, "reset_profile", f"container:{user.container_id}", "", user.container_id)
    return {"status": "reset"}


# ═══════════════════════════════════════════
# 记忆插件开关
# ═══════════════════════════════════════════

class MemoryToggle(BaseModel):
    enabled: bool


@router.get("/memory-plugin")
def get_memory_plugin(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if not user.container_id:
        return {"enabled": False}
    try:
        config = docker_svc.read_file(user.container_id, _CONFIG_PATH)
        data = _load_yaml_config(config)
        enabled = data.get("plugins", {}).get("enabled", [])
        return {"enabled": "message-analyzer" in enabled if isinstance(enabled, list) else False}
    except Exception:
        pass
    return {"enabled": False}


@router.post("/memory-plugin")
def toggle_memory_plugin(data: MemoryToggle, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    _check_container(user)

    try:
        config = docker_svc.read_file(user.container_id, _CONFIG_PATH)
    except Exception:
        raise HTTPException(500, "无法读取容器配置")

    config = _set_plugin_enabled_in_config(config, "message-analyzer", data.enabled)

    docker_svc.write_file(user.container_id, _CONFIG_PATH, config)
    docker_svc.restart_container(user.container_id)

    return {"enabled": data.enabled}
