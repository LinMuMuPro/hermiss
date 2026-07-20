# backend/services/sticker_service.py
import json
import shlex
from pathlib import Path

import yaml
from fastapi import HTTPException

from services import docker_service as docker_svc
from services.runtime_file_service import install_plugin_dir

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
    install_plugin_dir(container_id, str(resource_dir), _STICKER_PLUGIN_DIR)
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
