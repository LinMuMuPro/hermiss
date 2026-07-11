# backend/routers/cron.py
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session

from models.user import User
from config import MOCK_MODE
from routers.auth import get_current_user
from dependencies import get_db
from services import docker_service as docker_svc

router = APIRouter(prefix="/api/cron", tags=["cron"])


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "无效的认证头")
    return authorization[7:]


def _check_container(user: User):
    if MOCK_MODE:
        return
    if not user.container_id or user.container_status not in ("running", "created"):
        raise HTTPException(400, "请先创建并启动容器")


@router.get("/list")
def list_cron(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """获取定时任务列表"""
    user = get_current_user(token, db)
    _check_container(user)

    result = docker_svc.exec_in_container(
        user.container_id,
        "hermes --profile hermiss cron list 2>&1",
        timeout=10,
    )
    return {"output": result.get("output", "")}


@router.post("/cancel/{job_id}")
def cancel_cron(
    job_id: str,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """取消定时任务"""
    user = get_current_user(token, db)
    _check_container(user)

    result = docker_svc.exec_in_container(
        user.container_id,
        f"hermes --profile hermiss cron remove {job_id} 2>&1",
        timeout=10,
    )
    return {"output": result.get("output", "")}
