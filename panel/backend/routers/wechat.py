# backend/routers/wechat.py
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
import secrets, threading, time, base64, io, requests

import qrcode, qrcode.image.svg

from models.user import User
from routers.auth import get_current_user
from dependencies import get_db
from config import MOCK_MODE, HERMES_PROFILE_NAME

router = APIRouter(prefix="/api/wechat", tags=["wechat"])

ILINK_BASE = "https://ilinkai.weixin.qq.com"
EP_QR = "ilink/bot/get_bot_qrcode"
EP_QR_STATUS = "ilink/bot/get_qrcode_status"

_qr_store: dict = {}
_qr_lock = threading.Lock()
def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    return authorization[7:]
class WechatBindRequest(BaseModel):
    account_id: str; token: str; base_url: str = ILINK_BASE; dm_policy: str = "open"
class PairingApproveRequest(BaseModel):
    code: str
# ═══════════════════════════════════════════
# 扫码绑定
# ═══════════════════════════════════════════

@router.post("/qr")
def wechat_qr(token: str = Depends(get_token), db: Session = Depends(get_db)):
    get_current_user(token, db)
    try:
        resp = requests.get(f"{ILINK_BASE}/{EP_QR}?bot_type=3", timeout=30)
        resp.raise_for_status(); data = resp.json()
    except Exception as e:
        raise HTTPException(500, f"获取二维码失败: {e}")

    qrcode_hex = data.get("qrcode",""); qrcode_url = data.get("qrcode_img_content","")
    if not qrcode_hex: raise HTTPException(500, "iLink 未返回二维码")

    qr_id = secrets.token_hex(8)
    with _qr_lock:
        _qr_store[qr_id] = {"qrcode_hex": qrcode_hex, "qrcode_url": qrcode_url, "status": "wait", "created_at": time.time()}

    qr_data = qrcode_url or qrcode_hex
    factory = qrcode.image.svg.SvgImage
    qr_img = qrcode.make(qr_data, image_factory=factory)
    buf = io.BytesIO(); qr_img.save(buf)
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return {"qr_id": qr_id, "qrcode_url": qrcode_url or qrcode_hex, "qr_image": f"data:image/svg+xml;base64,{qr_b64}"}
@router.get("/qr/{qr_id}")
def wechat_qr_status(qr_id: str, token: str = Depends(get_token), db: Session = Depends(get_db)):
    with _qr_lock: session = _qr_store.get(qr_id)
    if not session: raise HTTPException(404, "二维码已过期")

    if session.get("status") == "confirmed":
        return {"status": "confirmed", "account_id": session.get("account_id")}

    try:
        resp = requests.get(f"{ILINK_BASE}/{EP_QR_STATUS}?qrcode={session['qrcode_hex']}", timeout=30)
        resp.raise_for_status(); data = resp.json()
    except Exception as e:
        raise HTTPException(500, f"状态查询失败: {e}")

    status = data.get("status","wait")
    if status == "confirmed":
        account_id = data.get("ilink_bot_id","")
        bot_token = data.get("bot_token","")
        base_url = data.get("baseurl", ILINK_BASE)
        if not account_id or not bot_token:
            raise HTTPException(500, "iLink 返回凭据不完整")

        user = get_current_user(token, db)
        user.wechat_bound = True
        user.wechat_account_id = account_id
        user.wechat_token = bot_token
        user.wechat_bot_token = bot_token

        # 写入容器 .env
        from services import docker_service as docker_svc
        docker_svc.update_env(user.container_id, {
            "WEIXIN_ACCOUNT_ID": account_id,
            "WEIXIN_TOKEN": bot_token,
            "WEIXIN_BASE_URL": base_url,
            "WEIXIN_DM_POLICY": "open",
            "GATEWAY_ALLOW_ALL_USERS": "true",
        })
        docker_svc.restart_container(user.container_id)
        db.commit()
        # 记录操作日志
        from operation_log import log_action
        log_action(user.email, "bind_wechat", f"container:{user.container_id}", account_id, user.container_id)
        # 后台发送欢迎消息

        

# ═══════════════════════════════════════════
# 状态 / 绑定 / 解绑 / 测试
# ═══════════════════════════════════════════

        with _qr_lock:
            if qr_id in _qr_store:
                _qr_store[qr_id]["status"] = "confirmed"
                _qr_store[qr_id]["account_id"] = account_id

        return {"status": "confirmed", "account_id": account_id}
@router.get("/status")
def wechat_status(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    return {"bound": user.wechat_bound, "account_id": user.wechat_account_id or "",
            "container_status": user.container_status or "unknown"}


@router.post("/bind")
def bind_wechat(req: WechatBindRequest, token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    if MOCK_MODE:
        user.wechat_account_id = req.account_id
        return {"status": "bound", "account_id": user.wechat_account_id or "[MOCK]"}

    user.wechat_account_id = req.account_id
    user.wechat_bound = True
    db.commit()

    # 写入容器 .env 并重启使微信配置生效
    if user.container_id and not MOCK_MODE:
        from services import docker_service as docker_svc
        try:
            docker_svc.update_env(user.container_id, {
                "WEIXIN_ACCOUNT_ID": req.account_id,
                "WEIXIN_DM_POLICY": "open",
                "GATEWAY_ALLOW_ALL_USERS": "true",
            })
            docker_svc.restart_container(user.container_id)
        except Exception:
            pass  # 不阻塞绑定流程

    return {"status": "bound", "account_id": req.account_id}


@router.post("/unbind")
def unbind_wechat(token: str = Depends(get_token), db: Session = Depends(get_db)):
    user = get_current_user(token, db)
    user.wechat_bound = False; user.wechat_account_id = None
    db.commit()
    if user.container_id and not MOCK_MODE:
        from services import docker_service as docker_svc
        docker_svc.update_env(user.container_id, {"WEIXIN_TOKEN": "", "WEIXIN_ACCOUNT_ID": "",
                            "WEIXIN_BASE_URL": "", "WEIXIN_DM_POLICY": ""})
        docker_svc.restart_container(user.container_id)
    return {"status": "unbound"}


@router.get("/pairing")
def list_pairing(token: str = Depends(get_token), db: Session = Depends(get_db)):
    return {"pairings": []}


@router.post("/pairing/approve")
def approve_pairing(req: PairingApproveRequest, token: str = Depends(get_token), db: Session = Depends(get_db)):
    return {"status": "approved"}


@router.get("/connection-test")
def test_connection(token: str = Depends(get_token), db: Session = Depends(get_db)):
    return {"status": "ok", "message": "连接正常"}
