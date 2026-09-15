"""Durable webhook inbox and reconciliation worker for Frappe events."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from gateway.connector.frappe_client import FrappeClient
from gateway.runtime import ingest, learner

EVENT_DOCTYPES = (
    "LMS Quiz Submission",
    "LMS Assignment Submission",
    "LMS Programming Exercise Submission",
    "LMS Course Progress",
    "LMS Enrollment",
    "Course Lesson",
)
POLL_DOCTYPES = EVENT_DOCTYPES + ("LMS Video Watch Duration",)


class EventProcessor:
    def __init__(
        self,
        frappe: FrappeClient,
        poll_interval: int = 900,
        path: Path | None = None,
    ):
        self.frappe = frappe
        self.poll_interval = max(30, int(poll_interval))
        self.path = path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._migrate()

    def _conn(self):
        return learner._conn(self.path)

    def _migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS event_inbox ("
                "id TEXT PRIMARY KEY, doctype TEXT NOT NULL, name TEXT NOT NULL, "
                "modified TEXT NOT NULL, payload TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, "
                "error TEXT, created_at REAL NOT NULL, processed_at REAL, "
                "UNIQUE(doctype, name, modified))"
            )

    def enqueue(self, payload: dict) -> dict:
        doctype = str(payload.get("doctype") or "").strip()
        name = str(payload.get("name") or "").strip()
        modified = str(payload.get("modified") or "").strip()
        if doctype not in POLL_DOCTYPES:
            raise ValueError(f"unsupported doctype: {doctype}")
        if not name or not modified:
            raise ValueError("name and modified are required")
        with self._conn() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO event_inbox "
                "(id, doctype, name, modified, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    doctype,
                    name,
                    modified,
                    json.dumps(payload, ensure_ascii=False),
                    time.time(),
                ),
            ).rowcount
        return {"queued": bool(inserted), "duplicate": not bool(inserted)}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="frappe-event-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        next_poll = time.monotonic() + 5
        while not self._stop.wait(1):
            self.process_pending()
            if time.monotonic() >= next_poll:
                try:
                    self.reconcile()
                except Exception:
                    # Frappe downtime must not kill the long-lived worker.
                    pass
                finally:
                    next_poll = time.monotonic() + self.poll_interval

    def _pending(self, limit: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM event_inbox WHERE status IN ('pending', 'failed') "
                "AND attempts < 5 ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def process_pending(self, limit: int = 50) -> int:
        processed = 0
        for event in self._pending(limit):
            try:
                ingest.handle_event_payload(self.frappe, event, self.path)
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE event_inbox SET status='processed', attempts=attempts+1, "
                        "processed_at=?, error=NULL WHERE id=?",
                        (time.time(), event["id"]),
                    )
                processed += 1
            except Exception as exc:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE event_inbox SET status='failed', attempts=attempts+1, error=? WHERE id=?",
                        (str(exc)[:1000], event["id"]),
                    )
        return processed

    def reconcile(self, doctypes: tuple[str, ...] = POLL_DOCTYPES) -> int:
        queued = 0
        for doctype in doctypes:
            try:
                cursor = ingest._cursor_get(doctype, self.path)
                filters = [["modified", ">", cursor]] if cursor else []
                rows = self.frappe.list_documents(
                    doctype,
                    filters=filters,
                    fields=["name", "modified"],
                    limit=200,
                    order_by="modified asc",
                ).get("data", [])
            except Exception:
                continue
            for row in rows:
                queued += int(
                    self.enqueue(
                        {"doctype": doctype, "name": row["name"], "modified": row["modified"]}
                    )["queued"]
                )
            if rows:
                ingest._cursor_set(doctype, str(rows[-1]["modified"]), self.path)
        return queued

    def stats(self) -> dict:
        with self._conn() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM event_inbox WHERE status IN ('pending', 'failed')"
            ).fetchone()[0]
            evidence = conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            concepts = conn.execute("SELECT COUNT(*) FROM concept").fetchone()[0]
        return {"pending_events": pending, "evidence": evidence, "concepts": concepts}
