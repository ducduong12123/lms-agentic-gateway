"""Long-term personal memory for the gateway widget.

Port kiến trúc hybrid của D:/AI-Memory-RAG/memory_cli.py (BM25 + vector + RRF)
sang lưu trữ nhẹ trong gateway.db (SQLite, stdlib-only):

- Scope theo Frappe user (chống lẫn ký ức giữa các user).
- Guest không được lưu/đọc ký ức dài hạn (tránh bucket chung).
- Vector offline hashing 384 chiều (không thêm dependency, giống provider
  "hash" của bản mẫu). Có thể nâng lên embedding API sau mà không đổi schema.
- BM25 tính on-the-fly trên memories của từng user (personal memory nhỏ).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from gateway.runtime.trace import trace as _trace

DB = Path(__file__).resolve().parents[3] / "data" / "gateway.db"

DIMENSION = 384
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_FORGOTTEN = {"superseded", "deleted", "forgotten"}


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS long_memories"
        "(id TEXT PRIMARY KEY, user TEXT NOT NULL, kind TEXT DEFAULT 'user_fact',"
        " title TEXT DEFAULT '', text TEXT NOT NULL, search_text TEXT NOT NULL,"
        " summary TEXT DEFAULT '', status TEXT DEFAULT 'active',"
        " importance REAL DEFAULT 0.8, confidence REAL DEFAULT 0.9,"
        " embedding TEXT NOT NULL, created REAL, updated REAL,"
        " supersedes TEXT, superseded_by TEXT)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_long_memories_user"
        " ON long_memories(user, status)"
    )
    return c


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(text or "")]


def _stable_bucket(value: str, dimension: int = DIMENSION) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dimension


def _normalize(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector
    return [value / length for value in vector]


def hashing_embedding(text: str, dimension: int = DIMENSION) -> list[float]:
    """Offline hashing vector, cùng công thức với memory_cli provider hash."""
    vector = [0.0] * dimension
    for token in tokenize(text):
        vector[_stable_bucket("w:" + token, dimension)] += 1.0
        if len(token) >= 3:
            for index in range(len(token) - 2):
                vector[_stable_bucket("g:" + token[index : index + 3], dimension)] += 0.25
    compact = re.sub(r"\s+", " ", (text or "").casefold().strip())
    if len(compact) >= 4:
        for index in range(len(compact) - 3):
            vector[_stable_bucket("c:" + compact[index : index + 4], dimension)] += 0.05
    return _normalize(vector)


def _is_guest(user: str) -> bool:
    return not user or user.strip() == "" or user.strip() == "Guest"


def add_memory(
    user: str,
    text: str,
    title: str = "",
    kind: str = "user_fact",
    importance: float = 0.8,
    confidence: float = 0.9,
    path: Path | None = None,
) -> dict[str, Any]:
    user = str(user or "").strip()[:256]
    text = str(text or "").strip()
    if _is_guest(user):
        raise ValueError("Chưa đăng nhập nên không lưu ký ức dài hạn.")
    if not text:
        raise ValueError("text trống.")
    if len(text) > 2000:
        raise ValueError("text quá dài (tối đa 2000 ký tự).")
    title = str(title or "").strip()[:256]
    kind = str(kind or "user_fact").strip()[:64] or "user_fact"
    try:
        importance = float(importance)
    except (TypeError, ValueError):
        importance = 0.8
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.9
    search_text = " ".join(value for value in [title, text] if value)
    record_id = "mem_" + uuid.uuid4().hex[:8]
    now = time.time()
    embedding = hashing_embedding(search_text, DIMENSION)
    c = _conn(path)
    c.execute(
        "INSERT INTO long_memories(id, user, kind, title, text, search_text, summary,"
        " status, importance, confidence, embedding, created, updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record_id, user, kind, title, text, search_text, text[:400],
            "active", importance, confidence, json.dumps(embedding),
            now, now,
        ),
    )
    c.commit()
    c.close()
    try:
        _trace("fact", "remember", member=user, memory_id=record_id, kind=kind, title=title, text=text)
    except Exception:
        pass
    return {"id": record_id, "user": user, "kind": kind, "title": title, "text": text}


def _load_active(user: str, path: Path | None = None) -> list[dict[str, Any]]:
    c = _conn(path)
    rows = c.execute(
        "SELECT id, kind, title, text, search_text, importance, confidence, embedding"
        " FROM long_memories WHERE user=? AND status='active' ORDER BY created ASC",
        (user,),
    ).fetchall()
    c.close()
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            embedding = json.loads(row["embedding"] or "[]")
        except (TypeError, ValueError):
            embedding = []
        records.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"] or "",
                "text": row["text"] or "",
                "search_text": row["search_text"] or row["text"] or "",
                "importance": row["importance"],
                "confidence": row["confidence"],
                "embedding": embedding,
            }
        )
    return records


def _bm25_search(
    records: list[dict[str, Any]], query: str, limit: int
) -> list[dict[str, Any]]:
    query_terms = Counter(tokenize(query))
    if not query_terms:
        return []
    doc_freq: Counter[str] = Counter()
    for record in records:
        doc_freq.update(set(tokenize(record.get("search_text", ""))))
    total = max(1, len(records))
    lengths = [len(tokenize(record.get("search_text", ""))) for record in records]
    avgdl = (sum(lengths) / len(lengths)) if lengths else 1.0
    avgdl = avgdl or 1.0
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, record in enumerate(records):
        terms = Counter(tokenize(record.get("search_text", "")))
        doc_len = len(terms)
        score = 0.0
        for term, query_frequency in query_terms.items():
            if terms[term] == 0:
                continue
            df = int(doc_freq.get(term, 0))
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            numerator = terms[term] * (BM25_K1 + 1.0)
            denominator = terms[term] + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / avgdl)
            score += idf * numerator / denominator * min(1.0, query_frequency)
        if score > 0:
            scored.append((score, index, record))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"record": record, "score": score} for score, _, record in scored[:limit]]


def _cosine_search(
    records: list[dict[str, Any]], query: str, limit: int
) -> list[dict[str, Any]]:
    query_vector = hashing_embedding(query, DIMENSION)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, record in enumerate(records):
        vector = record.get("embedding")
        if not isinstance(vector, list) or len(vector) != len(query_vector):
            continue
        score = sum(float(a) * float(b) for a, b in zip(query_vector, vector))
        if score > 0:
            scored.append((score, index, record))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"record": record, "score": score} for score, _, record in scored[:limit]]


def _rrf_merge(
    lexical: list[dict[str, Any]], semantic: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(lexical, start=1):
        record = item["record"]
        current = merged.setdefault(record["id"], {"record": record, "rrf_score": 0.0})
        current["rrf_score"] += 1.0 / (RRF_K + rank)
        current["bm25_rank"] = rank
        current["bm25_score"] = item["score"]
    for rank, item in enumerate(semantic, start=1):
        record = item["record"]
        current = merged.setdefault(record["id"], {"record": record, "rrf_score": 0.0})
        current["rrf_score"] += 1.0 / (RRF_K + rank)
        current["vector_rank"] = rank
        current["vector_score"] = item["score"]
    results = list(merged.values())
    results.sort(key=lambda item: -item["rrf_score"])
    return results[:limit]


def search_memories(
    user: str,
    query: str,
    top_k: int = 4,
    candidate_k: int = 20,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Hybrid BM25 + hashing-vector + RRF, scope theo user."""
    user = str(user or "").strip()
    query = str(query or "").strip()
    if _is_guest(user) or not query:
        return []
    top_k = max(1, min(8, int(top_k or 4)))
    candidate_k = max(top_k, min(50, int(candidate_k or 20)))
    records = _load_active(user, path)
    if not records:
        return []
    lexical = _bm25_search(records, query, candidate_k)
    semantic = _cosine_search(records, query, candidate_k)
    merged = _rrf_merge(lexical, semantic, top_k)
    out: list[dict[str, Any]] = []
    for item in merged:
        record = item["record"]
        out.append(
            {
                "id": record["id"],
                "kind": record.get("kind", "user_fact"),
                "title": record.get("title", ""),
                "text": record.get("text", ""),
                "importance": record.get("importance"),
                "confidence": record.get("confidence"),
                "rrf_score": item["rrf_score"],
                "bm25_rank": item.get("bm25_rank"),
                "vector_rank": item.get("vector_rank"),
            }
        )
    try:
        _trace(
            "fact", "recall", member=user, query=query,
            memory_ids=[item["id"] for item in out],
            rrf=[round(float(item["rrf_score"]), 6) for item in out],
        )
    except Exception:
        pass
    return out


