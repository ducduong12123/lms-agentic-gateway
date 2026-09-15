from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from gateway.api.webhook import verify_signature
from gateway.runtime import approval
from gateway.runtime.agent_loop import _run_tool
from gateway.runtime.identity import IdentityResolver, map_role
from gateway.runtime.tool_registry import Tool, ToolRegistry


def test_model_cannot_override_private_args_or_member(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "DB", tmp_path / "approval.db")
    executed = []
    registry = ToolRegistry()
    registry.register(
        Tool(
            "dangerous",
            "write",
            {"type": "object"},
            lambda args: executed.append(args),
            needs_approval=True,
            approval_roles={"admin"},
        )
    )
    args = {"member": "victim@example.com", "_role": "admin", "_approved": True}

    result, pending = _run_tool(
        registry,
        ["dangerous"],
        "student",
        "dangerous",
        args,
        {"user": "learner@example.com"},
    )

    assert result["needs_approval"] is True
    assert pending["approval_id"]
    assert args["member"] == "learner@example.com"
    assert args["_role"] == "student"
    assert "_approved" not in args
    assert executed == []


def test_approval_is_role_checked_and_single_use(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "DB", tmp_path / "approval.db")
    approval_id = approval.request_approval(
        "dangerous",
        {"value": 1},
        "teacher@example.com",
        "teacher",
        {"admin"},
    )

    with pytest.raises(PermissionError):
        approval.approve(approval_id, "teacher@example.com", "teacher")
    claimed = approval.approve(approval_id, "admin@example.com", "admin")
    assert claimed and claimed["tool"] == "dangerous"
    assert approval.approve(approval_id, "admin@example.com", "admin") is None


def test_frappe_webhook_requires_base64_hmac():
    body = b'{"doctype":"LMS Quiz Submission","name":"Q-1"}'
    secret = "test-secret"
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

    assert verify_signature(secret, body, signature)
    assert not verify_signature("", body, signature)
    assert not verify_signature(secret, body, hashlib.sha256(body).hexdigest())


def test_role_mapping_uses_server_roles():
    assert map_role(["LMS Student"]) == "student"
    assert map_role(["Batch Evaluator"]) == "evaluator"
    assert map_role(["Course Creator"]) == "teacher"
    assert map_role(["Course Creator", "Moderator"]) == "admin"


class _FakeFrappe:
    def with_session(self, sid):
        assert sid == "trusted-session"
        return self

    def get_method(self, method, **kwargs):
        if method == "frappe.auth.get_logged_user":
            return {"message": "teacher@example.com"}
        return {"message": {"name": "teacher@example.com", "roles": ["Course Creator"]}}


def test_identity_is_resolved_from_frappe_session():
    identity = IdentityResolver(_FakeFrappe()).resolve("trusted-session")
    assert identity.user == "teacher@example.com"
    assert identity.role == "teacher"
