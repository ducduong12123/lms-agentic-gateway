"""Webhook receiver: nhận event từ Frappe, verify HMAC, ghi log tối giản."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

LOG = Path(__file__).resolve().parents[3] / "data" / "webhooks.log"


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    if not secret:
        return True  # demo mode, prod phải set secret
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature or "")


def handle_event(headers: dict, body: bytes) -> dict:
    try:
        payload = json.loads(body.decode() or "{}")
    except Exception:
        payload = {"raw": body.decode(errors="ignore")[:1000]}
    doctype = payload.get("doctype") or headers.get("X-Frappe-Doctype", "?")
    event = headers.get("X-Frappe-Event", "on_change")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} {doctype} {json.dumps(payload, ensure_ascii=False)[:1000]}\n")
    # TODO MVP+1: invalidate vector index theo (doctype, name, modified)
    return {"ok": True, "doctype": doctype, "event": event}
