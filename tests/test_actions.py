import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import actions


class FakeFrappe:
    def __init__(self):
        self.calls = []

    def delete_document(self, doctype, name):
        self.calls.append(("delete", doctype, name))
        return {"ok": True}

    def update_document(self, doctype, name, fields):
        self.calls.append(("update", doctype, name, fields))
        return {"ok": True}


def test_record_undo_expiry_and_second_undo(tmp_path, monkeypatch):
    db = tmp_path / "actions.db"
    now = [1000.0]
    monkeypatch.setattr(actions.time, "time", lambda: now[0])
    action_id = actions.record_action(
        "student@example.com", "enroll_course", {"course": "PY-101"}, {"status": "done"},
        {"op": "delete", "doctype": "LMS Enrollment", "name": "ENR-1", "expires_at": 1010.0}, path=db,
    )
    frappe = FakeFrappe()
    assert actions.undo_action(action_id, frappe, member="student@example.com", path=db)["status"] == "undone"
    assert frappe.calls == [("delete", "LMS Enrollment", "ENR-1")]
    with pytest.raises(ValueError, match="cannot be undone"):
        actions.undo_action(action_id, frappe, member="student@example.com", path=db)

    action_id = actions.record_action(
        "student@example.com", "save_note", {}, {"status": "done"},
        {"op": "delete", "doctype": "LMS Lesson Note", "name": "NOTE-1", "expires_at": 1010.0}, path=db,
    )
    now[0] = 1011.0
    with pytest.raises(ValueError, match="expired"):
        actions.undo_action(action_id, frappe, member="student@example.com", path=db)
