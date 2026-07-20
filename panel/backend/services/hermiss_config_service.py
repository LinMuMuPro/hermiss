# backend/services/hermiss_config_service.py
import base64
import re
import shlex

from services.runtime_file_service import exec_in_container, read_file, write_file

PROFILE_DIR = "/root/.hermes/profiles/hermiss"
ENV_PATH = f"{PROFILE_DIR}/.env"
CONFIG_PATH = f"{PROFILE_DIR}/config.yaml"


def clean_env_key(key: str) -> str:
    key = str(key or "").strip()
    if not re.fullmatch(r"[A-Z0-9_]+", key):
        raise ValueError(f"invalid env key: {key!r}")
    return key


def clean_env_value(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\r", "").replace("\n", "").strip()


def update_env(container_name: str, updates: dict) -> None:
    """Merge updates into the profile .env without allowing malformed lines."""
    normalized = {clean_env_key(key): clean_env_value(value) for key, value in updates.items()}
    current = read_file(container_name, ENV_PATH)
    lines = current.strip().split(chr(10)) if current.strip() else []

    updated, found_keys = [], set()
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in normalized:
            updated.append(f"{key}={normalized[key]}")
            found_keys.add(key)
        else:
            updated.append(line)
    for key, val in normalized.items():
        if key not in found_keys:
            updated.append(f"{key}={val}")

    new_env = chr(10).join(updated).strip() + chr(10)
    write_file(container_name, ENV_PATH, new_env)


def ensure_custom_provider(container_name: str, base_url: str, key_env_var: str) -> str:
    """
    确保 custom_providers 中有对应 base_url 的条目。
    返回 provider name（从 hostname 提取），用于 custom:<name>。
    """
    try:
        host = base_url.split("://")[1].split("/")[0].split(":")[0]
        parts = host.split(".")
        name = "-".join(parts[:-1]) if len(parts) > 1 else parts[0]
    except Exception:
        name = "custom"
    name = re.sub(r"[^a-z0-9\\-]", "", name.lower())[:32]

    script = f"""
import re
path = {CONFIG_PATH!r}
with open(path) as f:
    content = f.read()

name = {name!r}
base_url = {base_url!r}
key_ref = {('${' + key_env_var + '}')!r}

if name not in content:
    idx = content.find('custom_providers:')
    if idx >= 0:
        end = content.find(chr(10)+'plugins:', idx)
        if end < 0:
            end = len(content)
        entry = chr(10)+'- name: ' + name + chr(10) + '  base_url: ' + base_url + chr(10) + '  api_key: ' + key_ref + chr(10)
        content = content[:end] + entry + content[end:]
    else:
        content += chr(10)+'custom_providers:' + chr(10) + '- name: ' + name + chr(10) + '  base_url: ' + base_url + chr(10) + '  api_key: ' + key_ref + chr(10)

with open(path, 'w') as f:
    f.write(content)
print('OK')
"""
    b64 = base64.b64encode(script.encode()).decode()
    result = exec_in_container(container_name, f"printf %s {shlex.quote(b64)} | base64 -d | python3 2>&1")
    if result.get("exit_code", 1) != 0 or "OK" not in result.get("output", ""):
        print(f"[panel] ensure_custom_provider FAILED for {name}: {result}")
    return name


def update_config_model(container_name: str, provider: str, model: str, base_url: str, supports_vision: bool = True) -> bool:
    """用 hermes config set 更新 model 段"""
    ok = True
    for key, val in [
        ("model.default", model),
        ("model.provider", provider),
        ("model.supports_vision", "true" if supports_vision else "false"),
    ]:
        result = exec_in_container(
            container_name,
            f"hermiss --profile hermiss config set {shlex.quote(key)} {shlex.quote(val)} 2>&1",
        )
        if result.get("exit_code", 1) != 0:
            ok = False
    if base_url:
        result = exec_in_container(
            container_name,
            f"hermiss --profile hermiss config set model.base_url {shlex.quote(base_url)} 2>&1",
        )
        if result.get("exit_code", 1) != 0:
            ok = False
    return ok


def update_config_vision(container_name: str, provider: str, model: str, base_url: str = "") -> bool:
    """Sync panel vision settings into Hermes auxiliary.vision config."""
    clean_provider = (provider or "").strip()
    clean_model = (model or "").strip()
    clean_base_url = (base_url or "").strip().rstrip("/")

    if clean_provider.startswith(("http://", "https://")) and not clean_base_url:
        clean_base_url = clean_provider
        clean_provider = "custom"

    if clean_base_url:
        provider_name = ensure_custom_provider(container_name, clean_base_url, "VISION_API_KEY")
        config_provider = f"custom:{provider_name}"
    else:
        config_provider = clean_provider.lower()

    ok = True
    if config_provider:
        result = exec_in_container(
            container_name,
            f"hermiss --profile hermiss config set auxiliary.vision.provider {shlex.quote(config_provider)} 2>&1",
        )
        if result.get("exit_code", 1) != 0:
            ok = False
    if clean_model:
        result = exec_in_container(
            container_name,
            f"hermiss --profile hermiss config set auxiliary.vision.model {shlex.quote(clean_model)} 2>&1",
        )
        if result.get("exit_code", 1) != 0:
            ok = False
    return ok
