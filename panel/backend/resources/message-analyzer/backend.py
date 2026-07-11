#!/usr/bin/env python3
"""
Hermes Memory Panel — API Backend
FastAPI server that reads/writes the SQLite memory database.
Run: python backend.py [--port 8765] [--db path/to/hermes_memory.db]
"""

import argparse
import json as _json
import sqlite3
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# ── App setup ──────────────────────────────────────────────────────────

app = FastAPI(title="Hermes Memory API", version="1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*", "null"], allow_methods=["*"], allow_headers=["*"])

DB_PATH: Path = None
PANEL_HTML: Path = Path(__file__).parent / "hermes-memory-panel.html"


def get_db() -> sqlite3.Connection:
    if not DB_PATH or not DB_PATH.exists():
        raise HTTPException(500, f"数据库不存在: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Serve the web panel ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_panel():
    if not PANEL_HTML.exists():
        raise HTTPException(404, "Panel HTML not found")
    return PANEL_HTML.read_text(encoding="utf-8")


# ── Pydantic models ────────────────────────────────────────────────────

class MemoryUpdate(BaseModel):
    entry: str | None = None
    category: str | None = None
    importance: str | None = None


# ── Stats ──────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        facts = db.execute("SELECT COUNT(*) FROM memories WHERE LOWER(category)='fact'").fetchone()[0]
        prefs = db.execute("SELECT COUNT(*) FROM memories WHERE LOWER(category)='preference'").fetchone()[0]
        sessions = db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]

        # Category breakdown
        cats = db.execute(
            "SELECT LOWER(category) as cat, COUNT(*) as cnt FROM memories GROUP BY cat ORDER BY cnt DESC"
        ).fetchall()
        by_category = {r["cat"]: r["cnt"] for r in cats}

        # Importance breakdown
        imps = db.execute(
            "SELECT LOWER(importance) as imp, COUNT(*) as cnt FROM memories GROUP BY imp ORDER BY cnt DESC"
        ).fetchall()
        by_importance = {r["imp"]: r["cnt"] for r in imps}

        # Emotion breakdown
        emos = db.execute(
            "SELECT LOWER(emotion) as emo, COUNT(*) as cnt FROM memories WHERE emotion IS NOT NULL GROUP BY emo ORDER BY cnt DESC"
        ).fetchall()
        by_emotion = {r["emo"]: r["cnt"] for r in emos}

        # Memory growth by month (last 12 months)
        months = db.execute(
            """SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt
               FROM memories
               WHERE created_at >= date('now', '-12 months')
               GROUP BY month ORDER BY month"""
        ).fetchall()
        memory_by_month = [{"month": r["month"], "count": r["cnt"]} for r in months]

        # Session activity (last 30 days)
        sess_activity = db.execute(
            """SELECT date(ended_at) as day, COUNT(*) as cnt, SUM(message_count) as msgs
               FROM session_summaries WHERE ended_at >= date('now', '-30 days')
               GROUP BY day ORDER BY day"""
        ).fetchall()
        session_activity = [{"day": r["day"], "count": r["cnt"], "messages": r["msgs"] or 0} for r in sess_activity]

        return {
            "total_memories": total,
            "facts": facts,
            "preferences": prefs,
            "sessions": sessions,
            "by_category": by_category,
            "by_importance": by_importance,
            "by_emotion": by_emotion,
            "memory_by_month": memory_by_month,
            "session_activity": session_activity,
        }
    finally:
        db.close()


# ── Memories ───────────────────────────────────────────────────────────

@app.get("/api/memories")
def list_memories(
    search: str = Query("", description="关键词搜索"),
    category: str = Query("", description="筛选类别: FACT, PREFERENCE, HEALTH, EMOTIONAL"),
    importance: str = Query("", description="筛选重要度: HIGH, MEDIUM, LOW"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    try:
        where = []
        params = []
        if search:
            where.append("entry LIKE ?")
            params.append(f"%{search}%")
        if category:
            where.append("LOWER(category) = LOWER(?)")
            params.append(category)
        if importance:
            where.append("LOWER(importance) = LOWER(?)")
            params.append(importance)

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT id, category, importance, entry, emotion, source_msg, created_at FROM memories {where_clause} ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.execute(sql, params).fetchall()
        total = db.execute(f"SELECT COUNT(*) FROM memories {where_clause}", params[:-2]).fetchone()[0]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "memories": [dict(r) for r in rows],
        }
    finally:
        db.close()


@app.get("/api/memories/{memory_id}")
def get_memory(memory_id: int):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"记忆 #{memory_id} 不存在")
        return dict(row)
    finally:
        db.close()


