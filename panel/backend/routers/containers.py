# backend/routers/containers.py
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from models.user import User
from routers.auth import get_current_user
from dependencies import get_db
from config import MOCK_MODE
from services import docker_service as docker_svc
from single_runtime import ensure_single_container

router = APIRouter(prefix="/api/container", tags=["container"])


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    return authorization[7:]


def _require_admin(user: User):
    if not user.is_admin:
        raise HTTPException(403, "仅管理员可操作容器")


class ContainerInfo(BaseModel):
    name: str = ""; status: str = "not_created"


@router.get("/status")
def get_status(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    ensure_single_container(user, db)
    if MOCK_MODE or not user.container_id:
        return ContainerInfo(name=f"hermes-{user.container_id or 'pending'}", status=user.container_status or "pending")

    result = docker_svc.get_container_status(user.container_id)
    return ContainerInfo(name=result.get("name",""), status=result.get("status","unknown"))


@router.post("/start")
def start_container(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE: return {"status": "running"}
    ensure_single_container(user, db)
    docker_svc.start_container(user.container_id)
    user.container_status = "running"; db.commit()
    return {"status": "running"}


@router.post("/stop")
def stop_container(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE: return {"status": "stopped"}
    docker_svc.stop_container(user.container_id)
    user.container_status = "stopped"; db.commit()
    return {"status": "stopped"}


@router.post("/restart")
def restart_container(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE: return {"status": "running"}
    ensure_single_container(user, db)
    docker_svc.restart_container(user.container_id)
    user.container_status = "running"; db.commit()
    return {"status": "running"}


@router.get("/logs")
def get_logs(tail: int = 50, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE or not user.container_id: return {"logs": "[MOCK] — 容器未运行"}
    logs = docker_svc.get_container_logs(user.container_id, tail=tail)
    return {"logs": logs}


@router.get("/server-stats")
def server_stats(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    return docker_svc.get_server_stats()
