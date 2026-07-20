import base64
import json
import re
import shlex
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dependencies import get_db
from models.user import User
from routers.auth import get_current_user
from services import docker_service as docker_svc
from single_runtime import ensure_single_container

router = APIRouter(prefix="/api/chat", tags=["chat"])
BRIDGE_PORT = 8799
BRIDGE_PATH = "/tmp/hermiss_panel_chat_bridge.py"


class ChatSendRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def _container_name(user: User, db: Session) -> str:
    ensure_single_container(user, db)
    if not user.container_id:
        raise HTTPException(400, "用户容器尚未创建")
    status = docker_svc.get_container_status(user.container_id)
    if status.get("status") != "running":
        docker_svc.start_container(user.container_id)
    return user.container_id


def _run_json_script(container_name: str, script: str, timeout: int = 60) -> dict:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    command = (
        "PYTHON_BIN=/usr/local/lib/hermes-agent/venv/bin/python3\n"
        "[ -x \"$PYTHON_BIN\" ] || PYTHON_BIN=python3\n"
        "export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 PYTHONIOENCODING=utf-8\n"
        f"printf %s {shlex.quote(encoded)} | base64 -d | \"$PYTHON_BIN\""
    )
    result = docker_svc.exec_in_container(container_name, command, timeout=timeout)
    if result.get("exit_code", 1) != 0:
        raise HTTPException(500, result.get("output") or result.get("error") or "容器执行失败")
    output = result.get("output") or "{}"
    try:
        return json.loads(output.strip().splitlines()[-1])
    except json.JSONDecodeError:
        raise HTTPException(500, f"容器返回异常: {output[-800:]}")


def _clean_cli_output(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")
    keep = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            keep.append("")
            continue
        if raw.startswith("[message-analyzer]"):
            continue
        if "pkg_resources is deprecated" in raw:
            continue
        if raw.startswith("/usr/local/lib/") and "site-packages" in raw:
            continue
        keep.append(line.rstrip())
    return "\n".join(keep).strip()


def _bridge_source() -> str:
    path = Path(__file__).resolve().parents[1] / "resources" / "panel-chat-bridge" / "server.py"
    return path.read_text(encoding="utf-8")


def _ensure_chat_bridge(container_name: str) -> None:
    bridge_b64 = base64.b64encode(_bridge_source().encode("utf-8")).decode("ascii")
    command = (
        "PYTHON_BIN=/usr/local/lib/hermes-agent/venv/bin/python3\n"
        "[ -x \"$PYTHON_BIN\" ] || PYTHON_BIN=python3\n"
        "export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUTF8=1 PYTHONIOENCODING=utf-8\n"
        f"printf %s {shlex.quote(bridge_b64)} | base64 -d > {shlex.quote(BRIDGE_PATH)}\n"
        f"$PYTHON_BIN - <<'PY' >/dev/null 2>&1\n"
        "import urllib.request\n"
        f"urllib.request.urlopen('http://127.0.0.1:{BRIDGE_PORT}/health', timeout=1).read()\n"
        "PY\n"
        "if [ $? -ne 0 ]; then\n"
        f"  nohup \"$PYTHON_BIN\" {shlex.quote(BRIDGE_PATH)} >/tmp/hermiss_panel_chat_bridge.log 2>&1 &\n"
        "  for i in $(seq 1 30); do\n"
        f"    \"$PYTHON_BIN\" - <<'PY' >/dev/null 2>&1 && exit 0\n"
        "import urllib.request\n"
        f"urllib.request.urlopen('http://127.0.0.1:{BRIDGE_PORT}/health', timeout=1).read()\n"
        "PY\n"
        "    sleep 0.2\n"
        "  done\n"
        "  tail -80 /tmp/hermiss_panel_chat_bridge.log 2>/dev/null\n"
        "  exit 1\n"
        "fi\n"
        "echo OK\n"
    )
    result = docker_svc.exec_in_container(container_name, command, timeout=15)
    if result.get("exit_code", 1) != 0:
        raise HTTPException(500, result.get("output") or result.get("error") or "面板聊天桥接启动失败")


def _send_via_bridge(container_name: str, message: str) -> dict:
    message_b64 = base64.b64encode(message.strip().encode("utf-8")).decode("ascii")
    script = f"""
import base64, json, urllib.request

message = base64.b64decode({message_b64!r}).decode('utf-8')
body = json.dumps({{'message': message}}, ensure_ascii=False).encode('utf-8')
request = urllib.request.Request(
    'http://127.0.0.1:{BRIDGE_PORT}/chat',
    data=body,
    headers={{'Content-Type': 'application/json; charset=utf-8'}},
    method='POST',
)
try:
    with urllib.request.urlopen(request, timeout=180) as response:
        print(response.read().decode('utf-8'))
except Exception as exc:
    print(json.dumps({{'ok': False, 'error': str(exc)}}, ensure_ascii=False))
"""
    return _run_json_script(container_name, script, timeout=190)


@router.get("/history")
def chat_history(
    limit: int = 80,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    container_name = _container_name(user, db)
    safe_limit = max(1, min(int(limit or 80), 300))
    script = f"""
import json, sqlite3
path = '/root/.hermes/profiles/hermiss/state.db'
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row
rows = conn.execute(\"\"\"
    SELECT id, session_id, role, content, timestamp
    FROM messages
    WHERE active=1
      AND role IN ('user', 'assistant')
      AND content IS NOT NULL
      AND TRIM(content) != ''
      AND session_id NOT LIKE 'cron_%'
      AND content NOT LIKE '[IMPORTANT:%'
    ORDER BY timestamp DESC, id DESC
    LIMIT ?
\"\"\", ({safe_limit},)).fetchall()
items = []
for row in reversed(rows):
    items.append({{
        'id': row['id'],
        'session_id': row['session_id'],
        'role': row['role'],
        'content': row['content'],
        'timestamp': row['timestamp'],
    }})
conn.close()
print(json.dumps({{'messages': items}}, ensure_ascii=False))
"""
    return _run_json_script(container_name, script, timeout=20)


@router.get("/short-state")
def chat_short_state(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    container_name = _container_name(user, db)
    script = """
import json
from datetime import datetime, timezone
from pathlib import Path

path = Path('/root/.hermes/profiles/hermiss/memory/short_term_user_state.json')
if not path.exists():
    print(json.dumps({'status': 'none', 'state': None, 'reason': 'missing'}, ensure_ascii=False))
    raise SystemExit

try:
    data = json.loads(path.read_text(encoding='utf-8'))
except Exception as exc:
    print(json.dumps({'status': 'error', 'state': None, 'error': str(exc)}, ensure_ascii=False))
    raise SystemExit

state = data.get('state') if isinstance(data, dict) else None
if isinstance(state, dict):
    started_raw = str(state.get('started_at') or '')
    try:
        started = datetime.fromisoformat(started_raw.replace('Z', '+00:00'))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()))
    except Exception:
        age_seconds = None
    state['age_seconds'] = age_seconds
data['state'] = state
print(json.dumps(data, ensure_ascii=False))
"""
    return _run_json_script(container_name, script, timeout=10)


@router.post("/send")
def chat_send(
    req: ChatSendRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    container_name = _container_name(user, db)
    _ensure_chat_bridge(container_name)
    data = _send_via_bridge(container_name, req.message)
    if not data.get("ok"):
        raise HTTPException(500, _clean_cli_output(data.get("error") or "发送失败"))
    return {
        "reply": _clean_cli_output(data.get("reply") or ""),
        "history": chat_history(limit=80, user=user, db=db),
    }
