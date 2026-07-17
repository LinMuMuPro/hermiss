"""
Milvus-backed memory vector store.

SQLite remains the source of truth for memory metadata and panel CRUD.
Milvus stores active memory vectors for semantic/nearest-neighbour recall.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _profile_env_value(name: str) -> str | None:
    if name in os.environ:
        return os.environ.get(name)

    profile = os.environ.get("HERMES_PROFILE", "hermiss")
    candidates = []
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        candidates.append(Path(hermes_home) / ".env")
    candidates.extend([
        Path.home() / ".hermes" / "profiles" / profile / ".env",
        Path.home() / ".hermes" / ".env",
    ])

    for env_path in candidates:
        try:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return None


def _env_bool(name: str, default: bool = True) -> bool:
    raw = _profile_env_value(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_profile(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "hermiss")[:64] or "hermiss"


def _safe_collection(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value or "hermiss_memories")[:255]
    if not cleaned or not re.match(r"^[A-Za-z_]", cleaned):
        cleaned = f"hermiss_{cleaned or 'memories'}"
    return cleaned


def _hash_embedding(text: str, dim: int) -> list[float]:
    """
    Dependency-free fallback embedding.

    It is not as semantically strong as a real embedding model, but it makes the
    Milvus path deterministic and available in the current Hermes image. A real
    embedding provider can replace this function later without changing storage.
    """
    vector = [0.0] * dim
    source = (text or "").lower()
    tokens = re.findall(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]", source)
    grams: list[str] = []
    grams.extend(tokens)
    compact = "".join(tokens)
    for size in (2, 3, 4):
        grams.extend(compact[i:i + size] for i in range(max(0, len(compact) - size + 1)))
    if not grams:
        grams = [source[:64] or "empty"]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]


@dataclass
class MemoryVectorStore:
    host: str
    port: str
    collection_name: str
    profile: str
    dim: int = 384
    enabled: bool = True

    _collection: Any = None
    _available: bool = False

    @classmethod
    def from_env(cls) -> "MemoryVectorStore | None":
        backend = (_profile_env_value("HERMISS_MEMORY_VECTOR_BACKEND") or "milvus").strip().lower()
        if backend not in {"milvus", "true", "1", "on", "enabled"}:
            return None
        return cls(
            host=_profile_env_value("HERMISS_MILVUS_HOST") or "hermiss-milvus",
            port=_profile_env_value("HERMISS_MILVUS_PORT") or "19530",
            collection_name=_safe_collection(_profile_env_value("HERMISS_MILVUS_COLLECTION") or "hermiss_memories"),
            profile=_safe_profile(_profile_env_value("HERMISS_MEMORY_NAMESPACE") or _profile_env_value("HERMES_PROFILE") or "hermiss"),
            dim=int(_profile_env_value("HERMISS_MEMORY_VECTOR_DIM") or 384),
            enabled=_env_bool("HERMISS_MEMORY_VECTOR_ENABLED", True),
        )

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self._available and self._collection is not None:
            return True
        try:
            self._connect()
            return self._available
        except Exception as exc:
            print(f"[message-analyzer] Milvus unavailable: {exc}")
            self._available = False
            return False

    def _connect(self) -> None:
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

        connections.connect(alias="default", host=self.host, port=self.port)
        if not utility.has_collection(self.collection_name):
            fields = [
                FieldSchema(name="pk", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
                FieldSchema(name="profile", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="memory_id", dtype=DataType.INT64),
                FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="importance", dtype=DataType.VARCHAR, max_length=16),
                FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=16),
                FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="entry", dtype=DataType.VARCHAR, max_length=4096),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
            ]
            schema = CollectionSchema(fields, description="Hermiss memory vectors")
            collection = Collection(self.collection_name, schema=schema)
            collection.create_index(
                "vector",
                {
                    "metric_type": "IP",
                    "index_type": "HNSW",
                    "params": {"M": 16, "efConstruction": 128},
                },
            )
        else:
            collection = Collection(self.collection_name)
        collection.load()
        self._collection = collection
        self._available = True

    def _pk(self, memory_id: int) -> str:
        return f"{self.profile}:{int(memory_id)}"

    def upsert_memory(self, memory: dict) -> None:
        if not self.available():
            return
        memory_id = int(memory.get("id") or 0)
        if memory_id <= 0:
            return
        if str(memory.get("status") or "active") != "active":
            self.delete_memory(memory_id)
            return
        entry = str(memory.get("entry") or "").strip()
        if not entry:
            self.delete_memory(memory_id)
            return
        pk = self._pk(memory_id)
        self._collection.delete(f'pk == "{pk}"')
        self._collection.insert([{
            "pk": pk,
            "profile": self.profile,
            "memory_id": memory_id,
            "category": str(memory.get("category") or "fact")[:32],
            "importance": str(memory.get("importance") or "medium")[:16],
            "status": str(memory.get("status") or "active")[:16],
            "created_at": str(memory.get("created_at") or "")[:64],
            "entry": entry[:4096],
            "vector": _hash_embedding(entry, self.dim),
        }])
        self._collection.flush()

    def delete_memory(self, memory_id: int) -> None:
        if not self.available():
            return
        self._collection.delete(f'pk == "{self._pk(int(memory_id))}"')
        self._collection.flush()

    def prune_except(self, active_memory_ids: set[int]) -> int:
        """
        Delete stale vectors for this profile that no longer exist as active
        SQLite memories. SQLite remains the source of truth.
        """
        if not self.available():
            return 0
        active_ids = {int(memory_id) for memory_id in active_memory_ids if int(memory_id) > 0}
        rows = self._collection.query(
            expr=f'profile == "{self.profile}"',
            output_fields=["pk", "memory_id"],
            limit=16384,
        )
        stale_pks = [
            str(row.get("pk") or self._pk(int(row.get("memory_id") or 0)))
            for row in rows
            if int(row.get("memory_id") or 0) not in active_ids
        ]
        for pk in stale_pks:
            self._collection.delete(f'pk == "{pk}"')
        if stale_pks:
            self._collection.flush()
        return len(stale_pks)

    def search(self, message: str, *, limit: int = 30) -> list[dict]:
        if not self.available():
            return []
        vector = _hash_embedding(message or "", self.dim)
        results = self._collection.search(
            data=[vector],
            anns_field="vector",
            param={"metric_type": "IP", "params": {"ef": 64}},
            limit=max(1, int(limit)),
            expr=f'profile == "{self.profile}" and status == "active"',
            output_fields=["memory_id", "category", "importance", "entry", "created_at"],
        )
        rows: list[dict] = []
        for hit in (results[0] if results else []):
            entity = hit.entity
            rows.append({
                "id": int(entity.get("memory_id")),
                "score": float(hit.score),
                "category": entity.get("category"),
                "importance": entity.get("importance"),
                "entry": entity.get("entry"),
                "created_at": entity.get("created_at"),
            })
        return rows
