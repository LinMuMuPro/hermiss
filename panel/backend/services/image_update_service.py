import docker

from services.docker_client import get_docker_client


def short_image_id(image_obj) -> str:
    image_id = getattr(image_obj, "id", "") or ""
    return image_id.replace("sha256:", "")[:12]


def remote_digest(image: str) -> str:
    data = get_docker_client().images.get_registry_data(image)
    descriptor = data.attrs.get("Descriptor") or {}
    return descriptor.get("digest") or data.id or ""


def local_image_info(image: str) -> dict:
    try:
        local = get_docker_client().images.get(image)
    except docker.errors.ImageNotFound:
        return {"present": False, "id": "", "repo_digests": []}
    return {
        "present": True,
        "id": short_image_id(local),
        "repo_digests": local.attrs.get("RepoDigests") or [],
    }


def check_image_update(image: str) -> dict:
    local = local_image_info(image)
    try:
        remote = remote_digest(image)
    except Exception as exc:
        return {
            "image": image,
            "local": local,
            "remote_digest": "",
            "update_available": None,
            "error": str(exc),
        }
    local_digests = local.get("repo_digests") or []
    update_available = True
    if remote and any(d.endswith(f"@{remote}") or d.endswith(remote) for d in local_digests):
        update_available = False
    elif not local.get("present"):
        update_available = True
    return {
        "image": image,
        "local": local,
        "remote_digest": remote,
        "update_available": update_available,
        "error": "",
    }


def pull_image(image: str) -> dict:
    pulled = get_docker_client().images.pull(image)
    return {
        "image": image,
        "id": short_image_id(pulled),
        "repo_digests": pulled.attrs.get("RepoDigests") or [],
    }
