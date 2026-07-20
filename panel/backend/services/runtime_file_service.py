import base64
import io
import json
import shlex
import tarfile
import uuid
from pathlib import Path

import docker

from services.docker_client import get_docker_client


def exec_in_container(container_name: str, command: str, timeout: int = 30) -> dict:
    try:
        exit_code, output = get_docker_client().containers.get(container_name).exec_run(
            cmd=["bash", "-c", command], tty=False, demux=False
        )
        return {"output": output.decode("utf-8", errors="replace") if output else "", "exit_code": exit_code}
    except docker.errors.NotFound:
        return {"output": "", "error": "容器不存在"}
    except Exception as e:
        return {"output": "", "error": str(e)}


def read_file(container_name: str, path: str) -> str:
    result = exec_in_container(container_name, f"cat {shlex.quote(path)} 2>/dev/null || echo ''")
    return result.get("output", "")


def write_file(container_name: str, path: str, content: str) -> None:
    encoded = base64.b64encode(content.encode()).decode()
    exec_in_container(
        container_name,
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}",
        timeout=5,
    )


def copy_dir_to_container(container_name: str, local_dir: str, target_dir: str) -> None:
    """Copy a local directory into a container, replacing the target directory.

    Generated Python caches are excluded so runtime plugins cannot keep stale
    bytecode after an update. The archive is unpacked into a temporary sibling
    directory and then moved into place.
    """
    source = Path(local_dir)
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"目录不存在: {local_dir}")

    parent = target_dir.rstrip("/").rsplit("/", 1)[0] or "/"
    name = target_dir.rstrip("/").rsplit("/", 1)[-1]
    tmp_name = f".{name}.tmp-{uuid.uuid4().hex[:10]}"
    tmp_dir = f"{parent.rstrip('/')}/{tmp_name}"
    container = get_docker_client().containers.get(container_name)

    exec_in_container(
        container_name,
        f"mkdir -p {shlex.quote(parent)} && rm -rf {shlex.quote(tmp_dir)}",
        timeout=10,
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in source.rglob("*"):
            rel = path.relative_to(source)
            if should_skip_archive_path(rel):
                continue
            tar.add(path, arcname=f"{tmp_name}/{rel}")
    buffer.seek(0)
    container.put_archive(parent, buffer.getvalue())
    exec_in_container(
        container_name,
        (
            "set -e; "
            f"test -d {shlex.quote(tmp_dir)}; "
            f"rm -rf {shlex.quote(target_dir)}; "
            f"mv {shlex.quote(tmp_dir)} {shlex.quote(target_dir)}; "
            f"find {shlex.quote(target_dir)} -name '__pycache__' -type d -prune -exec rm -rf {{}} + 2>/dev/null || true"
        ),
        timeout=10,
    )


def should_skip_archive_path(path: Path) -> bool:
    parts = set(path.parts)
    name = path.name
    return (
        "__pycache__" in parts
        or name.endswith((".pyc", ".pyo", ".tmp"))
        or name in {".DS_Store", "Thumbs.db"}
    )


def _read_local_manifest(resource_dir: Path) -> dict:
    manifest_path = resource_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_container_json(container_name: str, path: str) -> dict:
    raw = read_file(container_name, path)
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def install_plugin_dir(container_name: str, local_dir: str, target_dir: str, *, force: bool = False) -> dict:
    """Install a plugin resource directory into the runtime profile.

    If both source and target have matching ``manifest.json`` versions and the
    declared entry file exists in the target, the copy is skipped.
    """
    resource_dir = Path(local_dir)
    if not resource_dir.exists() or not resource_dir.is_dir():
        raise FileNotFoundError(f"Plugin directory not found: {local_dir}")

    local_manifest = _read_local_manifest(resource_dir)
    remote_manifest = _read_container_json(container_name, f"{target_dir.rstrip('/')}/manifest.json")
    entry = str(local_manifest.get("entry") or "__init__.py").strip() or "__init__.py"
    versions_match = (
        bool(local_manifest)
        and local_manifest.get("name") == remote_manifest.get("name")
        and local_manifest.get("version") == remote_manifest.get("version")
    )
    entry_check = exec_in_container(
        container_name,
        f"test -f {shlex.quote(target_dir.rstrip('/') + '/' + entry)}",
        timeout=5,
    )
    if not force and versions_match and entry_check.get("exit_code") == 0:
        return {
            "status": "skipped",
            "reason": "version_match",
            "name": local_manifest.get("name") or Path(target_dir).name,
            "version": local_manifest.get("version") or "",
            "target": target_dir,
        }

    copy_dir_to_container(container_name, str(resource_dir), target_dir)
    return {
        "status": "installed",
        "name": local_manifest.get("name") or Path(target_dir).name,
        "version": local_manifest.get("version") or "",
        "target": target_dir,
    }
