# backend/routers/cron.py
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session

from models.user import User
from config import MOCK_MODE, SINGLE_USER_CONTAINER
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
    if not (user.container_id or SINGLE_USER_CONTAINER):
        raise HTTPException(400, "请先创建并启动容器")


def _container_name(user: User) -> str:
    return user.container_id or SINGLE_USER_CONTAINER


def _read_json(container_name: str, path: str, default):
    result = docker_svc.exec_in_container(
        container_name,
        f"cat {path} 2>/dev/null || true",
        timeout=5,
    )
    raw = result.get("output") or ""
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _normalize_job(job: dict) -> dict:
    name = str(job.get("name") or "").strip()
    is_proactive = "HERMES PROACTIVE REPLY" in name or "active_checkin.json" in str(job.get("prompt") or "")
    return {
        "id": job.get("id") or "",
        "name": name.splitlines()[0] if name else "未命名任务",
        "type": "主动回访" if is_proactive else "定时任务",
        "state": job.get("state") or ("启用" if job.get("enabled", True) else "停用"),
        "enabled": bool(job.get("enabled", True)),
        "schedule": job.get("schedule_display") or (job.get("schedule") or {}).get("display") or "",
        "next_run_at": job.get("next_run_at") or "",
        "last_run_at": job.get("last_run_at") or "",
        "last_status": job.get("last_status") or "",
        "last_error": job.get("last_error") or job.get("last_delivery_error") or "",
        "deliver": job.get("deliver") or "",
        "repeat": job.get("repeat") or {},
        "is_proactive": is_proactive,
    }


def _active_checkin_status(active: dict | None) -> dict | None:
    if not isinstance(active, dict) or not active:
        return None
    return {
        "cancelled": bool(active.get("cancelled")),
        "checkin_id": active.get("checkin_id") or "",
        "job_id": active.get("job_id") or "",
        "job_ids": active.get("job_ids") or ([] if not active.get("job_id") else [active.get("job_id")]),
        "check_in_minutes": active.get("check_in_minutes") or active.get("effective_delay_minutes") or 0,
        "effective_delay": active.get("effective_delay") or "",
        "created_at": active.get("created_at") or "",
        "local_created_at": active.get("local_created_at") or "",
        "fire_at": active.get("fire_at") or "",
        "trigger_local_time": active.get("trigger_local_time") or "",
        "last_user_message_at": active.get("last_user_message_at") or "",
        "followup_stage": active.get("followup_stage") or 0,
        "max_followup_stage": active.get("max_followup_stage") or 3,
        "style_hint": active.get("style_hint") or "",
        "last_activity_hint": active.get("last_activity_hint") or "",
        "short_term_user_state": active.get("short_term_user_state"),
        "state_base": active.get("state_base"),
        "quiet_hour_delayed": bool(active.get("quiet_hour_delayed")),
    }


@router.get("/list")
def list_cron(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """获取定时任务列表"""
    user = get_current_user(token, db)
    _check_container(user)

    result = docker_svc.exec_in_container(
        _container_name(user),
        "hermes --profile hermiss cron list 2>&1",
        timeout=10,
    )
    return {"output": result.get("output", "")}


@router.get("/status")
def cron_status(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """获取结构化定时任务和主动回访状态"""
    user = get_current_user(token, db)
    _check_container(user)
    container_name = _container_name(user)

    jobs_data = _read_json(
        container_name,
        "/root/.hermes/profiles/hermiss/cron/jobs.json",
        {"jobs": []},
    )
    active_data = _read_json(
        container_name,
        "/root/.hermes/profiles/hermiss/reminders/active_checkin.json",
        None,
    )
    jobs = [_normalize_job(job) for job in jobs_data.get("jobs", []) if isinstance(job, dict)]
    jobs.sort(key=lambda item: item.get("next_run_at") or "")

    return {
        "container": container_name,
        "updated_at": datetime.now().isoformat(),
        "active_checkin": _active_checkin_status(active_data),
        "jobs": jobs,
        "raw_updated_at": jobs_data.get("updated_at", ""),
    }


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
        _container_name(user),
        f"hermes --profile hermiss cron remove {job_id} 2>&1",
        timeout=10,
    )
    return {"output": result.get("output", "")}


@router.post("/cancel-active")
def cancel_active_checkin(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """取消当前主动回访链"""
    user = get_current_user(token, db)
    _check_container(user)
    container_name = _container_name(user)
    active = _read_json(
        container_name,
        "/root/.hermes/profiles/hermiss/reminders/active_checkin.json",
        {},
    )
    job_ids = active.get("job_ids") or ([] if not active.get("job_id") else [active.get("job_id")])
    outputs = []
    for job_id in job_ids:
        if not job_id:
            continue
        result = docker_svc.exec_in_container(
            container_name,
            f"hermes --profile hermiss cron remove {job_id} 2>&1",
            timeout=10,
        )
        outputs.append(result.get("output", ""))
    docker_svc.exec_in_container(
        container_name,
        "python3 - <<'PY'\n"
        "import json, pathlib\n"
        "p=pathlib.Path('/root/.hermes/profiles/hermiss/reminders/active_checkin.json')\n"
        "data={}\n"
        "if p.exists():\n"
        "    data=json.loads(p.read_text(encoding='utf-8'))\n"
        "data['cancelled']=True\n"
        "data['cancel_reason']='cancelled_from_panel'\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')\n"
        "PY",
        timeout=5,
    )
    return {"output": "\n".join(outputs), "cancelled": True}
