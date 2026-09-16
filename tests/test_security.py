from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.request
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gateway.api.webhook import verify_signature
from gateway.runtime import approval
from gateway.runtime.agent_loop import _run_tool
from gateway.runtime.identity import IdentityResolver, map_role
from gateway.runtime.tool_registry import Tool, ToolRegistry
from gateway.api import server


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


def test_session_write_reads_and_sends_frappe_csrf_token(monkeypatch):
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/lms"):
            return Response(b'window["csrf_token"] = "csrf-token";')
        return Response(b'{"data":{"name":"lesson-1"}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from gateway.connector.frappe_client import FrappeClient

    result = FrappeClient(
        "http://lms.localhost",
        session_id="trusted-session",
        site_host="lms.localhost",
    ).update_document("Course Lesson", "lesson-1", {"body": "ok"})

    assert result["data"]["name"] == "lesson-1"
    assert requests[0].full_url == "http://lms.localhost/lms"
    assert requests[1].get_header("X-frappe-csrf-token") == "csrf-token"
    assert requests[1].get_header("Cookie") == "sid=trusted-session"


def test_same_origin_accepts_direct_lms_origin_and_referer_fallback(monkeypatch):
    monkeypatch.setattr(server.settings, "public_origins", ("http://localhost:8000",))

    server._require_same_origin(SimpleNamespace(headers={"origin": "http://localhost:8000"}))
    server._require_same_origin(SimpleNamespace(headers={"referer": "http://localhost:8000/lms"}))

    with pytest.raises(HTTPException) as exc_info:
        server._require_same_origin(SimpleNamespace(headers={"origin": "http://evil.test"}))
    assert exc_info.value.status_code == 403
    assert "origin not allowed" in exc_info.value.detail
