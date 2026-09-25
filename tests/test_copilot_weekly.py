import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.runtime import weekly_insight
from gateway.runtime.scheduler import ProactiveWorker

SIGNALS = {
    "course": "PY-101",
    "week_start": "2026-09-21",
    "week_end": "2026-09-27",
    "stats": {"learners": 30, "questions": 3, "submissions": 4, "approved_feedback": 2},
    "questions": [
        {"learner": "L-AAA", "lesson": "LS-1", "text": "Vì sao vòng for chạy thiếu?", "conversation": "CPC-1"},
        {"learner": "L-BBB", "lesson": "LS-1", "text": "range dừng ở đâu?", "conversation": "CPC-2"},
        {"learner": "L-AAA", "lesson": "LS-2", "text": "list comprehension là gì?", "conversation": "CPC-1"},
    ],
    "not_helpful_answers": [{"conversation": "CPC-2", "message_index": 2, "answer": "range(n) dừng trước n"}],
    "weak_criteria": [{
        "assignment": "ASG-1", "criterion": "Xử lý biên", "count": 1,
        "evidence": [{"draft": "CFD-1", "learner": "L-CCC", "reason": "Không kiểm tra danh sách rỗng"}],
    }],
    "possibly_inactive": ["L-DDD"],
}


class FakeFrappe:
    def __init__(self, signals=None, insights=None):
        self.signals = SIGNALS if signals is None else signals
        self.insights = insights if insights is not None else {}
        self.calls = []
        self.proposal_no = 0

    def call_copilot_tool(self, tool, arguments=None, **kwargs):
        self.calls.append((tool, json.loads(json.dumps(arguments or {})), kwargs))
        if tool == "gather_weekly_signals":
            return self.signals
        if tool == "save_weekly_insight":
            self.insights[arguments.get("week_start") or "current"] = {
                "name": "CWI-1", "week_start": arguments.get("week_start"), "groups": arguments["groups"],
            }
            return {"name": "CWI-1", "week_start": arguments.get("week_start"), "groups": len(arguments["groups"])}
        if tool.startswith("propose_"):
            self.proposal_no += 1
            return {"proposal": f"CPP-{self.proposal_no}", "status": "Pending"}
        raise AssertionError(tool)

    def get_copilot_weekly_insight(self, course, week_start=None):
        if week_start:
            return self.insights.get(week_start)
        return max(self.insights.values(), key=lambda item: str(item.get("week_start")), default=None)

    def called(self, tool):
        return [call for call in self.calls if call[0] == tool]


class FakeLLM:
    model = "fake-model"

    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    def chat(self, messages, tools=None):
        self.messages = messages
        content = self.payload if isinstance(self.payload, str) else "```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 50, "completion_tokens": 30}}


GOOD_GROUP = {
    "title": "Chưa hiểu điểm dừng của range",
    "summary": "Học viên nhầm cận trên của range trong vòng for.",
    "suggestion": "Thêm ví dụ minh họa range với cận trên.",
    "lesson": "LS-1",
    "evidence": ["Q1", "Q2", "N1"],
    "proposals": [
        {"tool": "propose_lesson_change", "lesson": "LS-1", "markdown": "> range(n) dừng trước n", "reason": "nhầm cận"},
        {"tool": "propose_learner_reminder", "learners": ["L-AAA", "L-ZZZ"], "message": "Xem lại bài vòng lặp"},
        {"tool": "propose_lesson_quiz", "lesson": "LS-1", "quiz": {"title": "range"}},
    ],
}


def test_job_saves_groups_with_counts_taken_from_signals_and_caps_proposals():
    frappe = FakeFrappe()
    llm = FakeLLM({"groups": [GOOD_GROUP]})

    result = weekly_insight.run_weekly_job(frappe, llm, "PY-101", "2026-09-23", model="fake-model")

    assert result["status"] == "saved"
    assert result["link"] == "/copilot/insight/PY-101"
    assert frappe.called("gather_weekly_signals")[0][1] == {"course": "PY-101", "week_start": "2026-09-23"}
    saves = frappe.called("save_weekly_insight")
    assert len(saves) == 2
    group = saves[-1][1]["groups"][0]
    # Q1 + Q2 + N1: học viên L-AAA, L-BBB -> 2; bằng chứng 3. LLM không đưa con số nào.
    assert group["learners"] == 2 and group["count"] == 3
    assert [item["id"] for item in group["evidence"]] == ["Q1", "Q2", "N1"]
    assert group["proposals"] == ["CPP-1", "CPP-2"]
    assert saves[-1][1]["at_risk"] == [{"learner": "L-DDD", "reason": weekly_insight.INACTIVE_REASON}]
    assert saves[-1][1]["stats"] == SIGNALS["stats"]
    assert saves[0][2]["tokens_in"] == 50

    proposals = [call for call in frappe.calls if call[0].startswith("propose_")]
    assert [call[0] for call in proposals] == ["propose_lesson_change", "propose_learner_reminder"]
    assert all(call[1]["insight"] == "CWI-1" for call in proposals)
    reminder = proposals[1][1]
    assert reminder["course"] == "PY-101"
    assert reminder["learners"] == ["L-AAA"]  # L-ZZZ không có trong đầu vào


