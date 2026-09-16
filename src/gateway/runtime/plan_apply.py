"""Apply a durable Tier-1 write plan with optimistic concurrency and item selection."""
from __future__ import annotations

from gateway.runtime import write_plans


def _normalize_items(items: list[dict] | None) -> list[dict]:
    normalized = []
    for entry in list(items or []):
        if isinstance(entry, str):
            normalized.append({"id": entry})
        elif isinstance(entry, dict):
            normalized.append(dict(entry))
    return normalized


def _requested_selection(payload_items: list[dict] | None) -> tuple[set[str], dict[str, dict]]:
    approved: set[str] = set()
    edits: dict[str, dict] = {}
    for entry in _normalize_items(payload_items):
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        status = str(entry.get("status") or "approved").strip().casefold()
        if status == "rejected":
            continue
        approved.add(item_id)
        payload = entry.get("payload")
        if isinstance(payload, dict) and payload:
            edits[item_id] = dict(payload)
    return approved, edits


def apply_plan_selection(plan: dict, payload_items: list[dict] | None) -> tuple[list[dict], dict[str, dict]]:
    stored = plan.get("items") if isinstance(plan, dict) else []
    stored_items = [entry for entry in (stored or []) if isinstance(entry, dict)]
    if not stored_items:
        return [], {}
    if payload_items is None:
        selected = [entry for entry in stored_items if str(entry.get("status") or "") != "rejected"]
        return selected, {str(entry.get("id") or ""): dict(entry.get("payload") or {}) for entry in selected}
    approved, edits = _requested_selection(payload_items)
    selected = []
    merged: dict[str, dict] = {}
    for entry in stored_items:
        item_id = str(entry.get("id") or "")
        if not item_id or str(entry.get("status") or "") == "rejected":
            continue
        if approved and item_id not in approved:
            continue
        selected.append(entry)
        payload = dict(entry.get("payload") or {})
        if item_id in edits:
            payload.update(edits[item_id])
        merged[item_id] = payload
    return selected, merged


def check_expected_modified(frappe, expected_modified: dict) -> None:
    expected = dict(expected_modified or {})
    for key, wanted in expected.items():
        wanted_text = str(wanted or "")
        if not wanted_text:
            continue
        if ":" not in str(key or ""):
            continue
        doctype, _, name = str(key).partition(":")
        if not doctype or not name:
            continue
        current = ((frappe.get_document(doctype, name) or {}).get("data") or {}).get("modified")
        if str(current or "") != wanted_text:
            raise ValueError(
                f"{doctype} {name} đã thay đổi (expected {wanted_text!r}, current {str(current or '')!r}). "
                "Hãy tải lại bản xem trước thay vì ghi đè."
            )


def collect_write_undo(changes: list[dict], before: dict[str, dict]) -> dict | None:
    restores: dict[str, dict] = {}
    for change in list(changes or []):
        if not isinstance(change, dict) or change.get("op") != "update":
            continue
        doctype = str(change.get("doctype") or "")
        name = str(change.get("name") or "")
        field = str(change.get("field") or "")
        snapshot = before.get(write_plans.plan_key(doctype, name)) or {}
        if doctype and name and field and field in snapshot:
            restores.setdefault(write_plans.plan_key(doctype, name), {"doctype": doctype, "name": name, "fields": {}})
            restores[write_plans.plan_key(doctype, name)]["fields"][field] = snapshot[field]
    if len(restores) == 1:
        only = next(iter(restores.values()))
        return {"op": "restore", "doctype": only["doctype"], "name": only["name"], "fields": only["fields"]}
    if restores:
        return {"op": "restore_many", "restores": list(restores.values())}
    return None
