"""Frappe webhook verification, event logging, and ITS evidence ingest."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

LOG = Path(__file__).resolve().parents[3] / "data" / "webhooks.log"

INGEST_DOCTYPES = {
    "LMS Quiz Submission",
    "LMS Assignment Submission",
    "LMS Programming Exercise Submission",
    "LMS Course Progress",
    "LMS Video Watch Duration",
    "Course Lesson",
}


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify Frappe's base64-encoded HMAC-SHA256 signature."""
    if not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    supplied = (signature or "").removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, supplied)


def should_ingest(payload: dict) -> bool:
    return str(payload.get("doctype") or "") in INGEST_DOCTYPES and bool(payload.get("name"))


def handle_event(headers: dict, body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        payload = {"raw": body.decode(errors="ignore")[:1000]}
    doctype = payload.get("doctype") or headers.get("X-Frappe-Doctype", "?")
    event = headers.get("X-Frappe-Event", "on_change")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} {doctype} "
            f"{json.dumps(payload, ensure_ascii=False)[:1000]}\n"
        )
    if isinstance(payload, dict) and should_ingest(payload):
        return {"ok": True, "doctype": doctype, "event": event, "ingest": "queued"}
    return {"ok": True, "doctype": doctype, "event": event}
