# backend/routers/memory.py
import base64
import json
import re
import shlex

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import MOCK_MODE
from dependencies import get_db
from models.user import User
from routers.auth import get_current_user
from services import docker_service as docker_svc


router = APIRouter(prefix="/api/memory", tags=["memory"])

DB_PATH = "/root/.hermes/profiles/hermiss/memory/hermes_memory.db"


class MemoryUpdate(BaseModel):
    entry: str | None = None
    category: str | None = None
    importance: str | None = None


class MemoryConflictResolve(BaseModel):
    keep_id: int | None = None
    discard_ids: list[int] = []
    merged_entry: str | None = None
    category: str | None = None
    importance: str | None = None


def get_token(authorization: str = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "无效的认证头")
    return authorization[7:]


def _check_container(user: User):
    if MOCK_MODE:
        return
    if not user.container_id or user.container_status not in ("running", "created"):
        raise HTTPException(400, "请先创建并启动容器")


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _like_value(value: str) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("'", "''")
    )
    return f"'%{escaped}%'"


def _run_sql(container_id: str, sql: str, *, json_output: bool = False, timeout: int = 10) -> str:
    encoded = base64.b64encode(sql.encode("utf-8")).decode("ascii")
    json_flag = "-json " if json_output else ""
    fallback = " || echo '[]'" if json_output else ""
    command = (
        f"printf %s {shlex.quote(encoded)} | base64 -d | "
        f"sqlite3 {json_flag}{shlex.quote(DB_PATH)} 2>/dev/null{fallback}"
    )
    return docker_svc.exec_in_container(container_id, command, timeout=timeout).get("output", "")


