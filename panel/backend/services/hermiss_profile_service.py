# backend/services/hermiss_profile_service.py
from pathlib import Path

from services.runtime_file_service import copy_dir_to_container, exec_in_container, install_plugin_dir, write_file

PROFILE_DIR = "/root/.hermes/profiles/hermiss"
MESSAGE_ANALYZER_PLUGIN_DIR = f"{PROFILE_DIR}/plugins/message-analyzer"
SESSION_RESET_HOOK_DIR = "/root/.hermes/hooks/hermiss-session-reset"


def check_memory_vector_dependencies(container_name: str) -> None:
    result = exec_in_container(
        container_name,
        "PYTHON_BIN=/usr/local/lib/hermes-agent/venv/bin/python3\n"
        "[ -x \"$PYTHON_BIN\" ] || PYTHON_BIN=python3\n"
        "$PYTHON_BIN - <<'PY'\n"
        "import pymilvus  # noqa: F401\n"
        "print('OK')\n"
        "PY",
        timeout=30,
    )
    if result.get("exit_code", 1) != 0:
        print(
            f"[panel] pymilvus missing in {container_name}; "
            "please rebuild/pull a Hermiss image with vector dependencies preinstalled."
        )


def install_message_analyzer_plugin(container_name: str, backend_root: Path | None = None) -> None:
    root = backend_root or Path(__file__).resolve().parents[1]
    resource_dir = root / "resources" / "message-analyzer"
    if resource_dir.exists():
        result = install_plugin_dir(
            container_name,
            str(resource_dir),
            MESSAGE_ANALYZER_PLUGIN_DIR,
        )
        print(f"[panel] message-analyzer plugin {result['status']}: {result.get('version') or 'unknown'}")
    hook_dir = root / "resources" / "session-reset-hook"
    if hook_dir.exists():
        copy_dir_to_container(
            container_name,
            str(hook_dir),
            SESSION_RESET_HOOK_DIR,
        )


def install_default_persona(container_name: str, backend_root: Path | None = None) -> None:
    root = backend_root or Path(__file__).resolve().parents[1]
    resource_dir = root / "resources" / "default_persona"
    soul_path = resource_dir / "SOUL.md"
    user_path = resource_dir / "USER.md"
    exec_in_container(
        container_name,
        f"mkdir -p {PROFILE_DIR}/memories",
        timeout=5,
    )
    if soul_path.exists():
        write_file(
            container_name,
            f"{PROFILE_DIR}/SOUL.md",
            soul_path.read_text(encoding="utf-8"),
        )
    if user_path.exists():
        write_file(
            container_name,
            f"{PROFILE_DIR}/memories/USER.md",
            user_path.read_text(encoding="utf-8"),
        )
