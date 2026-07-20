# backend/services/docker_service.py
# ── 兼容入口：路由仍调这个文件，具体职责已拆到独立 service ──
from typing import Optional

from services import container_orchestration_service as orchestration_svc
from services import container_runtime_service as runtime_svc
from services import hermiss_config_service as config_svc
from services.image_update_service import short_image_id
from services.milvus_service import MILVUS_CONTAINER, MILVUS_IMAGE, ensure_milvus_service, memory_vector_env
from services.runtime_file_service import exec_in_container, read_file, write_file


def ensure_memory_vector_stack(container_name: str) -> None:
    return orchestration_svc.ensure_memory_vector_stack(container_name)


def create_container(container_id: str, panel_port: int) -> dict:
    return orchestration_svc.create_container(container_id, panel_port)


def check_updates() -> dict:
    return orchestration_svc.check_updates()


def update_runtime_container(container_name: str, panel_port: int) -> dict:
    return orchestration_svc.update_runtime_container(container_name, panel_port)


def schedule_panel_self_update() -> dict:
    return orchestration_svc.schedule_panel_self_update()


def delete_container(container_name: str) -> bool:
    return runtime_svc.delete_container(container_name)


def restart_container(container_name: str) -> Optional[str]:
    return runtime_svc.restart_container(container_name)


def stop_container(container_name: str) -> Optional[str]:
    return runtime_svc.stop_container(container_name)


def start_container(container_name: str) -> Optional[str]:
    return runtime_svc.start_container(container_name)


def get_container_status(container_name: str) -> dict:
    return runtime_svc.get_container_status(container_name)


def get_container_logs(container_name: str, tail: int = 50) -> str:
    return runtime_svc.get_container_logs(container_name, tail=tail)


def get_server_stats() -> dict:
    return runtime_svc.get_server_stats()


def _clean_env_key(key: str) -> str:
    return config_svc.clean_env_key(key)


def _clean_env_value(value) -> str:
    return config_svc.clean_env_value(value)


def update_env(container_name: str, updates: dict) -> None:
    config_svc.update_env(container_name, updates)


def ensure_custom_provider(container_name: str, base_url: str, key_env_var: str) -> str:
    return config_svc.ensure_custom_provider(container_name, base_url, key_env_var)


def update_config_model(container_name: str, provider: str, model: str, base_url: str, supports_vision: bool = True) -> bool:
    return config_svc.update_config_model(container_name, provider, model, base_url, supports_vision=supports_vision)


def update_config_vision(container_name: str, provider: str, model: str, base_url: str = "") -> bool:
    return config_svc.update_config_vision(container_name, provider, model, base_url)