def test_job_rejects_groups_with_evidence_or_numbers_not_in_input():
    frappe = FakeFrappe()
    llm = FakeLLM({"groups": [
        {**GOOD_GROUP, "title": "Bằng chứng bịa", "evidence": ["Q1", "Q99"], "proposals": []},
        {**GOOD_GROUP, "title": "Tự đếm sai", "learners": 5, "proposals": []},
        {**GOOD_GROUP, "title": "7 học viên vướng range", "proposals": []},
        {**GOOD_GROUP, "title": "Đếm đúng", "learners": 2, "count": 3, "proposals": []},
    ]})

    result = weekly_insight.run_weekly_job(frappe, llm, "PY-101")

    assert result["status"] == "saved"
    assert [item["title"] for item in result["rejected"]] == [
        "Bằng chứng bịa", "Tự đếm sai", "7 học viên vướng range",
    ]
    saved = frappe.called("save_weekly_insight")
    assert len(saved) == 1  # không có đề xuất nào nên không lưu lần hai
    assert [group["title"] for group in saved[0][1]["groups"]] == ["Đếm đúng"]
    assert "week_start" not in frappe.called("gather_weekly_signals")[0][1]


def test_job_does_not_save_when_nothing_valid():
    frappe = FakeFrappe()
    result = weekly_insight.run_weekly_job(frappe, FakeLLM({"groups": [{**GOOD_GROUP, "evidence": ["X1"]}]}), "PY-101")
    assert result["status"] == "no_valid_groups"
    assert frappe.called("save_weekly_insight") == []

    result = weekly_insight.run_weekly_job(frappe, FakeLLM("không phải JSON"), "PY-101")
    assert result["status"] == "llm_error"


def test_job_skips_llm_without_signals():
    empty = {**SIGNALS, "questions": [], "not_helpful_answers": [], "weak_criteria": []}
    frappe = FakeFrappe(signals=empty)
    llm = FakeLLM({"groups": [GOOD_GROUP]})
    result = weekly_insight.run_weekly_job(frappe, llm, "PY-101")
    assert result["status"] == "no_signals"
    assert llm.messages is None


def test_llm_sees_only_evidence_ids_not_raw_emails():
    llm = FakeLLM({"groups": [GOOD_GROUP]})
    weekly_insight.run_weekly_job(FakeFrappe(), llm, "PY-101")
    payload = json.loads(llm.messages[1]["content"])
    assert {item["id"] for item in payload["evidence"]} == {"Q1", "Q2", "Q3", "N1", "D1", "I1"}
    assert "@" not in llm.messages[1]["content"]


def test_latest_or_run_reads_existing_insight_and_runs_job_when_missing():
    this_week = weekly_insight.week_monday().isoformat()
    existing = FakeFrappe(insights={this_week: {"name": "CWI-9", "week_start": this_week, "groups": [
        {"title": "Có sẵn", "learners": 3, "evidence": [{"id": "Q1"}], "proposal_status": {"CPP-1": "Pending"}},
    ]}})
    out = weekly_insight.latest_or_run(existing, FakeLLM({"groups": []}), "PY-101")
    assert out["generated"] is False
    assert out["insight"]["groups"][0]["title"] == "Có sẵn"
    assert out["link"] == "/copilot/insight/PY-101"
    assert existing.called("gather_weekly_signals") == []

    last_week = (weekly_insight.week_monday() - timedelta(days=7)).isoformat()
    stale = FakeFrappe(insights={last_week: {"name": "CWI-8", "week_start": last_week, "groups": []}})
    out = weekly_insight.latest_or_run(stale, FakeLLM({"groups": [GOOD_GROUP]}), "PY-101")
    assert out["generated"] is True
    assert stale.called("gather_weekly_signals")[0][1]["week_start"] == this_week


def test_scheduler_runs_weekly_job_on_monday_at_the_configured_hour():
    runs = []
    worker = ProactiveWorker(object(), weekly_job=lambda now: runs.append(now) or ["ok"], weekly_hour=7)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    monday = datetime(2026, 9, 28, 7, 5, tzinfo=tz)
    assert worker.weekly_due(monday)
    assert not worker.weekly_due(monday.replace(hour=8))
    assert not worker.weekly_due(monday + timedelta(days=1))
    assert worker.run_weekly(monday)["results"] == ["ok"]
    assert not worker.weekly_due(monday.replace(minute=30))
    assert ProactiveWorker(object()).weekly_due(monday) is False
