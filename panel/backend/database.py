# backend/database.py
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
from config import (
    DATABASE_URL,
    SINGLE_USER_CONTAINER,
    SINGLE_USER_CONTAINER_PORT,
    SINGLE_USER_EMAIL,
    SINGLE_USER_MODE,
    SINGLE_USER_PASSWORD,
)
from models.user import Base, User

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 建表
Base.metadata.create_all(bind=engine)

# 自动迁移：补上后续新增的列和新表
def _auto_migrate():
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    migrations = {
        "wechat_token": "ALTER TABLE users ADD COLUMN wechat_token VARCHAR(512)",
    }
    with engine.connect() as conn:
        for col, sql in migrations.items():
            if col not in cols:
                conn.execute(sql)
                conn.commit()

        # 操作日志表（P9/P18）
        if "operation_logs" not in insp.get_table_names():
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS operation_logs ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  admin_email VARCHAR(255) NOT NULL,"
                "  action VARCHAR(100) NOT NULL,"
                "  target VARCHAR(255) DEFAULT '',"
                "  detail TEXT DEFAULT '',"
                "  container_id VARCHAR(255) DEFAULT '',"
                "  created_at DATETIME NOT NULL"
                ")"
            ))
            conn.commit()
        else:
            # 已有表则检查 container_id 列
            existing = {c["name"] for c in insp.get_columns("operation_logs")}
            if "container_id" not in existing:
                conn.execute(text("ALTER TABLE operation_logs ADD COLUMN container_id VARCHAR(255) DEFAULT ''"))
                conn.commit()

        # 记忆检索日志表（P19）
        if "memory_retrieval_logs" not in insp.get_table_names():
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS memory_retrieval_logs ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  container_id VARCHAR(255) DEFAULT '',"
                "  user_message TEXT DEFAULT '',"
                "  keywords TEXT DEFAULT '',"
                "  match_count INTEGER DEFAULT 0,"
                "  matched_entries TEXT DEFAULT '',"
                "  created_at DATETIME NOT NULL"
                ")"
            ))
            conn.commit()

_auto_migrate()



def _ensure_single_user():
    if not SINGLE_USER_MODE:
        return
    pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == SINGLE_USER_EMAIL).first()
        if not user:
            user = User(email=SINGLE_USER_EMAIL)
            db.add(user)
        user.hashed_password = pwd_context.hash(SINGLE_USER_PASSWORD)
        user.is_active = True
        user.is_approved = True
        user.is_admin = False
        user.container_id = SINGLE_USER_CONTAINER
        user.container_status = user.container_status or "pending"
        user.panel_port = SINGLE_USER_CONTAINER_PORT
        user.model_provider = user.model_provider or "deepseek"
        user.model_name = user.model_name or "deepseek-v4-flash"
        db.commit()
    finally:
        db.close()


_ensure_single_user()
