"""
SQLite Memory Database — 长期记忆存储

表结构：
  memories          — 用户记忆 (fact/preference/milestone/pattern)
  memories_fts      — FTS5 全文搜索索引
  session_summaries — 会话归档

设计原则：
  - SQL 负责宽口径粗筛 (宁可多捞不漏捞)
  - LLM 负责语义精排 (判断实际相关性)
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from .vector_store import MemoryVectorStore, _profile_env_value


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('fact','preference','milestone','pattern')),
    entry TEXT NOT NULL,
    importance TEXT DEFAULT 'medium' CHECK(importance IN ('high','medium','low')),
    emotion TEXT,
    created_at TEXT NOT NULL,
    session_id TEXT,
    user_id TEXT,
    source_msg TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN ('active','resolved')),
    resolved_at TEXT,
    expires_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    entry, category, content='memories', content_rowid='id'
);

CREATE TABLE IF NOT EXISTS session_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    last_emotion TEXT,
    summary TEXT
);

-- Triggers to keep FTS index in sync
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, entry, category)
    VALUES (new.id, new.entry, new.category);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, entry, category)
    VALUES ('delete', old.id, old.entry, old.category);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, entry, category)
    VALUES ('delete', old.id, old.entry, old.category);
    INSERT INTO memories_fts(rowid, entry, category)
    VALUES (new.id, new.entry, new.category);
END;

CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at);

-- 记忆检索日志（P19）
CREATE TABLE IF NOT EXISTS memory_retrieval_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    container_id TEXT DEFAULT '',
    user_message TEXT DEFAULT '',
    keywords TEXT DEFAULT '',
    match_count INTEGER DEFAULT 0,
    matched_entries TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


CONCERN_MEMORY_TERMS = (
    "\u611f\u5192", "\u53d1\u70e7", "\u54b3\u55fd", "\u55d3\u5b50", "\u5589\u5499", "\u5934\u75bc", "\u5934\u75db",
    "\u80c3\u75bc", "\u80c3\u75db", "\u809a\u5b50\u75bc", "\u62c9\u809a\u5b50", "\u8179\u6cfb",
    "\u8fc7\u654f", "\u751f\u75c5", "\u4e0d\u8212\u670d", "\u5931\u7720", "\u71ac\u591c", "\u7ecf\u671f",
    "\u59e8\u5988", "\u4f4e\u70e7", "\u9ad8\u70e7", "\u6d41\u9f3b\u6d95", "\u9f3b\u585e",
    "\u6241\u6843\u4f53", "\u53d1\u708e",
)

CONCERN_TRIGGER_TERMS = (
    "\u5403", "\u559d", "\u96ea\u7cd5", "\u51b0\u6dc7\u6dcb", "\u51b0\u6fc0\u51cc", "\u51b7\u996e",
    "\u51b0\u7684", "\u51b0\u6c34", "\u5976\u8336", "\u8fa3", "\u706b\u9505", "\u70e7\u70e4",
    "\u9152", "\u5564\u9152", "\u5496\u5561", "\u71ac\u591c", "\u901a\u5bb5", "\u8dd1\u6b65",
    "\u5065\u8eab", "\u6e38\u6cf3", "\u51fa\u95e8", "\u6dcb\u96e8", "\u5439\u7a7a\u8c03", "\u8fd0\u52a8",
)

SCENE_RECALL_TERMS = {
    "food": (
        "\u996d", "\u83dc", "\u5403", "\u559d", "\u9910", "\u997f", "\u897f\u7ea2\u67ff", "\u756a\u8304",
        "\u9e21\u86cb", "\u96ea\u7cd5", "\u51b0\u6dc7\u6dcb", "\u51b7\u996e", "\u5976\u8336", "\u751c",
        "\u8fa3", "\u706b\u9505", "\u70e7\u70e4", "\u559c\u6b22\u5403", "\u4e0d\u559c\u6b22\u5403",
    ),
    "health": CONCERN_MEMORY_TERMS + CONCERN_TRIGGER_TERMS,
    "emotion": (
        "\u7d2f", "\u70e6", "\u96be\u8fc7", "\u59d4\u5c48", "\u538b\u529b", "\u7126\u8651", "\u7d27\u5f20",
        "\u5f00\u5fc3", "\u60f3\u54ed", "\u5d29\u6e83", "\u5fc3\u60c5", "\u5b64\u72ec", "\u60f3\u4f60",
    ),
    "activity": (
        "\u8003\u8bd5", "\u5b66\u4e60", "\u4e0a\u73ed", "\u9879\u76ee", "\u5f00\u4f1a", "\u5065\u8eab",
        "\u8dd1\u6b65", "\u51fa\u95e8", "\u56de\u5bb6", "\u7761\u89c9", "\u6d17\u6fa1", "\u5fd9",
        "\u5de5\u4f5c", "\u4f5c\u4e1a", "\u8bfe", "\u9762\u8bd5",
    ),
    "relationship": (
        "\u559c\u6b22", "\u7231", "\u60f3\u4f60", "\u966a", "\u79f0\u547c", "\u540d\u5b57", "\u53eb\u6211",
        "\u522b\u53eb", "\u4e0d\u559c\u6b22\u4f60", "\u4f60\u662f", "\u6211\u662f", "\u5173\u7cfb",
    ),
    "style": (
        "\u8bed\u6c14", "\u98ce\u683c", "\u4e0d\u8981", "\u522b\u603b", "\u4e0d\u559c\u6b22", "\u53eb\u540d\u5b57",
        "\u53eb\u6211\u540d\u5b57", "\u540d\u5b57", "\u79f0\u547c", "\u8868\u60c5", "emoji", "\u989c\u6587\u5b57",
        "\u5ba2\u670d", "\u8bf4\u6559", "\u592a\u5b98\u65b9",
    ),
}


def _detect_recall_scenes(message: str) -> set[str]:
    text = message or ""
    scenes = set()
    for scene, terms in SCENE_RECALL_TERMS.items():
        if any(term in text for term in terms):
            scenes.add(scene)
    if _message_may_need_concern_recall(text):
        scenes.add("health")
    return scenes


def _scene_search_terms(scenes: set[str]) -> tuple[str, ...]:
    terms: list[str] = []
    for scene in scenes:
        terms.extend(SCENE_RECALL_TERMS.get(scene, ()))
    seen = set()
    unique_terms = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            unique_terms.append(term)
    return tuple(unique_terms)

def _message_may_need_concern_recall(message: str) -> bool:
    text = (message or "").lower()
    if not text:
        return False
    return any(term in text for term in CONCERN_TRIGGER_TERMS)


def _build_like_filter(columns: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, list[str]]:
    clauses = []
    params = []
    for term in terms:
        pattern = f"%{term}%"
        term_clauses = []
        for column in columns:
            term_clauses.append(f"{column} LIKE ?")
            params.append(pattern)
        clauses.append("(" + " OR ".join(term_clauses) + ")")
    return " OR ".join(clauses), params


HEALTH_STATUS_GROUPS = {
    "cold": ("\u611f\u5192", "\u54b3\u55fd", "\u55d3\u5b50", "\u5589\u5499", "\u6d41\u9f3b\u6d95", "\u9f3b\u585e", "\u6241\u6843\u4f53"),
    "fever": ("\u53d1\u70e7", "\u4f4e\u70e7", "\u9ad8\u70e7"),
    "stomach": ("\u80c3\u75bc", "\u80c3\u75db", "\u809a\u5b50\u75bc", "\u62c9\u809a\u5b50", "\u8179\u6cfb"),
    "allergy": ("\u8fc7\u654f",),
    "sleep": ("\u5931\u7720", "\u71ac\u591c", "\u7761\u4e0d\u7740"),
    "period": ("\u7ecf\u671f", "\u59e8\u5988"),
}

RESOLVED_STATUS_TERMS = (
    "\u597d\u4e86", "\u597d\u591a\u4e86", "\u5df2\u7ecf\u597d\u4e86", "\u6062\u590d\u4e86", "\u5eb7\u590d\u4e86",
    "\u6ca1\u4e8b\u4e86", "\u4e0d\u96be\u53d7\u4e86", "\u4e0d\u54b3\u4e86", "\u9000\u70e7\u4e86",
)

ACTIVE_STATUS_TERMS = (
    "\u4e86", "\u4e0d\u8212\u670d", "\u96be\u53d7", "\u53c8", "\u6709\u70b9", "\u6700\u8fd1", "\u4eca\u5929",
)


def _detect_health_groups(text: str) -> set[str]:
    source = text or ""
    groups = set()
    for group, terms in HEALTH_STATUS_GROUPS.items():
        if any(term in source for term in terms):
            groups.add(group)
    return groups


def _is_resolved_health_status(entry: str, source_msg: str | None = None) -> bool:
    text = f"{entry or ''} {source_msg or ''}"
    return bool(_detect_health_groups(text) and any(term in text for term in RESOLVED_STATUS_TERMS))


def _is_active_health_status(entry: str, source_msg: str | None = None) -> bool:
    text = f"{entry or ''} {source_msg or ''}"
    if not _detect_health_groups(text):
        return False
    if _is_resolved_health_status(entry, source_msg):
        return False
    return True


def _health_status_expires_at(entry: str, source_msg: str | None = None) -> str | None:
    if _is_active_health_status(entry, source_msg):
        return (datetime.now(timezone.utc).replace(microsecond=0) + __import__("datetime").timedelta(days=14)).isoformat()
    return None

def _extract_user_name_memory(entry: str) -> str | None:
    """Return a likely explicit user name from a normalized memory entry."""
    text = " ".join((entry or "").strip().split())
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in ("assistant", "ai", "bot", "hermes", "助手", "机器人")):
        return None
    patterns = (
        r"^用户(?:的)?(?:名字|姓名|名称|称呼)(?:是|叫)\s*([^：；，。！？;,.!?:\s]+)",
        r"^用户名(?:是|叫)\s*([^：；，。！？;,.!?:\s]+)",
        r"^user[_ ]?name[_ ]?is[:?]\s*([^：；，。！？;,.!?:\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            reject_terms = (
                "问", "几天", "多久", "没见", "好久", "我俩", "我们", "你", "吗", "嘛", "呢",
                "什么", "谁", "哪个", "哪位", "哪里", "怎么", "为什么", "是不是", "有没有",
            )
            if (
                not candidate
                or len(candidate) > 12
                or candidate in {"你", "我", "自己", "谁", "什么", "什么角色", "哪个", "哪位"}
                or any(term in candidate for term in reject_terms)
                or candidate.endswith(("？", "?", "吗", "嘛", "呢"))
            ):
                return None
            return candidate
    return None

class MemoryDB:
    """SQLite memory database manager."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.vector_store = MemoryVectorStore.from_env()
        self._init_db()
        self._sync_vector_store_on_start()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            columns = set()
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
            except Exception:
                columns = set()
            if columns and "status" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'active'")
            if columns and "resolved_at" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN resolved_at TEXT")
            if columns and "expires_at" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
            conn.executescript(SCHEMA)
            self._migrate_schema(conn)

    def _migrate_schema(self, conn):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "status" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'active'")
        if "resolved_at" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN resolved_at TEXT")
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at)")

    def _sync_vector_store_on_start(self):
        if not self.vector_store or not self.vector_store.available():
            return
        if (_profile_env_value("HERMISS_MILVUS_SYNC_ON_START") or "true").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE COALESCE(status, 'active') = 'active'
                      AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (int(_profile_env_value("HERMISS_MILVUS_SYNC_LIMIT") or 5000),),
                ).fetchall()
                active_ids = {int(row["id"]) for row in rows}
                for row in rows:
                    self.vector_store.upsert_memory(dict(row))
                pruned = self.vector_store.prune_except(active_ids)
            print(f"[message-analyzer] Milvus sync complete: {len(rows)} memories, pruned={pruned}")
        except Exception as exc:
            print(f"[message-analyzer] Milvus sync failed: {exc}")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Memory CRUD ──────────────────────────────────────────

    def insert_memory(
        self,
        entry: str,
        category: str,
        *,
        importance: str = "medium",
        emotion: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        source_msg: str | None = None,
    ) -> int | None:
        """Insert a memory. Returns row id, or None if duplicate."""
        entry_clean = entry.strip()
        if not entry_clean:
            return None

        now = datetime.now(timezone.utc).isoformat()
        status = "active"
        resolved_at = None
        expires_at = _health_status_expires_at(entry_clean, source_msg)
        resolved_groups = _detect_health_groups(f"{entry_clean} {source_msg or ''}") if _is_resolved_health_status(entry_clean, source_msg) else set()
        if resolved_groups:
            status = "resolved"
            resolved_at = now
            expires_at = None

        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id FROM memories WHERE LOWER(entry) = LOWER(?) LIMIT 1",
                (entry_clean,),
            )
            if cur.fetchone():
                return None

            if category == "fact" and _extract_user_name_memory(entry_clean):
                cur = conn.execute(
                    """
                    SELECT id FROM memories
                    WHERE category = 'fact'
                      AND (
                        entry LIKE '用户的名字是%'
                        OR entry LIKE '用户名字是%'
                        OR entry LIKE '用户叫%'
                        OR entry LIKE '用户名叫%'
                        OR entry LIKE '我叫%'
                        OR entry LIKE '我的名字是%'
                        OR LOWER(entry) LIKE 'user_name_is:%'
                      )
                      AND entry NOT LIKE '%不是%'
                      AND LOWER(entry) NOT LIKE '%assistant%'
                      AND LOWER(entry) NOT LIKE '%bot%'
                      AND LOWER(entry) NOT LIKE '%hermes%'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                )
                row = cur.fetchone()
                if row:
                    memory_id = int(row[0])
                    conn.execute(
                        """
                        UPDATE memories
                        SET entry=?, source_msg=?, importance=?, emotion=?,
                            session_id=COALESCE(?, session_id),
                            user_id=COALESCE(?, user_id),
                            status=?, resolved_at=?, expires_at=?
                        WHERE id=?
                        """,
                        (entry_clean, source_msg, importance, emotion, session_id, user_id, status, resolved_at, expires_at, memory_id),
                    )
                    self._upsert_vector_by_id(conn, memory_id)
                    return memory_id

            if resolved_groups:
                like_sql, like_params = _build_like_filter(("entry", "source_msg"), tuple(term for group in resolved_groups for term in HEALTH_STATUS_GROUPS[group]))
                rows = conn.execute(
                    f"""
                    SELECT id FROM memories
                    WHERE category = 'fact'
                      AND COALESCE(status, 'active') = 'active'
                      AND ({like_sql})
                    ORDER BY created_at DESC, id DESC
                    """,
                    like_params,
                ).fetchall()
                for row in rows:
                    resolved_id = int(row["id"])
                    conn.execute(
                        """
                        UPDATE memories
                        SET status='resolved', resolved_at=?, expires_at=NULL
                        WHERE id=?
                        """,
                        (now, resolved_id),
                    )
                    self._delete_vector(resolved_id)

            cur = conn.execute(
                """
                INSERT INTO memories(category, entry, source_msg, importance, emotion, created_at, session_id, user_id, status, resolved_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (category, entry_clean, source_msg, importance, emotion, now, session_id, user_id, status, resolved_at, expires_at),
            )
            memory_id = int(cur.lastrowid)
            self._upsert_vector_by_id(conn, memory_id)
            return memory_id

    def get_memory_by_id(self, memory_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return dict(row) if row else None

    def delete_memory(self, memory_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            if cur.rowcount > 0:
                self._delete_vector(memory_id)
            return cur.rowcount > 0

    def _upsert_vector_by_id(self, conn, memory_id: int) -> None:
        if not self.vector_store:
            return
        try:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
            if row:
                self.vector_store.upsert_memory(dict(row))
        except Exception as exc:
            print(f"[message-analyzer] Milvus upsert failed for memory {memory_id}: {exc}")

    def _delete_vector(self, memory_id: int) -> None:
        if not self.vector_store:
            return
        try:
            self.vector_store.delete_memory(int(memory_id))
        except Exception as exc:
            print(f"[message-analyzer] Milvus delete failed for memory {memory_id}: {exc}")

    # ── Broad Recall (粗筛) ──────────────────────────────────

    def broad_recall(
        self,
        message: str,
        *,
        limit: int = 30,
        recency_days: int = 90,
    ) -> list[dict]:
        """
        Broad recall with priority lanes.

        1. Milvus vector recall for nearest-neighbour candidates.
        2. FTS5 full-text search on message keywords.
        3. Concern/status recall for health-sensitive actions.
        4. Recent memories as a wide fallback.
        5. High-importance memories always pinned.
        """
        with self._conn() as conn:
            results = {}
            priority_ids: set[int] = set()

            def remember(rows, *, priority: bool = False):
                for r in rows:
                    item = dict(r)
                    memory_id = int(item["id"])
                    results[memory_id] = item
                    if priority:
                        priority_ids.add(memory_id)

            if self.vector_store:
                try:
                    vector_hits = self.vector_store.search(message, limit=limit)
                    ids = [int(item.get("id")) for item in vector_hits if int(item.get("id") or 0) > 0]
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        rows = conn.execute(
                            f"""
                            SELECT * FROM memories
                            WHERE id IN ({placeholders})
                              AND COALESCE(status, 'active') = 'active'
                              AND (expires_at IS NULL OR expires_at > datetime('now'))
                            """,
                            ids,
                        ).fetchall()
                        row_map = {int(row["id"]): dict(row) for row in rows}
                        for memory_id in ids:
                            row = row_map.get(memory_id)
                            if row:
                                results[memory_id] = row
                except Exception as exc:
                    print(f"[message-analyzer] Milvus recall failed: {exc}")

            keywords = _extract_keywords(message, max_words=10)
            if keywords:
                fts_query = " OR ".join(keywords)
                try:
                    rows = conn.execute(
                        """SELECT m.* FROM memories m
                           JOIN memories_fts f ON m.id = f.rowid
                           WHERE memories_fts MATCH ?
                             AND COALESCE(m.status, 'active') = 'active'
                             AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
                           ORDER BY rank
                           LIMIT ?""",
                        (fts_query, limit),
                    ).fetchall()
                    remember(rows)
                except sqlite3.OperationalError:
                    pass

            if _message_may_need_concern_recall(message):
                like_sql, like_params = _build_like_filter(("entry", "source_msg"), CONCERN_MEMORY_TERMS)
                rows = conn.execute(
                    f"""SELECT * FROM memories
                        WHERE ({like_sql})
                          AND COALESCE(status, 'active') = 'active'
                          AND (expires_at IS NULL OR expires_at > datetime('now'))
                        ORDER BY
                            CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                            created_at DESC
                        LIMIT ?""",
                    (*like_params, max(8, limit // 3)),
                ).fetchall()
                remember(rows, priority=True)

            scenes = _detect_recall_scenes(message)
            scene_terms = _scene_search_terms(scenes)
            if scene_terms:
                like_sql, like_params = _build_like_filter(("entry", "source_msg", "category"), scene_terms)
                rows = conn.execute(
                    f"""SELECT * FROM memories
                        WHERE ({like_sql})
                          AND COALESCE(status, 'active') = 'active'
                          AND (expires_at IS NULL OR expires_at > datetime('now'))
                        ORDER BY
                            CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                            created_at DESC
                        LIMIT ?""",
                    (*like_params, max(8, limit // 2)),
                ).fetchall()
                remember(rows, priority=True)

            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE COALESCE(status, 'active') = 'active'
                     AND (expires_at IS NULL OR expires_at > datetime('now'))
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            remember(rows)

            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE importance = 'high'
                     AND COALESCE(status, 'active') = 'active'
                     AND (expires_at IS NULL OR expires_at > datetime('now'))
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (max(1, limit // 3),),
            ).fetchall()
            remember(rows, priority=True)

            sorted_results = sorted(
                results.values(),
                key=lambda r: (int(r.get("id", 0)) in priority_ids, r.get("created_at", "")),
                reverse=True,
            )
            return sorted_results[:limit]

    def search_by_category(self, category: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def memory_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    # ── Session ───────────────────────────────────────────────

    def save_session_summary(
        self,
        session_id: str,
        message_count: int,
        last_emotion: str | None = None,
        summary: str | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO session_summaries (session_id, ended_at, message_count, last_emotion, summary)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, now, message_count, last_emotion, summary),
            )

    def get_recent_sessions(self, limit: int = 5) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM session_summaries ORDER BY ended_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Maintenance ───────────────────────────────────────────

    def rebuild_fts(self):
        """Rebuild FTS index (run after bulk imports)."""
        with self._conn() as conn:
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_cat = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM memories GROUP BY category"
            ).fetchall()
            by_importance = conn.execute(
                "SELECT importance, COUNT(*) as cnt FROM memories GROUP BY importance"
            ).fetchall()
            return {
                "total_memories": total,
                "by_category": {r["category"]: r["cnt"] for r in by_cat},
                "by_importance": {r["importance"]: r["cnt"] for r in by_importance},
            }


def _extract_keywords(text: str, max_words: int = 10) -> list[str]:
    """
    Extract meaningful keywords for FTS5 search.
    Filters out stop words and short tokens.
    """
    stop_words = {
        "的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "这", "那",
        "不", "也", "就", "都", "要", "会", "吗", "呢", "吧", "啊", "哦", "嗯",
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "but", "or", "and", "if",
        "so", "no", "not", "too", "very", "just", "now", "then", "here",
        "there", "this", "that", "these", "those", "it", "its", "me", "my",
        "we", "our", "us", "your", "yours", "自己", "知道", "觉得", "想", "说",
        "什么", "怎么", "为什么", "因为", "所以", "但是", "可以", "应该",
    }

    # Simple tokenization: split on non-alphanumeric, filter short/stops
    tokens = re.findall(r"[\w一-鿿]+", text.lower())
    keywords = []
    for t in tokens:
        if len(t) < 2:
            continue
        if t in stop_words:
            continue
        keywords.append(f"{t}*")  # Prefix match for wider recall (Chinese + English)

    return keywords[:max_words]
