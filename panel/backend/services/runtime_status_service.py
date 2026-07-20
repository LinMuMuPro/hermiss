import json
import re
from datetime import datetime, timezone

import yaml

from services import docker_service as docker_svc


PROFILE_DIR = "/root/.hermes/profiles/hermiss"


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _safe_json(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _file_meta(container_id: str, path: str) -> dict:
    result = docker_svc.exec_in_container(
        container_id,
        (
            "python3 - <<'PY'\n"
            "import json, pathlib\n"
            f"p = pathlib.Path({json.dumps(path)})\n"
            "if p.exists():\n"
            "    print(json.dumps({'exists': True, 'bytes': p.stat().st_size}, ensure_ascii=False))\n"
            "else:\n"
            "    print(json.dumps({'exists': False, 'bytes': 0}, ensure_ascii=False))\n"
            "PY"
        ),
        timeout=5,
    )
    return _safe_json(result.get("output", "").strip())


def _plugin_manifest(container_id: str, plugin_name: str) -> dict:
    path = f"{PROFILE_DIR}/plugins/{plugin_name}/manifest.json"
    data = _safe_json(docker_svc.read_file(container_id, path))
    return {
        "name": plugin_name,
        "installed": bool(data),
        "version": data.get("version") or "",
        "entry": data.get("entry") or "__init__.py",
        "requires_restart": bool(data.get("requires_restart", True)) if data else None,
        "path": f"{PROFILE_DIR}/plugins/{plugin_name}",
    }


def _memory_summary(container_id: str) -> dict:
    db_path = f"{PROFILE_DIR}/memory/hermes_memory.db"
    command = (
        "python3 - <<'PY'\n"
        "import json, sqlite3, pathlib\n"
        f"p = pathlib.Path({json.dumps(db_path)})\n"
        "out = {'db_exists': p.exists(), 'memory_count': 0, 'retrieval_log_count': 0}\n"
        "if p.exists():\n"
        "    con = sqlite3.connect(p)\n"
        "    try:\n"
        "        out['memory_count'] = con.execute('select count(*) from memories').fetchone()[0]\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        out['retrieval_log_count'] = con.execute('select count(*) from memory_retrieval_logs').fetchone()[0]\n"
        "    except Exception:\n"
        "        pass\n"
        "    con.close()\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
        "PY"
    )
    result = docker_svc.exec_in_container(container_id, command, timeout=8)
    return _safe_json(result.get("output", "").strip())


def _active_checkin(container_id: str) -> dict:
    data = _safe_json(docker_svc.read_file(container_id, f"{PROFILE_DIR}/reminders/active_checkin.json"))
    if not data:
        return {"exists": False, "active": False}
    return {
        "exists": True,
        "active": not bool(data.get("cancelled")),
        "check_in_minutes": data.get("check_in_minutes") or data.get("effective_delay_minutes") or 0,
        "followup_stage": data.get("followup_stage") or 0,
        "trigger_local_time": data.get("trigger_local_time") or "",
        "cancelled_reason": data.get("cancelled_reason") or "",
    }


def _milvus_summary(container_id: str) -> dict:
    env = docker_svc.memory_vector_env(container_id)
    status = docker_svc.get_container_status(docker_svc.MILVUS_CONTAINER)
    return {
        "container": status,
        "backend": env.get("HERMISS_MEMORY_VECTOR_BACKEND"),
        "collection": env.get("HERMISS_MILVUS_COLLECTION"),
        "namespace": env.get("HERMISS_MEMORY_NAMESPACE"),
    }


def _recent_errors(container_id: str) -> list[str]:
    logs = docker_svc.get_container_logs(container_id, tail=240)
    lines: list[str] = []
    for line in logs.splitlines():
        lower = line.lower()
        if any(key in lower for key in ("error", "failed", "traceback", "exception", "denied", "timeout")):
            cleaned = _redact_log_line(" ".join(line.strip().split()))
            if cleaned:
                lines.append(cleaned[:300])
    return lines[-20:]


def _redact_log_line(line: str) -> str:
    text = line or ""
    patterns = [
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1***"),
        (r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{12,}", r"\1***"),
        (r"(?i)((?:api[_-]?key|token|secret|password|weixin_token)\s*[:=]\s*)[^\s,;]+", r"\1***"),
        (r"sk-[A-Za-z0-9_\-]{8,}", "sk-***"),
        (r"ghp_[A-Za-z0-9_]{8,}", "ghp_***"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _gateway_state(container_id: str) -> dict:
    return _safe_json(docker_svc.read_file(container_id, f"{PROFILE_DIR}/gateway_state.json"))


def _runtime_env(container_id: str) -> dict[str, str]:
    return _parse_env(docker_svc.read_file(container_id, f"{PROFILE_DIR}/.env"))


def _runtime_config(container_id: str) -> dict:
    try:
        data = yaml.safe_load(docker_svc.read_file(container_id, f"{PROFILE_DIR}/config.yaml") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_runtime_summary(user, include_recent_errors: bool = True) -> dict:
    container_id = user.container_id or ""
    container = docker_svc.get_container_status(container_id) if container_id else {"status": "not_created"}
    env_values = _runtime_env(container_id) if container_id else {}
    config_values = _runtime_config(container_id) if container_id else {}
    gateway = _gateway_state(container_id) if container_id else {}
    weixin = (gateway.get("platforms") or {}).get("weixin") or {}
    model_config = config_values.get("model") if isinstance(config_values.get("model"), dict) else {}
    auxiliary_config = config_values.get("auxiliary") if isinstance(config_values.get("auxiliary"), dict) else {}
    vision_config = auxiliary_config.get("vision") if isinstance(auxiliary_config.get("vision"), dict) else {}

    model_key = (
        env_values.get("DEEPSEEK_API_KEY")
        or env_values.get("OPENAI_API_KEY")
        or env_values.get("CUSTOM_API_KEY")
        or ""
    )
    vision_key = env_values.get("VISION_API_KEY") or ""
    weixin_token = env_values.get("WEIXIN_TOKEN") or ""
    account_id = env_values.get("WEIXIN_ACCOUNT_ID") or user.wechat_account_id or ""
    platform_state = str(weixin.get("state") or "unknown")
    token_configured = bool(weixin_token)
    connected = bool(token_configured and platform_state == "connected")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container": container,
        "wechat": {
            "db_bound": bool(user.wechat_bound),
            "configured": token_configured or bool(account_id),
            "bound": token_configured,
            "source": "runtime" if token_configured else ("panel_db" if user.wechat_bound else "none"),
            "token_configured": token_configured,
            "token_length": len(weixin_token) if weixin_token else 0,
            "account_id": account_id,
            "gateway_state": gateway.get("gateway_state") or "unknown",
            "platform_state": platform_state,
            "connected": connected,
            "updated_at": weixin.get("updated_at") or gateway.get("updated_at") or "",
            "error": str(weixin.get("error_message") or weixin.get("error_code") or ""),
            "inconsistent": bool(token_configured != bool(user.wechat_bound)),
        },
        "model": {
            "provider": model_config.get("provider") or user.model_provider or env_values.get("MODEL_PROVIDER") or "deepseek",
            "model": model_config.get("default") or user.model_name or env_values.get("MODEL") or "",
            "api_key_configured": bool(model_key),
            "api_key_length": len(model_key) if model_key else 0,
            "base_url_configured": bool(env_values.get("CUSTOM_BASE_URL")),
        },
        "vision": {
            "provider": vision_config.get("provider") or env_values.get("VISION_PROVIDER") or "",
            "model": vision_config.get("model") or env_values.get("VISION_MODEL") or "",
            "api_key_configured": bool(vision_key),
            "api_key_length": len(vision_key) if vision_key else 0,
            "base_url_configured": bool(env_values.get("VISION_BASE_URL")),
        },
        "memory": _memory_summary(container_id) if container_id else {},
        "milvus": _milvus_summary(container_id) if container_id else {},
        "plugins": {
            "message_analyzer": _plugin_manifest(container_id, "message-analyzer") if container_id else {},
            "sticker_sender": _plugin_manifest(container_id, "sticker-sender") if container_id else {},
        },
        "persona_files": {
            "soul": _file_meta(container_id, f"{PROFILE_DIR}/SOUL.md") if container_id else {},
            "user": _file_meta(container_id, f"{PROFILE_DIR}/memories/USER.md") if container_id else {},
        },
        "active_checkin": _active_checkin(container_id) if container_id else {},
    }
    if include_recent_errors:
        summary["recent_errors"] = (
            _recent_errors(container_id)
            if container_id and container.get("status") != "not_created"
            else []
        )
    return summary


def build_wechat_health(user, include_log: bool = False) -> dict:
    summary = build_runtime_summary(user, include_recent_errors=False)
    health = dict(summary.get("wechat") or {})
    health["container_status"] = (summary.get("container") or {}).get("status") or "unknown"
    if include_log and user.container_id:
        log = docker_svc.read_file(user.container_id, f"{PROFILE_DIR}/logs/gateway.log")
        interesting = []
        for line in log.splitlines()[-160:]:
            lower = line.lower()
            if any(key in lower for key in ("weixin", "ilink", "session", "token", "error", "connected", "disconnected")):
                interesting.append(line)
        health["log"] = "\n".join(interesting[-30:]) or "暂无微信连接日志"
    return health
