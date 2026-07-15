# backend/routers/auth.py
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt

from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, SINGLE_USER_MODE
from models.user import User
from dependencies import get_db
from single_runtime import ensure_single_container

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    user_id: int | None = None
    email: str | None = None
    is_admin: bool = False
    pending: bool = False   # 等待审批
    message: str = ""


def create_access_token(user_id: int, email: str, is_admin: bool = False) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "admin": is_admin, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    return authorization[7:]


def get_current_user(authorization: str = Depends(get_token), db: Session = Depends(get_db)) -> User:
    try:
        token = authorization
        if token.startswith("Bearer "):
            token = token[7:]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def get_admin_user(token: str = Depends(get_token), db: Session = Depends(get_db)) -> User:
    user = get_current_user(token, db)
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


# ── Endpoints ──

@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if SINGLE_USER_MODE:
        raise HTTPException(403, "单用户版本不开放注册")

    """注册 -> 进入审批队列"""
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(400, "该邮箱已注册")

    user = User(
        email=req.email,
        hashed_password=pwd_context.hash(req.password),
        is_approved=False,
    )
    db.add(user)
    db.commit()
    return {"message": "单用户版本不开放注册", "pending": True}


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """登录 — 审批通过后才能获取 token"""
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not pwd_context.verify(req.password, user.hashed_password):
        raise HTTPException(401, "邮箱或密码错误")

    if not user.is_active:
        return TokenResponse(pending=True, message="账号已被禁用，请联系管理员", email=user.email)
    if not user.is_approved:
        return TokenResponse(
            pending=True,
            message="账号暂不可用",
            email=user.email,
        )

    if SINGLE_USER_MODE:
        ensure_single_container(user, db)

    token = create_access_token(user.id, user.email, user.is_admin)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """校验 token 并返回用户信息 — 所有角色的通用端点"""
    return {
        "user_id": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "is_approved": user.is_approved,
    }