def forget_memory(user: str, memory_id: str, path: Path | None = None) -> bool:
    """Soft-delete ký ức của đúng user (giữ lịch sử, loại khỏi retrieval)."""
    user = str(user or "").strip()
    memory_id = str(memory_id or "").strip()
    if _is_guest(user) or not memory_id:
        return False
    c = _conn(path)
    cur = c.execute(
        "UPDATE long_memories SET status='forgotten', updated=? "
        "WHERE id=? AND user=? AND status='active'",
        (time.time(), memory_id, user),
    )
    c.commit()
    changed = cur.rowcount > 0
    c.close()
    try:
        _trace("fact", "forget", member=user, memory_id=memory_id, ok=changed)
    except Exception:
        pass
    return changed


def format_memories_block(memories: list[dict[str, Any]], max_items: int = 4) -> str:
    lines: list[str] = []
    for item in (memories or [])[:max_items]:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        if len(text) > 300:
            text = text[:297] + "..."
        title = str(item.get("title") or "").strip()
        lines.append(f"- {(title + ': ' if title else '')}{text}")
    if not lines:
        return ""
    return (
        "Thông tin đã nhớ về người học "
        "(dùng để cá nhân hóa, đừng đọc lại nguyên văn trừ khi được hỏi):\n"
        + "\n".join(lines)
    )
