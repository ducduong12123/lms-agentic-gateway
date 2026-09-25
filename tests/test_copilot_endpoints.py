import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.api import server
from gateway.runtime import agent_loop, approval, learner, tutor, weekly_insight
from gateway.runtime.identity import Identity
from gateway.tools import copilot_bridge
from gateway.tools.catalog import build_registry

ORIGIN = {"Origin": "http://localhost:8080"}


class SessionFrappe:
    """Giả FrappeClient: ghi lại mọi lời gọi cùng session đã dùng."""

    def __init__(self, calls, sid="", configured=True):
        self.calls = calls
        self.session_id = sid
        self.configured = configured

    @property
    def is_configured(self):
        return self.configured

    def with_session(self, sid):
        return SessionFrappe(self.calls, sid)

    def rate_copilot_answer(self, conversation, message_index, helpful):
        self.calls.append(("rate", self.session_id, conversation, message_index, helpful))
        return {"conversation": conversation, "message_index": message_index, "helpful": "Helpful"}

    def call_copilot_tool(self, tool, arguments=None, **kwargs):
        self.calls.append((tool, self.session_id, arguments, kwargs))
        return {"proposal": "CPP-1"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(tutor, "DB", tmp_path / "tutor.db")
    monkeypatch.setattr(server.settings, "public_origins", ("http://localhost:8080",))
    monkeypatch.setattr(server.settings, "copilot_job_key", "job-secret")
    calls = []
    monkeypatch.setattr(server, "_base_frappe", lambda: SessionFrappe(calls, configured=True))
    identities = {
        "sid-student": Identity("hv@test.com", "student", ("LMS Student",), "sid-student"),
        "sid-teacher": Identity("gv@test.com", "teacher", ("Course Creator",), "sid-teacher"),
    }

    def resolve(sid):
        if sid not in identities:
            raise PermissionError("invalid Frappe session")
        return identities[sid]

    monkeypatch.setattr(server.identity_resolver, "resolve", resolve)
    jobs = []

    def fake_job(frappe, llm, course, week_start=None, model=""):
        jobs.append({"sid": frappe.session_id, "course": course, "week_start": week_start})
        return {"course": course, "status": "saved", "groups": 1, "link": weekly_insight.insight_link(course)}

    monkeypatch.setattr(server.weekly_insight, "run_weekly_job", fake_job)
    client = TestClient(server.app)
    return client, calls, jobs


def test_rate_uses_caller_session_and_rejects_anonymous(api):
    client, calls, _ = api
    body = {"conversation": "CPC-1", "message_index": 2, "helpful": True}

    assert client.post("/copilot/answers/rate", json=body, headers=ORIGIN).status_code == 401
    assert calls == []

    client.cookies.set("sid", "sid-student")
    response = client.post("/copilot/answers/rate", json=body, headers=ORIGIN)
    assert response.status_code == 200
    assert calls == [("rate", "sid-student", "CPC-1", 2, True)]

    assert client.post("/copilot/answers/rate", json=body, headers={"Origin": "http://evil.test"}).status_code == 403


def test_escalate_uses_session_and_stored_conversation(api):
    client, calls, _ = api
    body = {"course": "PY-101", "question": "Học phí?", "lesson": "LS-1", "session_id": "cht_1"}
    assert client.post("/copilot/escalate", json=body, headers=ORIGIN).status_code == 401

    tutor.set_conversation("hv@test.com", "cht_1", "CPC-3")
    client.cookies.set("sid", "sid-student")
    response = client.post("/copilot/escalate", json=body, headers=ORIGIN)
    assert response.status_code == 200
    tool, sid, arguments, kwargs = calls[0]
    assert (tool, sid) == ("escalate_to_teacher", "sid-student")
    assert arguments == {"course": "PY-101", "question": "Học phí?", "lesson": "LS-1"}
    assert kwargs["conversation"] == "CPC-3"

    # Conversation của người khác không bị lấy nhầm.
    client.cookies.set("sid", "sid-teacher")
    client.post("/copilot/escalate", json=body, headers=ORIGIN)
    assert calls[-1][3]["conversation"] is None


def test_weekly_job_auth(api):
    client, _, jobs = api
    body = {"course": "PY-101"}

    assert client.post("/copilot/jobs/weekly", json=body).status_code == 401
    assert client.post("/copilot/jobs/weekly", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401

    response = client.post("/copilot/jobs/weekly", json=body, headers={"Authorization": "Bearer job-secret"})
    assert response.status_code == 200
    assert response.json()["actor"] == "service"
    assert jobs[-1] == {"sid": "", "course": "PY-101", "week_start": None}  # tài khoản AI Engine, không session

    client.cookies.set("sid", "sid-student")
    assert client.post("/copilot/jobs/weekly", json=body, headers=ORIGIN).status_code == 403

    client.cookies.set("sid", "sid-teacher")
    response = client.post("/copilot/jobs/weekly", json={**body, "week_start": "2026-09-21"}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert jobs[-1] == {"sid": "sid-teacher", "course": "PY-101", "week_start": "2026-09-21"}


def test_weekly_job_key_disabled_when_unset(api, monkeypatch):
    client, _, jobs = api
    monkeypatch.setattr(server.settings, "copilot_job_key", "")
    response = client.post("/copilot/jobs/weekly", json={"course": "PY-101"}, headers={"Authorization": "Bearer "})
    assert response.status_code == 401
    assert jobs == []


class TeacherFrappe:
    base_url = "http://lms.test"
    site_host = "lms.localhost"
    session_id = "sid-teacher"
    api_key = ""
    api_secret = ""
    is_configured = True

    def __init__(self):
        self.insight_reads = []

    def get_copilot_tools(self):
        return [{"name": "gather_weekly_signals", "kind": "read", "writes": False, "description": "signals",
                 "parameters": {"type": "object", "properties": {"course": {"type": "string"}}}}]

    def get_copilot_weekly_insight(self, course, week_start=None):
        self.insight_reads.append((course, week_start))
        return {"name": "CWI-1", "week_start": weekly_insight.week_monday().isoformat(), "groups": [
            {"title": "Nhầm range", "learners": 2, "evidence": [{"id": "Q1"}, {"id": "Q2"}]},
        ]}


class WeeklyAnswerLLM:
    model = "fake-model"

    def chat(self, messages, tools=None):
        result = json.loads(next(m for m in messages if m.get("role") == "tool")["content"])
        group = result["insight"]["groups"][0]
        return {"choices": [{"message": {
            "content": f"Lớp vướng: {group['title']} ({group['learners']} học viên). Xem {result['link']}",
        }}]}


def test_teacher_weekly_question_reads_insight_through_gateway_tool(tmp_path, monkeypatch):
    copilot_bridge.clear_cache()
    monkeypatch.setattr(learner, "DB", tmp_path / "learner.db")
    monkeypatch.setattr(approval, "DB", tmp_path / "approval.db")
    frappe = TeacherFrappe()
    llm = WeeklyAnswerLLM()
    registry = build_registry(frappe, llm)
    assert registry.get(copilot_bridge.WEEKLY_INSIGHT_TOOL) is not None

    result = agent_loop.run_agent(
        llm, registry, "teacher", "Tuần này lớp vướng ở đâu?",
        {"user": "gv@test.com", "route": {"kind": "course", "course": "PY-101"}},
    )
    assert result["tool_calls"][0]["tool"] == "copilot_get_weekly_insight"
    assert frappe.insight_reads == [("PY-101", None)]
    assert "/lms/copilot/insight/PY-101" in result["answer"]
    assert "Nhầm range (2 học viên)" in result["answer"]