@app.put("/api/memories/{memory_id}")
def update_memory(memory_id: int, body: MemoryUpdate):
    db = get_db()
    try:
        row = db.execute("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"记忆 #{memory_id} 不存在")

        updates = {}
        if body.entry is not None:
            updates["entry"] = body.entry
        if body.category is not None:
            updates["category"] = body.category
        if body.importance is not None:
            updates["importance"] = body.importance

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [memory_id]
            db.execute(f"UPDATE memories SET {set_clause} WHERE id=?", values)
            db.commit()

        return {"ok": True, "id": memory_id}
    finally:
        db.close()


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int):
    db = get_db()
    try:
        row = db.execute("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"记忆 #{memory_id} 不存在")
        # Delete from main table — FTS trigger handles index cleanup
        db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        db.commit()
        return {"ok": True, "id": memory_id}
    finally:
        db.close()


# ── Reminders ──────────────────────────────────────────────────────────

# ── Scheduled Tasks ────────────────────────────────────────────────────

# ── Cron Jobs ────────────────────────────────────────────────────────

@app.get("/api/cron-jobs")
def list_cron_jobs():
    """定时任务 — 活跃 (jobs.json) + 已完成 (cron/output/)"""
    # ── 路径推导 ──
    try:
        db = get_db()
        db_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    except Exception:
        db_path = Path.home() / ".hermes" / "profiles" / "uino_c" / "memory" / "hermes_memory.db"
    profile_dir = db_path.parent.parent
    jobs_path = profile_dir / "cron" / "jobs.json"
    output_dir = profile_dir / "cron" / "output"

    all_jobs = []
    seen = set()

    # ── 活跃任务 (jobs.json) ──
    if jobs_path.exists():
        data = _json.loads(jobs_path.read_text())
        for j in data.get("jobs", []):
            seen.add(j["id"])
            all_jobs.append({
                "id": j["id"],
                "name": (j.get("name") or "")[:60],
                "schedule": j.get("schedule_display", j["schedule"].get("display", "")),
                "state": j.get("state", "scheduled"),
                "deliver": j.get("deliver", "origin"),
                "created_at": j.get("created_at", ""),
                "next_run_at": j.get("next_run_at"),
                "last_run_at": j.get("last_run_at"),
                "response_preview": None,
            })

    # ── 已完成 (cron/output/<job_id>/) ──
    if output_dir.exists():
        for job_dir in sorted(output_dir.iterdir(), reverse=True):
            if not job_dir.is_dir() or job_dir.name in seen:
                continue
            md_files = sorted(job_dir.glob("*.md"), reverse=True)
            if not md_files:
                continue
            content = md_files[0].read_text(encoding="utf-8")
            name = ""
            schedule = ""
            response = ""
            in_response = False
            for line in content.split("\n"):
                if line.startswith("# Cron Job: "):
                    name = line.replace("# Cron Job: ", "").strip()[:60]
                elif line.startswith("**Schedule:** "):
                    schedule = line.replace("**Schedule:** ", "").strip()
                elif line.startswith("## Response"):
                    in_response = True
                elif in_response and line.strip() and not line.startswith("#"):
                    response += line.strip() + " "

            all_jobs.append({
                "id": job_dir.name,
                "name": name,
                "schedule": schedule,
                "state": "completed",
                "deliver": "",
                "created_at": "",
                "next_run_at": None,
                "last_run_at": None,
                "response_preview": response.strip()[:100] if response.strip() else None,
            })

    return {"cron_jobs": all_jobs}


# ── Sessions ───────────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions(limit: int = Query(50, ge=1, le=500)):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, session_id, summary, message_count, last_emotion, ended_at FROM session_summaries ORDER BY ended_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"sessions": [dict(r) for r in rows]}
    finally:
        db.close()


# ── Export ─────────────────────────────────────────────────────────────

@app.get("/api/export")
def export_memories():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, category, importance, entry, emotion, created_at FROM memories ORDER BY id"
        ).fetchall()
        return {"exported_at": datetime.now().isoformat(), "memories": [dict(r) for r in rows]}
    finally:
        db.close()


# ── Health ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    if DB_PATH and DB_PATH.exists():
        return {"status": "ok", "db": str(DB_PATH)}
    return {"status": "error", "db": str(DB_PATH) if DB_PATH else "not set"}


@app.get("/health")
def health_alias():
    return health()


# ── Non-prefixed aliases (compat with frontend that omits /api) ────────
# Must run AFTER all @app route decorators above.

# NOTE: /health has an explicit alias above because add_api_route with
# multi-method route objects doesn't always register GET-only aliases.

for _route in app.routes:
    if hasattr(_route, "path") and _route.path.startswith("/api/"):
        alias = _route.path[4:]  # strip "/api"
        if alias and alias != "/health":  # already handled above
            app.add_api_route(
                alias,
                _route.endpoint,
                methods=_route.methods,
                response_model=_route.response_model,
            )


# ── Entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Memory Panel API")
    parser.add_argument("--port", type=int, default=8765, help="API 端口 (默认 8765)")
    parser.add_argument("--db", type=str, default="hermes_memory.db", help="数据库路径")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    args = parser.parse_args()

    DB_PATH = Path(args.db).resolve()
    if not DB_PATH.exists():
        print(f"[WARN] 数据库不存在: {DB_PATH}，启动后请先复制数据库文件到此路径")
        print(f"[WARN] 示例: cp ~/.hermes/profiles/uino/memory/hermes_memory.db {DB_PATH}")

    print(f"Hermes Memory API v1.1")
    print(f"  数据库: {DB_PATH}")
    print(f"  地址:   http://{args.host}:{args.port}")
    print(f"  文档:   http://{args.host}:{args.port}/docs")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
