"""F4: báo cáo điểm vướng tuần của một khóa học.

gather_weekly_signals (lms_copilot) -> LLM gom tín hiệu thành chủ đề -> Gateway kiểm tra mọi con số
và mọi bằng chứng có trong dữ liệu đầu vào -> save_weekly_insight -> tối đa 2 đề xuất mỗi nhóm
(propose_lesson_change / propose_lesson_quiz / propose_learner_reminder, chờ giáo viên duyệt).

LLM không bao giờ đếm: số học viên và số bằng chứng của mỗi nhóm do Gateway tính từ các bằng
chứng mà nhóm tham chiếu. Nhóm có con số hoặc bằng chứng không nằm trong đầu vào bị loại.
"""
from __future__ import annotations

from ..config import settings

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_PROPOSALS_PER_GROUP = 2
MAX_GROUPS = 12
PROPOSAL_TOOLS = {"propose_lesson_change", "propose_lesson_quiz", "propose_learner_reminder"}
_PROPOSAL_FIELDS = {
    "propose_lesson_change": {"lesson", "markdown", "mode", "after_block", "block_id", "reason", "confidence"},
    "propose_lesson_quiz": {"lesson", "quiz", "confidence"},
    "propose_learner_reminder": {"learners", "message", "reason", "confidence"},
}
_CONFIDENCE = {"High", "Medium", "Low"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
INACTIVE_REASON = "Không có hoạt động trong 9 ngày gần nhất"


def insight_link(course: str) -> str:
    return f"{settings.copilot_pages}/insight/{course}"


def week_monday(value: str | date | None = None, timezone: str = "Asia/Ho_Chi_Minh") -> date:
    if isinstance(value, date):
        day = value
    elif value:
        day = date.fromisoformat(str(value)[:10])
    else:
        day = datetime.now(ZoneInfo(timezone)).date()
    return day - timedelta(days=day.weekday())


# ------------------------------------------------------------------ evidence


def build_evidence(signals: dict) -> dict[str, dict]:
    """Đánh id cho từng tín hiệu đầu vào; LLM chỉ được tham chiếu các id này."""
    evidence: dict[str, dict] = {}
    learner_of_conversation: dict[str, str] = {}
    for index, item in enumerate(signals.get("questions") or [], 1):
        if not isinstance(item, dict):
            continue
        learner = str(item.get("learner") or "")
        conversation = str(item.get("conversation") or "")
        if conversation and learner:
            learner_of_conversation[conversation] = learner
        evidence[f"Q{index}"] = {
            "id": f"Q{index}", "kind": "question", "learner": learner,
            "lesson": str(item.get("lesson") or ""), "conversation": conversation,
            "label": "Câu hỏi: " + str(item.get("text") or "")[:200],
        }
    for index, item in enumerate(signals.get("not_helpful_answers") or [], 1):
        if not isinstance(item, dict):
            continue
        conversation = str(item.get("conversation") or "")
        evidence[f"N{index}"] = {
            "id": f"N{index}", "kind": "not_helpful", "learner": learner_of_conversation.get(conversation, ""),
            "conversation": conversation, "message_index": item.get("message_index"),
            "label": "Câu trả lời bị chấm chưa hữu ích: " + str(item.get("answer") or "")[:200],
        }
    counter = 0
    for criterion in signals.get("weak_criteria") or []:
        if not isinstance(criterion, dict):
            continue
        for item in criterion.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            counter += 1
            evidence[f"D{counter}"] = {
                "id": f"D{counter}", "kind": "rubric", "learner": str(item.get("learner") or ""),
                "draft": str(item.get("draft") or ""), "assignment": str(criterion.get("assignment") or ""),
                "criterion": str(criterion.get("criterion") or ""),
                "label": f"Rubric “{criterion.get('criterion')}”: " + str(item.get("reason") or "")[:200],
            }
    for index, learner in enumerate(signals.get("possibly_inactive") or [], 1):
        evidence[f"I{index}"] = {
            "id": f"I{index}", "kind": "inactive", "learner": str(learner or ""), "label": INACTIVE_REASON,
        }
    return evidence


def _llm_input(signals: dict, evidence: dict[str, dict]) -> dict:
    return {
        "course": signals.get("course"),
        "week_start": signals.get("week_start"),
        "evidence": [
            {key: value for key, value in item.items() if key in {"id", "kind", "learner", "lesson", "assignment", "criterion", "label"} and value}
            for item in evidence.values()
        ],
    }


SYSTEM_PROMPT = (
    "Bạn phân tích điểm vướng trong tuần của một lớp học. Đầu vào là danh sách bằng chứng có id "
    "(Q = câu hỏi, N = câu trả lời bị chấm chưa hữu ích, D = tiêu chí rubric điểm thấp, I = học viên không hoạt động). "
    "Gom bằng chứng thành tối đa 6 chủ đề vướng chung. Chỉ trả về JSON đúng dạng: "
    '{"groups":[{"title":"...","summary":"...","suggestion":"...","lesson":"<lesson có trong bằng chứng hoặc rỗng>",'
    '"evidence":["Q1","D2"],"proposals":[{"tool":"propose_lesson_change","lesson":"...","markdown":"...","reason":"..."},'
    '{"tool":"propose_lesson_quiz","lesson":"...","quiz":{"title":"...","questions":[...]}},'
    '{"tool":"propose_learner_reminder","learners":["L-..."],"message":"...","reason":"..."}]}]}. '
    "Quy tắc: không tự đếm và không viết bất kỳ con số nào trong title/summary/suggestion (Gateway tự tính số học viên "
    "và số bằng chứng); mỗi nhóm có ít nhất 1 id bằng chứng lấy nguyên văn từ đầu vào; tối đa 2 đề xuất mỗi nhóm; "
    "chỉ dùng lesson và mã học viên có trong đầu vào. Viết tiếng Việt, ngắn gọn."
)


def _parse_json(content: str) -> dict:
    text = str(content or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM không trả về JSON")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM không trả về object JSON")
    return data


# ------------------------------------------------------------------ validation


def _numbers(text: str) -> set[str]:
    return {value.replace(",", ".") for value in _NUMBER.findall(str(text or ""))}


def validate_groups(raw_groups, evidence: dict[str, dict], signals: dict) -> tuple[list[dict], list[dict]]:
    """Giữ nhóm có bằng chứng và con số đều nằm trong đầu vào; trả (groups, rejected)."""
    lessons = {item["lesson"] for item in evidence.values() if item.get("lesson")}
    learners_in_input = {item["learner"] for item in evidence.values() if item.get("learner")}
    stats_numbers = {
        str(value) for value in (signals.get("stats") or {}).values() if isinstance(value, (int, float))
    }
    groups: list[dict] = []
    rejected: list[dict] = []
    for raw in raw_groups if isinstance(raw_groups, list) else []:
        if len(groups) >= MAX_GROUPS:
            break
        if not isinstance(raw, dict):
            rejected.append({"title": "", "reason": "nhóm không phải object"})
            continue
        title = str(raw.get("title") or "").strip()[:200]
        summary = str(raw.get("summary") or "").strip()[:2000]
        suggestion = str(raw.get("suggestion") or "").strip()[:2000]
        ids = [str(item).strip() for item in raw.get("evidence") or [] if str(item).strip()]
        unknown = [item for item in ids if item not in evidence]
        if not title or not summary:
            rejected.append({"title": title, "reason": "thiếu title hoặc summary"})
            continue
        if not ids or unknown:
            rejected.append({"title": title, "reason": f"bằng chứng không có trong đầu vào: {unknown or 'trống'}"})
            continue
        ids = list(dict.fromkeys(ids))[:50]
        cited = [evidence[item] for item in ids]
        learners = len({item["learner"] for item in cited if item.get("learner")})
        count = len(cited)
        claimed = {key: raw.get(key) for key in ("learners", "count") if raw.get(key) not in (None, "")}
        if any(str(value) != str({"learners": learners, "count": count}[key]) for key, value in claimed.items()):
            rejected.append({"title": title, "reason": f"con số do LLM tự đếm không khớp đầu vào: {claimed}"})
            continue
        allowed_numbers = {str(learners), str(count)} | stats_numbers
        for item in cited:
            allowed_numbers |= _numbers(" ".join(str(value) for value in item.values()))
        stray = sorted(_numbers(" ".join((title, summary, suggestion))) - allowed_numbers)
        if stray:
            rejected.append({"title": title, "reason": f"con số không có trong đầu vào: {stray}"})
            continue
        lesson = str(raw.get("lesson") or "").strip()
        if lesson not in lessons:
            lesson = ""
        proposals = []
        for item in (raw.get("proposals") or [])[:MAX_PROPOSALS_PER_GROUP] if isinstance(raw.get("proposals"), list) else []:
            proposal = _proposal_args(item, lessons, learners_in_input, lesson)
            if proposal:
                proposals.append(proposal)
        groups.append({
            "title": title,
            "summary": summary,
            "suggestion": suggestion,
            "lesson": lesson or None,
            "learners": learners,
            "count": count,
            "evidence": [
                {key: value for key, value in item.items() if value not in ("", None)} for item in cited
            ],
            "_proposals": proposals,
        })
    return groups, rejected


def _proposal_args(item, lessons: set[str], learners: set[str], group_lesson: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    tool = str(item.get("tool") or item.get("type") or "")
    if not tool.startswith("propose_"):
        tool = "propose_" + tool
    if tool not in PROPOSAL_TOOLS:
        return None
    args = {key: item[key] for key in _PROPOSAL_FIELDS[tool] if key in item and item[key] not in (None, "")}
    if args.get("confidence") not in _CONFIDENCE:
        args.pop("confidence", None)
    if tool in {"propose_lesson_change", "propose_lesson_quiz"}:
        args["lesson"] = str(args.get("lesson") or group_lesson or "")
        if args["lesson"] not in lessons:
            return None
        if tool == "propose_lesson_change" and not str(args.get("markdown") or "").strip():
            return None
        if tool == "propose_lesson_quiz" and not isinstance(args.get("quiz"), dict):
            return None
    else:
        chosen = [str(value) for value in args.get("learners") or [] if str(value) in learners]
        if not chosen or not str(args.get("message") or "").strip():
            return None
        args["learners"] = list(dict.fromkeys(chosen))
    return {"tool": tool, "arguments": args}


# ------------------------------------------------------------------ job


def _usage(response: dict) -> tuple[int | None, int | None]:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def run_weekly_job(frappe, llm, course: str, week_start: str | None = None, model: str = "") -> dict:
    """Chạy trọn job cho một khóa. ``frappe`` là session giáo viên hoặc tài khoản AI Engine."""
    course = str(course or "").strip()
    if not course:
        raise ValueError("thiếu course")
    model = model or str(getattr(llm, "model", "") or "")
    arguments = {"course": course}
    if week_start:
        arguments["week_start"] = str(week_start)[:10]
    signals = frappe.call_copilot_tool("gather_weekly_signals", arguments, model=model)
    if not isinstance(signals, dict):
        raise RuntimeError("gather_weekly_signals không trả về dữ liệu")
    week = str(signals.get("week_start") or arguments.get("week_start") or "")
    base = {"course": course, "week_start": week, "link": insight_link(course)}
    evidence = build_evidence(signals)
    if not any(item["kind"] != "inactive" for item in evidence.values()):
        return {**base, "status": "no_signals", "groups": 0, "proposals": [], "rejected": []}

    response = llm.chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(_llm_input(signals, evidence), ensure_ascii=False)},
    ])
    tokens_in, tokens_out = _usage(response)
    try:
        content = response["choices"][0]["message"].get("content") or ""
        raw_groups = _parse_json(content).get("groups")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return {**base, "status": "llm_error", "error": str(exc), "groups": 0, "proposals": [], "rejected": []}
    groups, rejected = validate_groups(raw_groups, evidence, signals)
    if not groups:
        return {**base, "status": "no_valid_groups", "groups": 0, "proposals": [], "rejected": rejected}

    at_risk = [
        {"learner": item["learner"], "reason": INACTIVE_REASON}
        for item in evidence.values() if item["kind"] == "inactive" and item.get("learner")
    ][:100]
    stats = dict(signals.get("stats") or {})

    def save(stored_groups: list[dict]):
        payload = {
            "course": course,
            "groups": stored_groups,
            "at_risk": at_risk,
            "stats": stats,
            "model": model,
        }
        if week:
            payload["week_start"] = week
        return frappe.call_copilot_tool(
            "save_weekly_insight", payload, model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        )

    public = [{key: value for key, value in group.items() if key != "_proposals"} for group in groups]
    saved = save([{**group, "proposals": []} for group in public])
    insight = str((saved or {}).get("name") or "") if isinstance(saved, dict) else ""

    created: list[str] = []
    failures: list[dict] = []
    for group, stored in zip(groups, public):
        names = []
        for proposal in group["_proposals"][:MAX_PROPOSALS_PER_GROUP]:
            args = dict(proposal["arguments"])
            if proposal["tool"] == "propose_learner_reminder":
                args["course"] = course
            if insight:
                args["insight"] = insight
            try:
                result = frappe.call_copilot_tool(proposal["tool"], args, model=model)
            except RuntimeError as exc:
                failures.append({"group": group["title"], "tool": proposal["tool"], "error": str(exc)})
                continue
            name = str((result or {}).get("proposal") or (result or {}).get("name") or "") if isinstance(result, dict) else ""
            if name:
                names.append(name)
        stored["proposals"] = names
        created.extend(names)
    if created:
        save(public)
    return {
        **base,
        "status": "saved",
        "insight": insight,
        "groups": len(public),
        "proposals": created,
        "proposal_errors": failures,
        "rejected": rejected,
    }


