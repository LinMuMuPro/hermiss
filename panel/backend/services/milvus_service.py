# backend/services/milvus_service.py
import re

import docker

from services.docker_client import get_docker_client

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


def ensure_network():
    client = get_docker_client()
    try:
        return client.networks.get(HERMISS_NETWORK)
    except docker.errors.NotFound:
        return client.networks.create(HERMISS_NETWORK, driver="bridge")


def safe_milvus_collection(container_name: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", container_name or "default")[:80] or "default"
    if not re.match(r"^[A-Za-z_]", safe_name):
        safe_name = f"u_{safe_name}"
    return f"hermiss_memories_{safe_name}"


def memory_vector_env(container_name: str) -> dict:
    return {
        **MEMORY_VECTOR_ENV,
        "HERMISS_MEMORY_NAMESPACE": container_name,
        "HERMISS_MILVUS_COLLECTION": safe_milvus_collection(container_name),
    }


def ensure_milvus_service() -> dict:
    client = get_docker_client()
    network = ensure_network()
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


def connect_container_to_network(container_name: str) -> None:
    network = ensure_network()
    container = get_docker_client().containers.get(container_name)
    try:
        network.connect(container)
    except Exception:
        pass
