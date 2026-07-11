from sqlalchemy.orm import Session

from config import MOCK_MODE, SINGLE_USER_CONTAINER, SINGLE_USER_CONTAINER_PORT
from models.user import User
from services import docker_service as docker_svc


def ensure_single_container(user: User, db: Session) -> None:
    if MOCK_MODE:
        user.container_id = SINGLE_USER_CONTAINER
        user.panel_port = SINGLE_USER_CONTAINER_PORT
        user.container_status = "running"
        db.commit()
        return

    if user.container_id != SINGLE_USER_CONTAINER:
        user.container_id = SINGLE_USER_CONTAINER
    user.panel_port = SINGLE_USER_CONTAINER_PORT

    result = docker_svc.create_container(SINGLE_USER_CONTAINER, SINGLE_USER_CONTAINER_PORT)
    user.container_status = result.get("status") or "running"
    db.commit()