def _exec_sql(container_id: str, sql: str) -> list[dict]:
    output = _run_sql(container_id, sql, json_output=True, timeout=10)
    try:
        data = json.loads(output or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _exec_write(container_id: str, sql: str, *, timeout: int = 10) -> None:
    result = docker_svc.exec_in_container(
        container_id,
        (
            f"printf %s {shlex.quote(base64.b64encode(sql.encode('utf-8')).decode('ascii'))} | "
            f"base64 -d | sqlite3 {shlex.quote(DB_PATH)} 2>&1"
        ),
        timeout=timeout,
    )
    if result.get("exit_code", 1) != 0:
        raise HTTPException(500, "数据库操作失败：" + result.get("output", ""))


def _memory_table_has_column(container_id: str, column: str) -> bool:
    rows = _exec_sql(container_id, "PRAGMA table_info(memories);")
    return any(row.get("name") == column for row in rows)


def _delete_memory_statements(memory_id: int, *, include_semicolon: bool = True) -> list[str]:
    suffix = ";" if include_semicolon else ""
    return [
        f"DELETE FROM memories WHERE id = {int(memory_id)}{suffix}",
        f"DELETE FROM memories_fts WHERE rowid = {int(memory_id)}{suffix}",
    ]


_STATUS_CONFLICT_PAIRS = [
    (("感冒", "生病", "发烧", "咳嗽", "难受", "不舒服", "疼", "痛", "失眠", "焦虑", "紧张", "累", "困"),
     ("好了", "好啦", "恢复", "康复", "不感冒", "不难受", "舒服了", "没事了", "退烧", "不疼", "不痛", "睡好了", "不紧张")),
    (("喜欢", "爱吃", "想吃", "想要", "想去", "想看"),
     ("不喜欢", "不爱吃", "不想吃", "不想要", "不想去", "不想看")),
    (("讨厌", "不喜欢", "不爱吃", "不想"),
     ("喜欢", "爱吃", "想吃", "想要")),
]


def _memory_tokens(text: str) -> set[str]:
    value = str(text or "").lower()
    words = set(re.findall(r"[a-zA-Z0-9_]{2,}", value))
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    for chunk in zh_chunks:
        words.add(chunk)
        for size in (2, 3):
            words.update(chunk[index:index + size] for index in range(max(0, len(chunk) - size + 1)))
    return {word for word in words if word.strip()}


def _pair_conflict_reason(a: dict, b: dict) -> str | None:
    entry_a = str(a.get("entry") or "")
    entry_b = str(b.get("entry") or "")
    if not entry_a or not entry_b:
        return None
    if a.get("category") and b.get("category") and a.get("category") != b.get("category"):
        return None

    tokens_a = _memory_tokens(entry_a)
    tokens_b = _memory_tokens(entry_b)
    overlap = tokens_a & tokens_b
    if len(overlap) < 1:
        return None

    for negative_words, positive_words in _STATUS_CONFLICT_PAIRS:
        a_negative = any(word in entry_a for word in negative_words)
        a_positive = any(word in entry_a for word in positive_words)
        b_negative = any(word in entry_b for word in negative_words)
        b_positive = any(word in entry_b for word in positive_words)
        if (a_negative and b_positive) or (a_positive and b_negative):
            return "状态变化/偏好反转：" + "、".join(sorted(overlap)[:4])
    return None


@router.get("/list")
def list_memories(
    search: str = Query(""),
    category: str = Query(""),
    importance: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"memories": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 1}

    conditions = []
    if search:
        conditions.append(f"entry LIKE {_like_value(search)} ESCAPE '\\'")
    if category:
        conditions.append(f"category = {_sql_literal(category)}")
    if importance:
        conditions.append(f"importance = {_sql_literal(importance)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size

    rows = _exec_sql(
        user.container_id,
        (
            "SELECT id, category, entry, importance, emotion, source_msg, created_at "
            f"FROM memories {where} ORDER BY created_at DESC "
            f"LIMIT {int(page_size)} OFFSET {int(offset)}"
        ),
    )
    total_result = _exec_sql(user.container_id, f"SELECT COUNT(*) as cnt FROM memories {where}")
    total = total_result[0]["cnt"] if total_result else 0

    return {
        "memories": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/export")
def export_memories(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"exported_at": "", "memories": []}

    rows = _exec_sql(
        user.container_id,
        "SELECT category, entry, importance, emotion, source_msg, created_at FROM memories ORDER BY created_at",
    )
    return {
        "exported_at": docker_svc.exec_in_container(user.container_id, "date -Iseconds").get("output", "").strip(),
        "memories": rows,
    }


@router.get("/conflicts")
def list_memory_conflicts(
    limit: int = Query(20, ge=1, le=100),
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"conflicts": []}

    rows = _exec_sql(
        user.container_id,
        "SELECT id, category, entry, importance, emotion, source_msg, created_at "
        "FROM memories ORDER BY created_at DESC LIMIT 300",
    )
    conflicts = []
    seen = set()
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            pair_key = tuple(sorted((int(left.get("id")), int(right.get("id")))))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            reason = _pair_conflict_reason(left, right)
            if not reason:
                continue
            newer, older = (left, right) if str(left.get("created_at") or "") >= str(right.get("created_at") or "") else (right, left)
            conflicts.append({
                "id": f"{pair_key[0]}-{pair_key[1]}",
                "reason": reason,
                "suggested_keep_id": newer.get("id"),
                "memories": [newer, older],
            })
            if len(conflicts) >= limit:
                return {"conflicts": conflicts}
    return {"conflicts": conflicts}


@router.post("/resolve-conflict")
def resolve_memory_conflict(
    data: MemoryConflictResolve,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "resolved"}

    keep_id = int(data.keep_id) if data.keep_id else None
    discard_ids = sorted({int(item) for item in data.discard_ids if int(item) > 0})
    if not keep_id and not discard_ids:
        raise HTTPException(400, "请选择保留或废弃的记忆")

    statements = []
    if keep_id:
        sets = []
        if data.merged_entry is not None:
            sets.append(f"entry = {_sql_literal(data.merged_entry)}")
        if data.category is not None:
            sets.append(f"category = {_sql_literal(data.category)}")
        if data.importance is not None:
            sets.append(f"importance = {_sql_literal(data.importance)}")
        if sets:
            statements.append(f"UPDATE memories SET {', '.join(sets)} WHERE id = {keep_id};")

    has_fts = bool(_exec_sql(
        user.container_id,
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts';",
    ))
    for memory_id in discard_ids:
        if keep_id and memory_id == keep_id:
            continue
        statements.append(f"DELETE FROM memories WHERE id = {memory_id};")
        if has_fts:
            statements.append(f"DELETE FROM memories_fts WHERE rowid = {memory_id};")

    if statements:
        _exec_write(user.container_id, "\n".join(statements), timeout=10)
    return {"status": "resolved", "keep_id": keep_id, "discard_ids": discard_ids}


@router.get("/{memory_id}")
def get_memory(
    memory_id: int,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"memory": None}

    rows = _exec_sql(user.container_id, f"SELECT * FROM memories WHERE id = {int(memory_id)}")
    if not rows:
        raise HTTPException(404, f"记忆 #{memory_id} 不存在")
    return {"memory": rows[0]}


@router.put("/{memory_id}")
def update_memory(
    memory_id: int,
    data: MemoryUpdate,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "updated", "id": memory_id}

    sets = []
    if data.entry is not None:
        sets.append(f"entry = {_sql_literal(data.entry)}")
    if data.category is not None:
        sets.append(f"category = {_sql_literal(data.category)}")
    if data.importance is not None:
        sets.append(f"importance = {_sql_literal(data.importance)}")

    if sets:
        _exec_write(user.container_id, f"UPDATE memories SET {', '.join(sets)} WHERE id = {int(memory_id)}")

    return {"status": "updated", "id": memory_id}


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: int,
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "deleted", "id": memory_id}

    statements = [f"DELETE FROM memories WHERE id = {int(memory_id)};"]
    has_fts = _exec_sql(
        user.container_id,
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts';",
    )
    if has_fts:
        statements.append(f"DELETE FROM memories_fts WHERE rowid = {int(memory_id)};")
    _exec_write(user.container_id, "\n".join(statements), timeout=5)
    return {"status": "deleted", "id": memory_id}


@router.post("/clear")
def clear_memories(
    token: str = Depends(get_token),
    db: Session = Depends(get_db),
):
    user = get_current_user(token, db)
    _check_container(user)
    if MOCK_MODE:
        return {"status": "cleared", "message": "所有记忆已清空"}

    statements = ["DELETE FROM memories;"]
    if _exec_sql(user.container_id, "SELECT name FROM sqlite_master WHERE type='table' AND name='session_summaries';"):
        statements.append("DELETE FROM session_summaries;")
    if _exec_sql(user.container_id, "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts';"):
        statements.append("DELETE FROM memories_fts;")
    statements.append("VACUUM;")
    _exec_write(user.container_id, "\n".join(statements), timeout=10)
    return {"status": "cleared", "message": "所有记忆已清空"}
