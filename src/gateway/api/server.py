"""FastAPI entrypoint for the external LMS agent engine."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gateway.api.webhook import handle_event, verify_signature
from gateway.config import settings
from gateway.connector.frappe_client import FrappeClient
from gateway.runtime.agent_loop import learner_context_for_prompt, run_agent, run_agent_stream
from gateway.runtime.approval import approve as claim_approval
from gateway.runtime.approval import mark_executed
from gateway.runtime.events import EventProcessor
from gateway.runtime import features, learner
from gateway.runtime.identity import Identity, IdentityResolver
from gateway.runtime.long_memory import search_memories
from gateway.runtime.memory import short_term_memory
from gateway.runtime.model_client import OpenAICompatClient
from gateway.runtime.route_adapter import load_lesson_context, resolve_lms_context
from gateway.runtime.scheduler import ProactiveWorker
from gateway.tools.catalog import build_registry

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
    mode: str = Field(default="chat", pattern="^(chat|feynman|check)$")


class ConceptExtractPayload(BaseModel):
    lesson: str = Field(min_length=1, max_length=256)


class LearningSessionPayload(BaseModel):
    mode: str = Field(pattern="^(feynman|check)$")
    concept_id: str = Field(min_length=1, max_length=128)
    transcript: str = Field(min_length=20, max_length=12000)


class FeedbackPayload(BaseModel):
    concept_id: str = Field(min_length=1, max_length=128)
    note: str = Field(default="not-accurate", max_length=500)


class PreferencePayload(BaseModel):
    daily_plan_opt_in: bool
    quiet_hours: str = Field(default="", max_length=128)


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
    return llm, build_registry(frappe)


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


def _memory_context(payload: ChatPayload, identity: Identity) -> tuple[str | None, dict[str, Any]]:
    conversation_id = payload.conversation_id.strip()
    key = None
    if conversation_id:
        raw_key = f"{identity.user}\0{identity.role}\0{conversation_id}".encode("utf-8")
        key = hashlib.sha256(raw_key).hexdigest()
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
        "long_term_memories": search_memories(identity.user, payload.message, top_k=4),
        **tutor,
    }
    if key:
        context["recent_messages"] = short_term_memory.get(key)
    return key, context


def _sse(event: dict) -> bytes:
    event_name = str(event.get("t") or "message")
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


def _require_same_origin(request: Request) -> None:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin or origin not in settings.public_origins:
        raise HTTPException(status_code=403, detail="same-origin request required")


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
    return FileResponse(WIDGET_DIR / "agentic-copilot.js", media_type="application/javascript")


@app.get("/widget/{name:path}")
def widget_compat(name: str):
    target = (WIDGET_DIR / name).resolve()
    if not target.is_relative_to(WIDGET_DIR.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="widget not found")
    return FileResponse(target, media_type="application/javascript" if target.suffix == ".js" else None)


@app.post("/chat")
def chat(payload: ChatPayload, identity: Identity = Depends(trusted_identity)) -> dict:
    _check_rate(identity)
    llm, registry = _deps(identity)
    memory_key, context = _memory_context(payload, identity)
    output = run_agent(llm, registry, identity.role, payload.message, context)
    short_term_memory.append(memory_key, payload.message, output.get("answer", ""))
    return output


@app.post("/chat/stream")
def chat_stream(payload: ChatPayload, identity: Identity = Depends(trusted_identity)) -> StreamingResponse:
    _check_rate(identity)
    llm, registry = _deps(identity)
    memory_key, context = _memory_context(payload, identity)

    def events():
        for event in run_agent_stream(llm, registry, identity.role, payload.message, context):
            if event.get("t") == "done":
                short_term_memory.append(memory_key, payload.message, event.get("answer", ""))
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    return features.approve_teacher_action(action_id, identity.user, _base_frappe())


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
) -> dict:
    _require_same_origin(request)
    try:
        record = claim_approval(approval_id, identity.user, identity.role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="approval not found or already used")

    _, registry = _deps(identity)
    tool = registry.get(record["tool"])
    if not tool or not tool.needs_approval or identity.role not in tool.approval_roles:
        raise HTTPException(status_code=403, detail="tool cannot be approved by this identity")
    args = {
        key: value
        for key, value in dict(record["args"] or {}).items()
        if not str(key).startswith("_")
    }
    args["member"] = identity.user
    args["_role"] = identity.role
    try:
        result = tool.func(args)
    except Exception as exc:
        result = {"error": str(exc)}
    mark_executed(approval_id, result)
    return {"ok": "error" not in result, "tool": record["tool"], "result": result}


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
