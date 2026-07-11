# backend/services/docker_service.py
# ── 单个 Docker 操作入口，所有路由只调这个文件 ──
import docker
import io
import re
import shlex
import tarfile
import time
from pathlib import Path
from typing import Optional
from config import DOCKER_IMAGE

_client = None
_server_stats_cache = {"ts": 0.0, "data": None}
SERVER_STATS_CACHE_SECONDS = 5
HERMISS_NETWORK = "hermiss-net"
MILVUS_CONTAINER = "hermiss-milvus"
MILVUS_IMAGE = "milvusdb/milvus:v2.4.0"
MILVUS_VOLUME = "hermiss-milvus-data"
MEMORY_VECTOR_ENV = {
    "HERMISS_MEMORY_VECTOR_BACKEND": "milvus",
    "HERMISS_MEMORY_VECTOR_ENABLED": "true",
    "HERMISS_MILVUS_HOST": MILVUS_CONTAINER,
    "HERMISS_MILVUS_PORT": "19530",
    "HERMISS_MILVUS_SYNC_ON_START": "true",
}

def _get_client():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _ensure_network():
    client = _get_client()
    try:
        return client.networks.get(HERMISS_NETWORK)
    except docker.errors.NotFound:
        return client.networks.create(HERMISS_NETWORK, driver="bridge")


