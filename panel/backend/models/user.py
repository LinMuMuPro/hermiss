# backend/models/user.py
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import declarative_base
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # 审批
    is_approved = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)

    # 容器信息（审批通过后自动创建）
    container_id = Column(String(6), unique=True, nullable=True)  # 6 位随机 ID
    container_status = Column(String(20), default="pending")

    # 配置
    model_provider = Column(String(100), default="deepseek")
    model_name = Column(String(100), default="deepseek-v4-flash")
    api_key_encrypted = Column(String(512), nullable=True)

    # 微信
    wechat_account_id = Column(String(255), nullable=True)
    wechat_token = Column(String(512), nullable=True)
    wechat_bound = Column(Boolean, default=False)

    # 端口
    panel_port = Column(Integer, nullable=True)