def summarize_insight(insight: dict | None) -> dict | None:
    if not isinstance(insight, dict):
        return None
    return {
        "name": insight.get("name"),
        "week_start": str(insight.get("week_start") or ""),
        "week_end": str(insight.get("week_end") or ""),
        "stats": insight.get("stats") or {},
        "groups": [
            {
                "title": group.get("title"),
                "summary": group.get("summary"),
                "suggestion": group.get("suggestion"),
                "lesson": group.get("lesson"),
                "learners": group.get("learners"),
                "evidence": len(group.get("evidence") or []),
                "proposals": group.get("proposal_status") or group.get("proposals") or [],
            }
            for group in insight.get("groups") or [] if isinstance(group, dict)
        ],
        "at_risk": len(insight.get("at_risk") or []),
    }


def latest_or_run(frappe, llm, course: str, timezone: str = "Asia/Ho_Chi_Minh") -> dict:
    """Báo cáo tuần này; nếu chưa có thì chạy job rồi đọc lại. Dùng cho câu hỏi của giáo viên."""
    this_week = week_monday(None, timezone)
    insight = frappe.get_copilot_weekly_insight(course)
    generated = None
    if not insight or str(insight.get("week_start") or "")[:10] < this_week.isoformat():
        generated = run_weekly_job(frappe, llm, course, this_week.isoformat())
        insight = frappe.get_copilot_weekly_insight(course, this_week.isoformat()) or insight
    return {
        "course": course,
        "link": insight_link(course),
        "generated": bool(generated and generated.get("status") == "saved"),
        "job": generated,
        "insight": summarize_insight(insight),
        "source": f"lms_copilot Copilot Weekly Insight: {course}",
    }
