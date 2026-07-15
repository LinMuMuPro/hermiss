import base64
import json
import re
import shlex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dependencies import get_db
from models.user import User
from routers.auth import get_current_user
from services import docker_service as docker_svc
from single_runtime import ensure_single_container

router = APIRouter(prefix="/api/chat", tags=["chat"])


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


@router.post("/send")
def chat_send(
    req: ChatSendRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    container_name = _container_name(user, db)
    message_b64 = base64.b64encode(req.message.strip().encode("utf-8")).decode("ascii")
    script = f"""
import base64, json, subprocess

message = base64.b64decode({message_b64!r}).decode('utf-8')
try:
    proc = subprocess.run(
        ['hermes', '--profile', 'hermiss', '--cli', '-z', message],
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(json.dumps({{
        'ok': proc.returncode == 0,
        'exit_code': proc.returncode,
        'stdout': proc.stdout or '',
        'stderr': proc.stderr or '',
    }}, ensure_ascii=False))
except subprocess.TimeoutExpired:
    print(json.dumps({{'ok': False, 'exit_code': 124, 'stdout': '', 'stderr': '回复超时'}}, ensure_ascii=False))
"""
    data = _run_json_script(container_name, script, timeout=210)
    if not data.get("ok"):
        raise HTTPException(500, _clean_cli_output(data.get("stderr") or data.get("stdout") or "发送失败"))
    return {
        "reply": _clean_cli_output(data.get("stdout") or ""),
        "history": chat_history(limit=80, user=user, db=db),
    }
