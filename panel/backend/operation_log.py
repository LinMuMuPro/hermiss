from datetime import datetime
from sqlalchemy import text
from database import engine


def log_action(admin_email: str, action: str, target: str = "", detail: str = "", container_id: str = ""):
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO operation_logs (admin_email, action, target, detail, container_id, created_at) "
                "VALUES (:email, :action, :target, :detail, :cid, :now)"
            ), {
                "email": admin_email,
                "action": action,
                "target": target,
                "detail": detail,
                "cid": container_id,
                "now": datetime.utcnow().isoformat(),
            })
    except Exception:
        pass
