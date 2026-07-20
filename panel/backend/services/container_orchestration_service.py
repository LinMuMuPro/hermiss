# backend/services/container_orchestration_service.py
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import docker

from config import DOCKER_IMAGE
from services.container_runtime_service import restart_container
from services.docker_client import get_docker_client
from services.hermiss_config_service import update_env
from services.hermiss_profile_service import (
    check_memory_vector_dependencies,
    install_default_persona,
    install_message_analyzer_plugin,
)
from services.image_update_service import check_image_update, local_image_info, pull_image
from services.milvus_service import (
    HERMISS_NETWORK,
    connect_container_to_network,
    ensure_milvus_service,
    ensure_network,
    memory_vector_env,
)

PANEL_IMAGE = os.getenv("PANEL_IMAGE", "ghcr.io/linmumupro/hermiss-panel:single")


def ensure_memory_vector_stack(container_name: str) -> None:
    try:
        ensure_milvus_service()
    except Exception as e:
        print(f"[panel] ensure milvus failed: {e}")
    try:
        connect_container_to_network(container_name)
    except Exception as e:
        print(f"[panel] connect {container_name} to {HERMISS_NETWORK} failed: {e}")
    try:
        update_env(container_name, memory_vector_env(container_name))
    except Exception as e:
        print(f"[panel] update vector env failed for {container_name}: {e}")
    check_memory_vector_dependencies(container_name)
    install_message_analyzer_plugin(container_name)


def create_container(container_id: str, panel_port: int) -> dict:
    container_name = container_id
    volume_name = f"hermiss-data-{container_id}"
    ensure_network()
    ensure_milvus_service()

    client = get_docker_client()
    try:
        existing = client.containers.get(container_name)
        ensure_memory_vector_stack(container_name)
        return {"container_name": container_name, "panel_port": panel_port, "status": existing.status}
    except docker.errors.NotFound:
        pass

    try:
        client.volumes.get(volume_name)
    except docker.errors.NotFound:
        client.volumes.create(volume_name)

    container = client.containers.run(
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
            "HERMES_GATEWAY_BUSY_TEXT_HARD_CAP_SECONDS": "3.0",
            "WEIXIN_TEXT_BATCH_DELAY_SECONDS": "3.0",
            "WEIXIN_TEXT_BATCH_SPLIT_DELAY_SECONDS": "3.0",
            "HERMISS_PROACTIVE_CHECKIN_ENABLED": "true",
            **memory_vector_env(container_name),
        },
        network=HERMISS_NETWORK,
        restart_policy={"Name": "unless-stopped"},
    )
    install_default_persona(container_name)
    ensure_memory_vector_stack(container_name)
    restart_container(container_name)
    return {"container_name": container_name, "panel_port": panel_port, "status": container.status}


def check_updates() -> dict:
    targets = {
        "panel": PANEL_IMAGE,
        "runtime": DOCKER_IMAGE,
    }
    results = {}
    timeout = int(os.getenv("HERMISS_UPDATE_CHECK_TIMEOUT", "18"))
    executor = ThreadPoolExecutor(max_workers=3)
    futures = {key: executor.submit(check_image_update, image) for key, image in targets.items()}
    try:
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=timeout)
            except TimeoutError:
                results[key] = {
                    "image": targets[key],
                    "local": local_image_info(targets[key]),
                    "remote_digest": "",
                    "update_available": None,
                    "error": "检查更新超时，请检查 Docker 网络或代理。",
                }
            except Exception as exc:
                results[key] = {
                    "image": targets[key],
                    "local": local_image_info(targets[key]),
                    "remote_digest": "",
                    "update_available": None,
                    "error": str(exc),
                }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


def update_runtime_container(container_name: str, panel_port: int) -> dict:
    pull_info = pull_image(DOCKER_IMAGE)
    try:
        existing = get_docker_client().containers.get(container_name)
        existing.stop(timeout=10)
        existing.remove(v=False, force=True)
    except docker.errors.NotFound:
        pass
    created = create_container(container_name, panel_port)
    return {"image": pull_info, "container": created}


def mounts_to_volumes(container) -> dict:
    volumes = {}
    for mount in container.attrs.get("Mounts") or []:
        source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
        target = mount.get("Destination")
        if source and target:
            volumes[source] = {
                "bind": target,
                "mode": "rw" if mount.get("RW", True) else "ro",
            }
    return volumes


def ports_for_recreate(container) -> dict:
    ports = {}
    for container_port, bindings in (container.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
        if not bindings:
            continue
        binding = bindings[0]
        ports[container_port] = (
            binding.get("HostIp") or "127.0.0.1",
            int(binding.get("HostPort")),
        )
    return ports


def env_for_recreate(container) -> dict:
    env = {}
    for item in container.attrs.get("Config", {}).get("Env") or []:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    return env


def replace_panel_container() -> None:
    time.sleep(2)
    client = get_docker_client()
    current_id = os.getenv("HOSTNAME", "")
    current = client.containers.get(current_id)
    original_name = current.name
    old_name = f"{original_name}-old-{int(time.time())}"
    ports = ports_for_recreate(current)
    volumes = mounts_to_volumes(current)
    environment = env_for_recreate(current)
    labels = current.attrs.get("Config", {}).get("Labels") or {}
    restart_policy = current.attrs.get("HostConfig", {}).get("RestartPolicy") or {"Name": "unless-stopped"}
    networks = list((current.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys())
    network = networks[0] if networks else None

    current.rename(old_name)
    client.containers.run(
        image=PANEL_IMAGE,
        name=original_name,
        detach=True,
        ports=ports,
        volumes=volumes,
        environment=environment,
        labels=labels,
        network=network,
        restart_policy=restart_policy,
    )
    try:
        current.stop(timeout=3)
    finally:
        try:
            current.remove(v=False, force=True)
        except Exception:
            pass


def schedule_panel_self_update() -> dict:
    pull_info = pull_image(PANEL_IMAGE)
    thread = threading.Thread(target=replace_panel_container, daemon=True)
    thread.start()
    return {"image": pull_info, "scheduled": True}
