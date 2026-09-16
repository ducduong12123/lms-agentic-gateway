"""FastAPI entrypoint for the external LMS agent engine."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gateway.api.webhook import handle_event, verify_signature
from gateway.config import settings
from gateway.connector.frappe_client import FrappeClient
from gateway.runtime import plan_apply, write_plans
from gateway.runtime.agent_loop import learner_context_for_prompt, run_agent, run_agent_stream
from gateway.runtime import actions, runs
from gateway.runtime.approval import mark_executed, pending_for
from gateway.runtime.approval import approve as claim_approval
from gateway.runtime.events import EventProcessor
from gateway.runtime import features, learner
from gateway.runtime.identity import Identity, IdentityResolver
from gateway.runtime.long_memory import search_memories
from gateway.runtime.model_client import OpenAICompatClient
from gateway.runtime.route_adapter import load_lesson_context, resolve_lms_context
from gateway.runtime.scheduler import ProactiveWorker
from gateway.tools.catalog import build_registry
from gateway.tools import plan_executors

ROOT = Path(__file__).resolve().parents[3]
WIDGET_DIR = ROOT / "widget"
WEB_DIR = ROOT / "web"

app = FastAPI(title="LMS Agentic Gateway", docs_url=None, redoc_url=None)


def _base_frappe() -> FrappeClient:
    return FrappeClient(
        settings.frappe_url,
        settings.frappe_api_key,
        settings.frappe_api_secret,
        site_host=settings.frappe_site_host,
    )


identity_resolver = IdentityResolver(_base_frappe(), settings.identity_cache_ttl)
event_processor = EventProcessor(_base_frappe(), settings.poll_interval_seconds)
proactive_worker = ProactiveWorker(
    _base_frappe(), settings.scheduler_timezone, settings.daily_plan_hour,
    settings.transcript_retention_days,
)
_rate_lock = threading.Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)


@app.on_event("startup")
def start_event_worker() -> None:
    event_processor.start()
    proactive_worker.start()


@app.on_event("shutdown")
def stop_event_worker() -> None:
    event_processor.stop()
    proactive_worker.stop()


class ChatPayload(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: str = Field(default="", max_length=128)
    page: str = Field(default="", max_length=1000)
    mode: str = Field(default="chat", pattern="^(chat|feynman|viva|check)$")
    effort: str = Field(default="auto", pattern="^(auto|minimal|low|medium|high|xhigh)$")
    approval_mode: str = Field(default="ask", pattern="^(ask|auto|full_access)$")


class ChatSessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)

class ConceptExtractPayload(BaseModel):
    lesson: str = Field(min_length=1, max_length=256)


class LearningSessionPayload(BaseModel):
    mode: str = Field(pattern="^(feynman|viva|check)$")
    concept_id: str = Field(min_length=1, max_length=128)
    transcript: str = Field(min_length=20, max_length=12000)


class FeedbackPayload(BaseModel):
    concept_id: str = Field(min_length=1, max_length=128)
    note: str = Field(default="not-accurate", max_length=500)


class PreferencePayload(BaseModel):
    daily_plan_opt_in: bool
    quiet_hours: str = Field(default="", max_length=128)


class ApprovalItemDecision(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    status: str = Field(default="approved", pattern="^(approved|rejected|pending)$")
    payload: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    items: list[ApprovalItemDecision] | None = None
    typed_confirm: str = Field(default="", max_length=256)


class TeacherActionPayload(BaseModel):
    course: str = Field(default="", max_length=256)
    target: str = Field(min_length=3, max_length=256)
    subject: str = Field(min_length=1, max_length=180)
    message: str = Field(min_length=1, max_length=2000)


def trusted_identity(sid: str | None = Cookie(default=None)) -> Identity:
    try:
        return identity_resolver.resolve(sid or "")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Frappe session is required") from exc


def _deps(identity: Identity):
    frappe = _base_frappe().with_session(identity.sid)
    llm = OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    return llm, build_registry(frappe, llm)


def _require_roles(identity: Identity, *roles: str) -> None:
    if identity.role not in roles:
        raise HTTPException(status_code=403, detail="role is not allowed")


def _require_course_access(identity: Identity, course: str) -> None:
    _require_roles(identity, "teacher", "admin")
    if identity.role == "admin" or not course:
        return
    try:
        rows = _base_frappe().with_session(identity.sid).list_documents(
            "LMS Course", filters={"name": course}, fields=["name"], limit=1
        ).get("data", [])
    except Exception as exc:
        raise HTTPException(status_code=403, detail="course access denied") from exc
    if not rows:
        raise HTTPException(status_code=403, detail="course access denied")


def _check_rate(identity: Identity, limit: int = 30, window: int = 60) -> None:
    now = time.monotonic()
    key = hashlib.sha256(identity.user.encode()).hexdigest()
    with _rate_lock:
        events = _rate_events[key]
        while events and events[0] < now - window:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        events.append(now)


def _memory_context(payload: ChatPayload, identity: Identity) -> dict[str, Any]:
    conversation_id = payload.conversation_id.strip()
    route = resolve_lms_context(_base_frappe().with_session(identity.sid), payload.page)
    lesson_context = load_lesson_context(
        _base_frappe().with_session(identity.sid), route, identity.role
    )
    tutor = learner_context_for_prompt(
        identity.user, str(route.get("course") or ""), str(route.get("lesson") or "")
    )
    instructions = {
        "feynman": (
            "Chế độ Feynman: đóng vai người mới học. Yêu cầu học viên giảng lại concept, "
            "hỏi vặn điểm mơ hồ; đánh giá đúng, đủ, ví dụ và giới hạn. Không giảng hộ ngay."
        ),
        "check": (
            "Chế độ kiểm tra hội thoại: hỏi từng câu tự luận, ưu tiên concept yếu nhất. "
            "Nếu mới đúng một phần thì hỏi tiếp; không tiết lộ đáp án trước."
        ),
    }
    context: dict[str, Any] = {
        "user": identity.user,
        "route": route,
        "current_lesson": lesson_context,
        "mode": payload.mode,
        "mode_instruction": instructions.get(payload.mode, ""),
        "approval_mode": payload.approval_mode,
        "long_term_memories": search_memories(identity.user, payload.message, top_k=4),
        **tutor,
    }
    if identity.user and identity.user != "Guest" and conversation_id:
        detail = features.get_chat_session(identity.user, conversation_id, limit=100)
        if detail and detail.get("messages"):
            messages = detail["messages"]
            if not features.get_conversation_state(identity.user, conversation_id):
                pending_user = ""
                for item in messages:
                    role = str(item.get("role") or "")
                    content = str(item.get("content") or "")
                    if role == "user":
                        pending_user = content
                    elif role == "assistant":
                        features.capture_conversation_artifacts(
                            identity.user, conversation_id, pending_user, content,
                        )
                        pending_user = ""
            context["recent_messages"] = [
                {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")[:12000]}
                for item in messages[-16:]
                if str(item.get("role") or "") in {"user", "assistant"}
                and str(item.get("content") or "").strip()
            ]
        workflow = features.workflow_context(identity.user, conversation_id)
        if workflow:
            context["workflow_state"] = workflow
    return context


def _sse(event: dict) -> bytes:
    event_name = str(event.get("t") or "message")
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


def _request_origin(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _require_same_origin(request: Request) -> None:
    allowed = {_request_origin(value) for value in settings.public_origins}
    origin = _request_origin(request.headers.get("origin"))
    if origin in allowed:
        return
    if not origin and _request_origin(request.headers.get("referer")) in allowed:
        return
    if origin:
        detail = f"origin not allowed: {origin}"
    else:
        detail = "same-origin request required: Origin/Referer missing or not allowed"
    raise HTTPException(status_code=403, detail=detail)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "model": settings.llm_model,
        "identity": "frappe-sid",
        "webhook_configured": bool(settings.frappe_webhook_secret),
        "store": event_processor.stats(),
        "scheduler": {"hour": settings.daily_plan_hour, "timezone": settings.scheduler_timezone},
    }


@app.get("/identity")
def identity(identity: Identity = Depends(trusted_identity)) -> dict:
    return {"user": identity.user, "role": identity.role, "roles": identity.roles}


@app.get("/widget.js")
def widget() -> FileResponse:
    return FileResponse(
        WIDGET_DIR / "agentic-copilot.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/widget/{name:path}")
def widget_compat(name: str):
    target = (WIDGET_DIR / name).resolve()
    if not target.is_relative_to(WIDGET_DIR.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="widget not found")
    return FileResponse(
        target,
        media_type="application/javascript" if target.suffix == ".js" else None,
        headers={"Cache-Control": "no-store, max-age=0"} if target.suffix == ".js" else None,
    )


@app.post("/chat")
def chat(payload: ChatPayload, identity: Identity = Depends(trusted_identity)) -> dict:
    _check_rate(identity)
    llm, registry = _deps(identity)
    context = _memory_context(payload, identity)
    session = features.ensure_chat_session(
        identity.user, payload.conversation_id, payload.message, payload.mode
    )
    context["session_id"] = session["id"]
    output = run_agent(llm, registry, identity.role, payload.message, context, effort=payload.effort)
    features.append_chat_turn(
        identity.user, session["id"], payload.message, output.get("answer", "")
    )
    output["session_id"] = session["id"]
    return output


@app.post("/chat/stream")
def chat_stream(payload: ChatPayload, identity: Identity = Depends(trusted_identity)) -> StreamingResponse:
    _check_rate(identity)
    llm, registry = _deps(identity)
    context = _memory_context(payload, identity)
    session = features.ensure_chat_session(
        identity.user, payload.conversation_id, payload.message, payload.mode
    )
    context["session_id"] = session["id"]

    def events():
        yield _sse({"t": "session", "session_id": session["id"]})
        for event in run_agent_stream(llm, registry, identity.role, payload.message, context, effort=payload.effort):
            if event.get("t") == "done":
                features.append_chat_turn(
                    identity.user, session["id"], payload.message, event.get("answer", "")
                )
                event["session_id"] = session["id"]
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/chat/sessions")
def chat_sessions(limit: int = 30, identity: Identity = Depends(trusted_identity)) -> dict:
    return {"sessions": features.list_chat_sessions(identity.user, limit)}


@app.get("/chat/sessions/{session_id}")
def chat_session_detail(session_id: str, identity: Identity = Depends(trusted_identity)) -> dict:
    detail = features.get_chat_session(identity.user, session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="session not found")
    return detail


@app.patch("/chat/sessions/{session_id}")
def chat_session_rename(
    session_id: str, payload: ChatSessionRename, request: Request,
    identity: Identity = Depends(trusted_identity),
) -> dict:
    _require_same_origin(request)
    if not features.rename_chat_session(identity.user, session_id, payload.title):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session_id": session_id, "title": payload.title.strip()[:60]}


@app.delete("/chat/sessions/{session_id}")
def chat_session_delete(
    session_id: str, request: Request, identity: Identity = Depends(trusted_identity)
) -> dict:
    _require_same_origin(request)
    if not features.delete_chat_session(identity.user, session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session_id": session_id}


@app.get("/me/mastery")
def my_mastery(course: str = "", page: str = "", identity: Identity = Depends(trusted_identity)) -> dict:
    route = resolve_lms_context(_base_frappe().with_session(identity.sid), page)
    selected_course = course or str(route.get("course") or "")
    gate = features.soft_gate(identity.user, str(route.get("lesson") or "")) if route.get("lesson") else None
    return {
        "course": selected_course, "route": route,
        "lesson_context_available": bool(route.get("lesson")),
        "concepts": features.mastery_with_evidence(identity.user, selected_course),
        "soft_gate": gate,
    }


@app.post("/me/feedback")
def mastery_feedback(payload: FeedbackPayload, request: Request,
                     identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    try:
        return features.record_concept_feedback(identity.user, payload.concept_id, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/concepts")
def concepts(course: str = "", status: str = "", identity: Identity = Depends(trusted_identity)) -> dict:
    _require_course_access(identity, course)
    return {"concepts": features.list_concepts(course, status)}


@app.post("/concepts/extract")
def extract_concepts(payload: ConceptExtractPayload, request: Request,
                     identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    _require_roles(identity, "teacher", "admin")
    session_frappe = _base_frappe().with_session(identity.sid)
    doc = session_frappe.get_document("Course Lesson", payload.lesson).get("data", {})
    _require_course_access(identity, str(doc.get("course") or ""))
    llm = OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    return {"concepts": features.extract_concepts(llm, session_frappe, payload.lesson)}


@app.post("/concepts/{concept_id}/approve")
def approve_concept(concept_id: str, request: Request, course: str = "",
                    identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    actual_course = features.concept_course(concept_id)
    if actual_course is None:
        raise HTTPException(status_code=404, detail="concept not found")
    _require_course_access(identity, actual_course)
    if not learner.approve_concept(concept_id):
        raise HTTPException(status_code=404, detail="concept not found")
    return {"ok": True, "concept_id": concept_id, "status": "approved"}


@app.post("/learning/session")
def finish_learning_session(payload: LearningSessionPayload, request: Request,
                            identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    _require_roles(identity, "student")
    if features.concept_status(payload.concept_id) != "approved":
        raise HTTPException(status_code=400, detail="approved concept is required")
    llm = OpenAICompatClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    return features.evaluate_session(
        llm, identity.user, payload.mode, payload.concept_id, payload.transcript
    )


@app.get("/preferences")
def preferences(identity: Identity = Depends(trusted_identity)) -> dict:
    return learner.get_member_pref(identity.user)


@app.post("/preferences")
def update_preferences(payload: PreferencePayload, request: Request,
                       identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    return learner.set_member_pref(
        identity.user, payload.daily_plan_opt_in, payload.quiet_hours
    )


@app.get("/plans/me")
def my_plans(identity: Identity = Depends(trusted_identity)) -> dict:
    return {"plans": features.plans_for(identity.user), "preferences": learner.get_member_pref(identity.user)}
@app.get("/queue")
def queue(identity: Identity = Depends(trusted_identity)) -> dict:
    return {
        "plans": features.plans_for(identity.user),
        "approvals": pending_for(identity.role, identity.user),
        "reviews": features.scheduled_reviews_for(identity.user),
    }


@app.get("/review-sets/{set_id}")
def review_set(set_id: str, identity: Identity = Depends(trusted_identity)) -> dict:
    result = features.get_review_set(set_id, identity.user)
    if not result:
        raise HTTPException(status_code=404, detail="review set not found")
    return result


@app.post("/review-sets/{set_id}/answer")
def answer_review_set(
    set_id: str,
    payload: ReviewAnswerPayload,
    request: Request,
    identity: Identity = Depends(trusted_identity),
) -> dict:
    _require_same_origin(request)
    try:
        return features.answer_review_item(set_id, payload.item_id, payload.answer, identity.user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/actions/recent")
def recent_actions(limit: int = 20, identity: Identity = Depends(trusted_identity)) -> dict:
    return {"actions": actions.recent_actions(identity.user, limit)}


@app.post("/actions/{action_id}/undo")
def undo_action(
    action_id: str,
    request: Request,
    identity: Identity = Depends(trusted_identity),
) -> dict:
    _require_same_origin(request)
    try:
        return actions.undo_action(
            action_id,
            _base_frappe().with_session(identity.sid),
            member=identity.user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@app.get("/teacher/risk")
def teacher_risk(course: str = "", identity: Identity = Depends(trusted_identity)) -> dict:
    _require_course_access(identity, course)
    return {"course": course, "students": features.detect_risk(course)}


@app.post("/teacher/actions")
def propose_action(payload: TeacherActionPayload, request: Request,
                   identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    _require_course_access(identity, payload.course)
    return features.propose_teacher_action(
        payload.course, payload.target, payload.subject, payload.message, identity.user
    )


@app.post("/teacher/actions/{action_id}/approve")
def approve_teacher_action(action_id: str, request: Request,
                           identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    course = features.action_course(action_id)
    if course is None:
        raise HTTPException(status_code=404, detail="action not found")
    _require_course_access(identity, course)
    return features.approve_teacher_action(action_id, identity.user, _base_frappe().with_session(identity.sid))

@app.post("/ops/recompute")
def recompute(request: Request, identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    _require_roles(identity, "admin")
    return {"mastery": learner.recompute_mastery()}


@app.delete("/me/data")
def erase_my_data(request: Request, identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    return features.erase_member(identity.user)


@app.post("/ops/run-daily")
def run_daily(request: Request, identity: Identity = Depends(trusted_identity)) -> dict:
    _require_same_origin(request)
    _require_roles(identity, "admin")
    return proactive_worker.run_daily()


@app.get("/plan")
def plan_page(identity: Identity = Depends(trusted_identity)) -> FileResponse:
    return FileResponse(WEB_DIR / "plan.html", media_type="text/html")


@app.get("/teacher")
def teacher_page(identity: Identity = Depends(trusted_identity)) -> FileResponse:
    _require_roles(identity, "teacher", "admin")
    return FileResponse(WEB_DIR / "teacher.html", media_type="text/html")


@app.post("/approve/{approval_id}")
def approve_action(
    approval_id: str,
    request: Request,
    identity: Identity = Depends(trusted_identity),
    decision: ApprovalDecision | None = None,
) -> dict:
    body_decision = decision
    if body_decision is None:
        try:
            payload = request.scope.get("fastapi_body") or {}
        except Exception:
            payload = {}
        if isinstance(payload, dict) and (payload.get("items") is not None or payload.get("typed_confirm")):
            try:
                body_decision = ApprovalDecision.model_validate(payload)
            except Exception:
                body_decision = None
    return _execute_approval(approval_id, body_decision, request, identity)


@app.patch("/approvals/{approval_id}")
def update_approval_items(
    approval_id: str,
    decision: ApprovalDecision,
    request: Request,
    identity: Identity = Depends(trusted_identity),
) -> dict:
    _require_same_origin(request)
    try:
        record = claim_approval(approval_id, identity.user, identity.role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not record or not record.get("plan_id"):
        raise HTTPException(status_code=404, detail="plan approval not found")
    plan = write_plans.get_plan(str(record.get("plan_id") or ""))
    if not plan or plan.get("status") != "pending":
        raise HTTPException(status_code=409, detail="plan is no longer editable")
    items = [item.model_dump() for item in (decision.items or [])]
    updated = plan
    for entry in items:
        item_id = str(entry.get("id") or "")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        status = str(entry.get("status") or "")
        if payload:
            updated = write_plans.patch_item(str(plan.get("id") or ""), item_id, payload) or updated
        if status in {"approved", "rejected", "pending"}:
            updated = write_plans.set_item_status(str(plan.get("id") or ""), item_id, status) or updated
    return {"ok": True, "plan": updated}


def _execute_approval(approval_id: str, decision: ApprovalDecision | None, request: Request, identity: Identity) -> dict:
    _require_same_origin(request)
    frappe = _base_frappe().with_session(identity.sid)
    _, registry = _deps(identity)
    try:
        record = claim_approval(approval_id, identity.user, identity.role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="approval not found or already used")

    tool = registry.get(record["tool"])
    if not tool or not tool.needs_approval or identity.role not in tool.approval_roles:
        raise HTTPException(status_code=403, detail="tool cannot be approved by this identity")
    plan = write_plans.get_plan(str(record.get("plan_id") or "")) if record.get("plan_id") else None
    payload_items = [item.model_dump() for item in (decision.items if decision and decision.items else [])] if decision else None
    typed_confirm = str(decision.typed_confirm if decision else "") or ""
    args = {
        key: value
        for key, value in dict(record["args"] or {}).items()
        if not str(key).startswith("_")
    }
    args["member"] = identity.user
    args["_role"] = identity.role
    try:
        if plan and plan.get("status") == "pending" and str(plan.get("tool") or "") == record["tool"]:
            result = _apply_write_plan(frappe, record["tool"], plan, payload_items, typed_confirm, identity)
        else:
            result = tool.func(args)
    except ValueError as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        result = {"error": str(exc)}
    if isinstance(result, dict) and result.get("kind") == "action":
        undo = result.get("undo") or {}
        if result.get("status") in {"done", "noop"}:
            actions.record_action(
                member=identity.user,
                tool=record["tool"],
                args=args,
                result=result,
                undo=undo if undo.get("available") else None,
                status=str(result.get("status") or "done"),
                action_id=str(result.get("action_id") or ""),
            )
    mark_executed(approval_id, result)
    resumed = runs.complete_approval(approval_id, result)
    draft_id = str(args.get("draft_id") or "")
    if draft_id:
        draft = features.get_lesson_draft(identity.user, draft_id)
        if draft:
            if "error" in result:
                features.mark_lesson_draft_status(identity.user, draft_id, "draft")
            features.update_conversation_state(
                identity.user, str(draft["session_id"]), {"pending_approval_id": ""},
            )
    return {
        "ok": "error" not in result,
        "tool": record["tool"],
        "result": result,
        "run_id": str((resumed or {}).get("id") or ""),
        "resume": bool(resumed) and "error" not in result,
        "resume_message": "Tiếp tục kế hoạch sau khi thao tác vừa được duyệt.",
    }


def _apply_write_plan(frappe, tool_name: str, plan: dict, payload_items, typed_confirm: str, identity: Identity) -> dict:
    operation = str((plan.get("args") or {}).get("operation") or "")
    reversibility = str(plan.get("reversibility") or "irreversible")
    if write_plans.requires_typed_confirm(tool_name, operation, reversibility):
        expected = str((plan.get("args") or {}).get("course") or (plan.get("args") or {}).get("chapter") or (plan.get("args") or {}).get("lesson") or (plan.get("args") or {}).get("quiz") or (plan.get("args") or {}).get("name") or "")
        if expected and typed_confirm.strip() != expected:
            write_plans.mark_plan_status(str(plan.get("id") or ""), "failed")
            return {"error": f"xác nhận '{expected}' chưa đúng; thao tác nguy hiểm đã bị chặn"}
    selected, merged = plan_apply.apply_plan_selection(plan, payload_items)
    for item_id in [str(entry.get("id") or "") for entry in selected]:
        write_plans.set_item_status(str(plan.get("id") or ""), item_id, "approved")
    for entry in plan.get("items") or []:
        if isinstance(entry, dict) and str(entry.get("id") or "") not in {str(item.get("id") or "") for item in selected}:
            write_plans.set_item_status(str(plan.get("id") or ""), str(entry.get("id") or ""), "rejected")
    if not selected:
        write_plans.mark_plan_status(str(plan.get("id") or ""), "rejected")
        return {"kind": "action", "action": tool_name, "status": "noop", "title": "Đã bỏ toàn bộ kế hoạch", "summary": "Không có mục nào được duyệt nên không ghi gì vào LMS.", "changes": [], "undo": {"available": False, "expires_at": None}, "view": None}
    if tool_name == "manage_course" and operation == "update":
        result = plan_executors.apply_manage_course_update(frappe, plan, merged)
    elif tool_name == "manage_chapter" and operation == "update":
        result = plan_executors.apply_manage_chapter_update(frappe, plan, merged)
    elif tool_name == "manage_chapter" and operation == "reorder":
        result = plan_executors.apply_manage_chapter_reorder(frappe, plan, merged)
    elif tool_name == "manage_lesson" and operation == "update":
        result = plan_executors.apply_manage_lesson_update(frappe, plan, merged)
    elif tool_name == "manage_lesson" and operation == "reorder":
        result = plan_executors.apply_manage_lesson_reorder(frappe, plan, merged)
    elif tool_name == "manage_lesson_block":
        result = plan_executors.apply_manage_lesson_block(frappe, plan, merged)
    elif tool_name == "manage_quiz" and operation == "update":
        result = plan_executors.apply_manage_quiz_update(frappe, plan, merged)
    elif tool_name in {"manage_assignment", "manage_programming_exercise"} and operation == "update":
        allowed = {"title", "question", "type", "course", "grade_assignment", "show_answer", "answer"} if tool_name == "manage_assignment" else {"title", "problem_statement", "language", "test_cases"}
        doctype = "LMS Assignment" if tool_name == "manage_assignment" else "LMS Programming Exercise"
        result = plan_executors.apply_simple_update(frappe, action=tool_name, doctype=doctype, allowed=allowed, plan=plan, merged=merged)
    else:
        write_plans.mark_plan_status(str(plan.get("id") or ""), "failed")
        return {"error": f"kế hoạch {tool_name}/{operation} chưa hỗ trợ apply từng mục an toàn"}
    refreshed = write_plans.get_plan(str(plan.get("id") or ""))
    rejected = [entry for entry in (refreshed or {}).get("items", []) if isinstance(entry, dict) and str(entry.get("status") or "") == "rejected"] if refreshed else []
    write_plans.mark_plan_status(str(plan.get("id") or ""), "partially_applied" if rejected else "applied")
    if isinstance(result, dict):
        result["plan_id"] = str(plan.get("id") or "")
        result["applied_items"] = [str(entry.get("id") or "") for entry in selected]
        result["rejected_items"] = [str(entry.get("id") or "") for entry in rejected]
    return result


@app.post("/webhook/frappe")
async def frappe_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Frappe-Webhook-Signature", "")
    if not verify_signature(settings.frappe_webhook_secret, body, signature):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
        queued = event_processor.enqueue(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    logged = handle_event(dict(request.headers), body)
    return {**logged, **queued}


def main() -> None:
    uvicorn.run(app, host=settings.gateway_host, port=settings.gateway_port, proxy_headers=True)


if __name__ == "__main__":
    main()
