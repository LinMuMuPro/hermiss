# backend/routers/usage.py
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from sqlalchemy.orm import Session

from models.user import User
from services import docker_service as docker_svc
from routers.auth import get_current_user, get_admin_user
from dependencies import get_db
from config import MOCK_MODE

router = APIRouter(prefix="/api/usage", tags=["usage"])


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    return authorization[7:]


def _get_insights(user: User, days: int = 7) -> dict:
    """执行容器内 hermes insights"""
    if MOCK_MODE:
        import random, hashlib
        seed = int(hashlib.md5(user.email.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        return {
            "total_tokens": rng.randint(50000, 500000),
            "total_calls": rng.randint(50, 500),
            "days": days,
            "daily_breakdown": [
                {"date": f"2026-06-{d:02d}", "tokens": rng.randint(5000, 80000), "calls": rng.randint(5, 80)}
                for d in range(max(1, 30 - days), 31)
            ],
            "note": f"[MOCK] — {user.email}"
        }
    if not user.container_id:
        return {"total_tokens": 0, "total_calls": 0, "days": days, "daily_breakdown": [], "note": "容器未创建"}
    container_name = user.container_id
    result = docker_svc.exec_in_container(
        container_name,
        f"hermes insights --days {days} 2>/dev/null || echo 'N/A'",
        timeout=15,
    )
    output = result.get("output", "")
    # 简单解析 hermes insights 输出
    tokens = 0
    calls = 0
    for line in output.split("\n"):
        if "total=" in line:
            try:
                tokens += int(line.split("total=")[1].split()[0])
            except (ValueError, IndexError):
                pass
        if "API call" in line:
            calls += 1
    return {
        "total_tokens": tokens,
        "total_calls": calls,
        "days": days,
        "raw": output,
    }


# ── 普通用户：自己的用量 ──

@router.get("/my")
def my_usage(
    days: int = Query(7, ge=1, le=30),
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    """当前用户的 token 用量"""
    user = get_current_user(token, db)
    return _get_insights(user, days)


# ── 管理员：全局用量 + 全部容器 ──

@router.get("/admin/global")
def global_usage(
    days: int = Query(7, ge=1, le=30),
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """管理员查看全部用户的用量汇总"""
    users = db.query(User).filter(User.is_approved == True).all()
    if MOCK_MODE:
        import random
        rng = random.Random(42)
        mock_users = [{
            "id": i + 1,
            "email": f"[MOCK] user{i+1}",
            "container_id": f"mock-{rng.choice('abcdefgh')[0]}",
            "container_status": "running" if rng.random() > 0.2 else "stopped",
            "tokens": rng.randint(50000, 500000),
            "calls": rng.randint(50, 500),
        } for i in range(5)]
        total_tokens = sum(u["tokens"] for u in mock_users)
        total_calls = sum(u["calls"] for u in mock_users)
        return {"total_tokens": total_tokens, "total_calls": total_calls, "days": days, "users": mock_users}
    total = {"total_tokens": 0, "total_calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "users": [], "daily": []}
    for u in users:
        total["users"].append({
            "id": u.id,
            "email": u.email,
            "container_id": u.container_id or "未创建",
            "container_status": u.container_status,
            "tokens": 0,
            "calls": 0,
            "input_tokens": 0,
            "cache_read_tokens": 0,
        })
    total["days"] = days
    total["user_count"] = len(users)
    return total


@router.get("/admin/containers")
def admin_containers(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).filter(User.is_approved == True).all()
    if MOCK_MODE:
        import random; rng = random.Random(42)
        return {'containers': [{'id': i+1, 'email': f'[MOCK] user{i+1}','container_id': f'mock-{rng.choice(chr(97)+chr(98)+chr(99)+chr(100)+chr(101)+chr(102)+chr(103))[0]}{i}','status': 'running','wechat_bound': i<3} for i in range(5)]}
    from services import docker_service as docker_svc
    server = docker_svc.get_server_stats()
    return {
        'containers': [{'id': u.id,'email': u.email,'container_id': u.container_id or '未创建','status': u.container_status,'wechat_bound': u.wechat_bound,'created_at': str(u.created_at)} for u in users],
        'server': {'disk': server['disk'], 'memory': server['memory'], 'load': server['load']},
        'containers_raw': server['containers'],
    }