def _safe_milvus_collection(container_name: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", container_name or "default")[:80] or "default"
    if not re.match(r"^[A-Za-z_]", safe_name):
        safe_name = f"u_{safe_name}"
    return f"hermiss_memories_{safe_name}"


def memory_vector_env(container_name: str) -> dict:
    return {
        **MEMORY_VECTOR_ENV,
        "HERMISS_MEMORY_NAMESPACE": container_name,
        "HERMISS_MILVUS_COLLECTION": _safe_milvus_collection(container_name),
    }


def ensure_milvus_service() -> dict:
    client = _get_client()
    network = _ensure_network()
    try:
        existing = client.containers.get(MILVUS_CONTAINER)
        if existing.status != "running":
            existing.start()
        try:
            network.connect(existing)
        except Exception:
            pass
        return {"name": MILVUS_CONTAINER, "status": existing.status}
    except docker.errors.NotFound:
        pass

    try:
        client.volumes.get(MILVUS_VOLUME)
    except docker.errors.NotFound:
        client.volumes.create(MILVUS_VOLUME)

    container = client.containers.run(
        image=MILVUS_IMAGE,
        name=MILVUS_CONTAINER,
        detach=True,
        command=["milvus", "run", "standalone"],
        environment={
            "ETCD_USE_EMBED": "true",
            "COMMON_STORAGETYPE": "local",
        },
        volumes={MILVUS_VOLUME: {"bind": "/var/lib/milvus", "mode": "rw"}},
        ports={
            "19530/tcp": ("127.0.0.1", 19530),
            "9091/tcp": ("127.0.0.1", 9091),
        },
        network=HERMISS_NETWORK,
        restart_policy={"Name": "unless-stopped"},
    )
    return {"name": MILVUS_CONTAINER, "status": container.status}


def _install_memory_vector_dependencies(container_name: str) -> None:
    result = exec_in_container(
        container_name,
        "PYTHON_BIN=/usr/local/lib/hermes-agent/venv/bin/python3\n"
        "[ -x \"$PYTHON_BIN\" ] || PYTHON_BIN=python3\n"
        "$PYTHON_BIN - <<'PY'\n"
        "import subprocess, sys\n"
        "try:\n"
        "    import pkg_resources  # noqa: F401\n"
        "    import pymilvus  # noqa: F401\n"
        "except Exception:\n"
        "    subprocess.check_call([\n"
        "        sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--force-reinstall',\n"
        "        'setuptools<81', 'protobuf==5.29.5', 'grpcio==1.67.1', 'pymilvus==2.4.10'\n"
        "    ])\n"
        "print('OK', sys.executable)\n"
        "PY",
        timeout=180,
    )
    if result.get("exit_code", 1) != 0:
        print(f"[panel] install pymilvus failed for {container_name}: {result.get('output', '')}")


def _install_message_analyzer_plugin(container_name: str) -> None:
    resource_dir = Path(__file__).resolve().parents[1] / "resources" / "message-analyzer"
    if resource_dir.exists():
        copy_dir_to_container(
            container_name,
            str(resource_dir),
            "/root/.hermes/profiles/hermiss/plugins/message-analyzer",
        )


def _install_default_persona(container_name: str) -> None:
    resource_dir = Path(__file__).resolve().parents[1] / "resources" / "default_persona"
    soul_path = resource_dir / "SOUL.md"
    user_path = resource_dir / "USER.md"
    exec_in_container(
        container_name,
        "mkdir -p /root/.hermes/profiles/hermiss/memories",
        timeout=5,
    )
    if soul_path.exists():
        write_file(
            container_name,
            "/root/.hermes/profiles/hermiss/SOUL.md",
            soul_path.read_text(encoding="utf-8"),
        )
    if user_path.exists():
        write_file(
            container_name,
            "/root/.hermes/profiles/hermiss/memories/USER.md",
            user_path.read_text(encoding="utf-8"),
        )


def ensure_memory_vector_stack(container_name: str) -> None:
    try:
        ensure_milvus_service()
    except Exception as e:
        print(f"[panel] ensure milvus failed: {e}")
    try:
        network = _ensure_network()
        container = _get_client().containers.get(container_name)
        try:
            network.connect(container)
        except Exception:
            pass
    except Exception as e:
        print(f"[panel] connect {container_name} to {HERMISS_NETWORK} failed: {e}")
    try:
        update_env(container_name, memory_vector_env(container_name))
    except Exception as e:
        print(f"[panel] update vector env failed for {container_name}: {e}")
    _install_memory_vector_dependencies(container_name)
    _install_message_analyzer_plugin(container_name)


# ═══════════════════════════════════════════
# 容器生命周期
# ═══════════════════════════════════════════

def create_container(container_id: str, panel_port: int) -> dict:
    container_name = container_id
    volume_name = f"hermiss-data-{container_id}"
    _ensure_network()
    ensure_milvus_service()

    try:
        existing = _get_client().containers.get(container_name)
        ensure_memory_vector_stack(container_name)
        return {"container_name": container_name, "panel_port": panel_port, "status": existing.status}
    except docker.errors.NotFound:
        pass

    try: _get_client().volumes.get(volume_name)
    except docker.errors.NotFound: _get_client().volumes.create(volume_name)

    container = _get_client().containers.run(
        image=DOCKER_IMAGE,
        name=container_name,
        detach=True,
        entrypoint="/opt/hermes/entrypoint.sh",
        ports={"8765/tcp": ("127.0.0.1", panel_port)},
        volumes={volume_name: {"bind": "/root/.hermes", "mode": "rw"}},
        environment={
            "HERMES_PROFILE": "hermiss",
            "HERMES_PANEL_PORT": str(panel_port),
            "HERMES_GATEWAY_BUSY_TEXT_MODE": "queue",
            "HERMES_GATEWAY_BUSY_TEXT_DEBOUNCE_SECONDS": "3.0",
            "HERMES_GATEWAY_BUSY_TEXT_HARD_CAP_SECONDS": "6.0",
            "WEIXIN_TEXT_BATCH_DELAY_SECONDS": "6.0",
            "WEIXIN_TEXT_BATCH_SPLIT_DELAY_SECONDS": "8.0",
            "HERMISS_PROACTIVE_CHECKIN_ENABLED": "true",
            **memory_vector_env(container_name),
        },
        network=HERMISS_NETWORK,
        restart_policy={"Name": "unless-stopped"},
    )
    _install_default_persona(container_name)
    ensure_memory_vector_stack(container_name)
    restart_container(container_name)
    return {"container_name": container_name, "panel_port": panel_port, "status": container.status}


def delete_container(container_name: str) -> bool:
    import subprocess
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(["docker", "volume", "rm", "-f", f"hermiss-data-{container_name}"], capture_output=True)
    return True


def restart_container(container_name: str) -> Optional[str]:
    try:
        c = _get_client().containers.get(container_name)
        c.restart(timeout=1)
        return c.status
    except docker.errors.NotFound:
        return None


def stop_container(container_name: str) -> Optional[str]:
    try:
        c = _get_client().containers.get(container_name)
        c.stop(timeout=5)
        return c.status
    except Exception:
        return None


def start_container(container_name: str) -> Optional[str]:
    try:
        c = _get_client().containers.get(container_name)
        c.start()
        return c.status
    except Exception:
        return None


# ═══════════════════════════════════════════
# 文件操作（统一用 base64，不依赖 shell 转义）
# ═══════════════════════════════════════════

def read_file(container_name: str, path: str) -> str:
    result = exec_in_container(container_name, f"cat {shlex.quote(path)} 2>/dev/null || echo ''")
    return result.get("output", "")


def write_file(container_name: str, path: str, content: str) -> None:
    import base64
    enc = base64.b64encode(content.encode()).decode()
    exec_in_container(
        container_name,
        f"printf %s {shlex.quote(enc)} | base64 -d > {shlex.quote(path)}",
        timeout=5,
    )


def copy_dir_to_container(container_name: str, local_dir: str, target_dir: str) -> None:
    """Copy a local directory into a container, replacing the target directory."""
    from pathlib import Path

    source = Path(local_dir)
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"目录不存在: {local_dir}")

    parent = target_dir.rstrip("/").rsplit("/", 1)[0] or "/"
    name = target_dir.rstrip("/").rsplit("/", 1)[-1]
    container = _get_client().containers.get(container_name)

    exec_in_container(
        container_name,
        f"mkdir -p {shlex.quote(parent)} && rm -rf {shlex.quote(target_dir)}",
        timeout=10,
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in source.rglob("*"):
            arcname = f"{name}/{path.relative_to(source)}"
            tar.add(path, arcname=arcname)
    buffer.seek(0)
    container.put_archive(parent, buffer.getvalue())


def update_env(container_name: str, updates: dict) -> None:
    """合并式更新 .env，保留已有的其他行"""
    env_path = f"/root/.hermes/profiles/hermiss/.env"
    current = read_file(container_name, env_path)
    lines = current.strip().split(chr(10)) if current.strip() else []

    updated, found_keys = [], set()
    for line in lines:
        matched = False
        for key, val in updates.items():
            if line.startswith(f"{key}="):
                updated.append(f"{key}={val}")
                found_keys.add(key)
                matched = True
                break
        if not matched:
            updated.append(line)
    for key, val in updates.items():
        if key not in found_keys:
            updated.append(f"{key}={val}")

    new_env = chr(10).join(updated).strip() + chr(10)
    write_file(container_name, env_path, new_env)


# ═══════════════════════════════════════════
# Config.yaml 更新
# ═══════════════════════════════════════════

def ensure_custom_provider(container_name: str, base_url: str, key_env_var: str) -> str:
    """
    确保 custom_providers 中有对应 base_url 的条目。
    返回 provider name（从 hostname 提取），用于 custom:<name>。
    """
    # 从 base_url 提取 hostname 生成唯一 name
    import re
    try:
        host = base_url.split("://")[1].split("/")[0].split(":")[0]
        parts = host.split(".")
        name = "-".join(parts[:-1]) if len(parts) > 1 else parts[0]
    except:
        name = "custom"
    name = re.sub(r'[^a-z0-9\-]', '', name.lower())[:32]

    import base64
    script = f"""
import re
path = '/root/.hermes/profiles/hermiss/config.yaml'
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


def update_config_model(container_name: str, provider: str, model: str, base_url: str) -> bool:
    """用 hermes config set 更新 model 段"""
    ok = True
    for key, val in [("model.default", model), ("model.provider", provider)]:
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


# ═══════════════════════════════════════════
# 查询/日志
# ═══════════════════════════════════════════

def exec_in_container(container_name: str, command: str, timeout: int = 30) -> dict:
    try:
        exit_code, output = _get_client().containers.get(container_name).exec_run(
            cmd=["bash", "-c", command], tty=False, demux=False
        )
        return {"output": output.decode("utf-8", errors="replace") if output else "", "exit_code": exit_code}
    except docker.errors.NotFound:
        return {"output": "", "error": "容器不存在"}
    except Exception as e:
        return {"output": "", "error": str(e)}


def get_container_status(container_name: str) -> dict:
    try:
        c = _get_client().containers.get(container_name)
        return {"name": c.name, "status": c.status, "image": c.image.tags[0] if c.image.tags else "unknown"}
    except docker.errors.NotFound:
        return {"name": container_name, "status": "not_created"}


def get_container_logs(container_name: str, tail: int = 50) -> str:
    try:
        return _get_client().containers.get(container_name).logs(tail=tail).decode("utf-8", errors="replace")
    except docker.errors.NotFound:
        return "容器不存在"


def get_server_stats() -> dict:
    import json
    import subprocess
    now = time.time()
    cached = _server_stats_cache.get("data")
    if cached and now - float(_server_stats_cache.get("ts") or 0) < SERVER_STATS_CACHE_SECONDS:
        return cached

    disk = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    dps = disk.stdout.strip().split(chr(10))[-1].split()
    mem = subprocess.run(["free", "-m"], capture_output=True, text=True)
    ms = mem.stdout.strip().split(chr(10))
    raw = ms[1].split() if len(ms) > 1 and len(ms[1].split()) > 6 else ["?"] * 7
    # free -m 返回 MB 整数，统一转为 GB 避免前端 parseGi 误解析 Mi 后缀
    if raw[0] != "?":
        raw[1] = f"{int(raw[1])/1024:.1f}G"
        raw[2] = f"{int(raw[2])/1024:.1f}G"
        raw[6] = f"{int(raw[6])/1024:.1f}G"
    mp = raw
    load = subprocess.run(["uptime"], capture_output=True, text=True)
    ld = load.stdout.split("load average:")[-1].strip() if "load average" in load.stdout else "?"

    live_stats = {}
    try:
        sr = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=4
        )
        for line in sr.stdout.strip().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            name = item.get("Name") or item.get("Container") or item.get("ID")
            if not name:
                continue
            live_stats[name] = {
                "cpu": item.get("CPUPerc") or "-",
                "memory": item.get("MemUsage") or "-",
                "memory_percent": item.get("MemPerc") or "-",
                "net_io": item.get("NetIO") or "-",
                "block_io": item.get("BlockIO") or "-",
                "pids": item.get("PIDs") or "-",
            }
    except Exception:
        live_stats = {}

    containers = []
    try:
        cr = subprocess.run(
            ["docker", "ps", "-a", "--size", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=2
        )
        for line in cr.stdout.strip().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            status_text = str(item.get("Status") or "")
            if status_text.lower().startswith("up"):
                status = "running"
            elif status_text.lower().startswith("exited"):
                status = "exited"
            elif status_text:
                status = status_text.split()[0].lower()
            else:
                status = "unknown"
            stats = live_stats.get(item.get("Names") or item.get("Name") or "?") or {}
            containers.append({
                "name": item.get("Names") or item.get("Name") or "?",
                "status": status,
                "image": item.get("Image") or "?",
                "ports": item.get("Ports") or "-",
                "size": item.get("Size") or "-",
                "cpu": stats.get("cpu", "-"),
                "memory": stats.get("memory", "-"),
                "memory_percent": stats.get("memory_percent", "-"),
                "net_io": stats.get("net_io", "-"),
                "block_io": stats.get("block_io", "-"),
                "pids": stats.get("pids", "-"),
            })
    except: pass

    data = {
        "disk": {"used": dps[2] if len(dps)>2 else "?", "avail": dps[3] if len(dps)>3 else "?", "pct": dps[4] if len(dps)>4 else "?"},
        "memory": {"total": mp[1] if len(mp)>0 else "?", "used": mp[2] if len(mp)>1 else "?", "avail": mp[6] if len(mp)>6 else "?"},
        "load": ld, "containers": containers,
    }
    _server_stats_cache["ts"] = now
    _server_stats_cache["data"] = data
    return data
