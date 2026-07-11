# backend/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/hermes_panel.db")

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY 未设置，请在 .env 中配置")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

DOCKER_IMAGE = os.getenv("DOCKER_IMAGE", "ghcr.io/linmumupro/hermiss:single")
CONTAINER_PREFIX = "hermiss-"
PANEL_PORT_BASE = 8770
HERMES_PROFILE_NAME = "hermiss"

SINGLE_USER_MODE = os.getenv("SINGLE_USER_MODE", "true").lower() in ("true", "1", "yes")
SINGLE_USER_EMAIL = os.getenv("SINGLE_USER_EMAIL", "hermiss")
SINGLE_USER_PASSWORD = os.getenv("SINGLE_USER_PASSWORD", "hermiss")
SINGLE_USER_CONTAINER = os.getenv("SINGLE_USER_CONTAINER", "hermiss-single")
SINGLE_USER_CONTAINER_PORT = int(os.getenv("SINGLE_USER_CONTAINER_PORT", "8770"))

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("true",)
