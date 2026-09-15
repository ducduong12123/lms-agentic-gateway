"""Ingest Frappe learning events thành ITS evidence append-only.

Theo engine-plan: webhook chỉ là chuông báo (payload mỏng), engine đọc lại bản
ghi thật rồi ghi evidence idempotent theo (doctype, name, modified, concept).
Poller đối soát dùng sync_cursor để lấp sự kiện mất.
"""

from __future__ import annotations

import time
from pathlib import Path

from gateway.connector.frappe_client import FrappeClient
from gateway.runtime import learner


def _cursor_get(doctype: str, path: Path | None = None) -> str:
    conn = learner._conn(path)
    row = conn.execute(
        "SELECT last_modified FROM sync_cursor WHERE doctype=?", (doctype,)
    ).fetchone()
    conn.close()
    return str(row["last_modified"] or "") if row else ""


def _cursor_set(doctype: str, value: str, path: Path | None = None) -> None:
    conn = learner._conn(path)
    conn.execute(
        "INSERT INTO sync_cursor(doctype, last_modified) VALUES (?,?)"
        " ON CONFLICT(doctype) DO UPDATE SET last_modified=excluded.last_modified",
        (doctype, str(value or "")),
    )
    conn.commit()
    conn.close()


def _concept_for_question(question: str, path: Path | None = None) -> str:
    conn = learner._conn(path)
    row = conn.execute(
        "SELECT concept_id FROM question_concept WHERE question=? LIMIT 1",
        (str(question or ""),),
    ).fetchone()
    conn.close()
    return str(row["concept_id"]) if row else ""


def _ensure_lesson_concept(
    lesson: str, course: str, path: Path | None = None
) -> str:
    """Phase 1: chưa có concept thì gắn tạm theo lesson để không mất evidence."""
    lesson = str(lesson or "").strip()
    if not lesson:
        return ""
    concepts = learner.concepts_for_lesson(lesson, path)
    if concepts:
        return str(concepts[0]["id"])
    created = learner.upsert_concept(
        f"Lesson {lesson}", course=str(course or ""),
        description="Concept tạm theo lesson, chờ trích/gắn tay.",
        source_lesson=lesson, status="draft", path=path,
    )
    learner.link_lesson_concept(lesson, created["id"], 1.0, path=path)
    return str(created["id"])


def ingest_quiz_submission(
    frappe: FrappeClient, name: str, path: Path | None = None
) -> dict:
    """Đọc LMS Quiz Submission thật, ghi 1 evidence mỗi câu hỏi."""
    doc = frappe.get_document("LMS Quiz Submission", str(name)).get("data", {})
    member = str(doc.get("member") or "").strip()
    modified = str(doc.get("modified") or "")
    results = doc.get("result") or doc.get("results") or doc.get("quiz_result") or []
    if isinstance(results, dict):
        results = results.get("data") or []
    written: list[dict] = []
    fallback_concept = ""
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question_name") or item.get("question") or "")
        if not question:
            continue
        raw = item.get("is_correct")
        if raw is None:
            raw = item.get("correct")
        outcome = 1.0 if raw in (1, True, "1", "True", "true") else 0.0
        concept_id = _concept_for_question(question, path)
        if not concept_id:
            if not fallback_concept:
                lessons = frappe.list_documents(
                    "Course Lesson",
                    filters={"quiz_id": doc.get("quiz")},
                    fields=["name", "course"],
                    limit=1,
                ).get("data", [])
                lesson = str(lessons[0].get("name") if lessons else f"quiz:{doc.get('quiz') or name}")
                course = str(doc.get("course") or (lessons[0].get("course") if lessons else ""))
                fallback_concept = _ensure_lesson_concept(lesson, course, path)
            concept_id = fallback_concept
        if not concept_id:
            continue
        written.append(
            learner.record_evidence(
                member, concept_id, "quiz_item", outcome,
                ref_doctype="LMS Quiz Submission", ref_name=str(name),
                ref_modified=f"{modified}::{question}", path=path,
            )
        )
    return {"member": member, "evidence": len(written), "duplicate": all(
        item.get("duplicate") for item in written) if written else False}


def ingest_assignment_submission(
    frappe: FrappeClient, name: str, path: Path | None = None
) -> dict:
    doc = frappe.get_document("LMS Assignment Submission", str(name)).get("data", {})
    member = str(doc.get("member") or "").strip()
    modified = str(doc.get("modified") or "")
    status = str(doc.get("status") or "").casefold()
    if status not in {"pass", "passed", "approved", "correct", "fail", "failed"}:
        return {"member": member, "evidence": 0}
    outcome = 1.0 if status in {"pass", "passed", "approved", "correct"} else 0.0
    lesson = str(doc.get("lesson") or doc.get("course_lesson") or "")
    course = str(doc.get("course") or "")
    concept_id = _ensure_lesson_concept(lesson, course, path)
    if not concept_id:
        return {"member": member, "evidence": 0}
    record = learner.record_evidence(
        member, concept_id, "assignment", outcome,
        ref_doctype="LMS Assignment Submission", ref_name=str(name),
        ref_modified=modified, path=path,
    )
    return {"member": member, "evidence": 1, "duplicate": record.get("duplicate", False)}


def ingest_programming_submission(
    frappe: FrappeClient, name: str, path: Path | None = None
) -> dict:
    doc = frappe.get_document("LMS Programming Exercise Submission", str(name)).get("data", {})
    member = str(doc.get("member") or "").strip()
    modified = str(doc.get("modified") or "")
    cases = doc.get("test_cases") or []
    if cases:
        outcome = sum(1 for case in cases if case.get("status") == "Passed") / len(cases)
    else:
        outcome = 1.0 if doc.get("status") == "Passed" else 0.0
    lesson = f"exercise:{doc.get('exercise') or name}"
    concept_id = _ensure_lesson_concept(lesson, "", path)
    if not concept_id:
        return {"member": member, "evidence": 0}
    record = learner.record_evidence(
        member, concept_id, "programming", outcome,
        ref_doctype="LMS Programming Exercise Submission", ref_name=str(name),
        ref_modified=modified, path=path,
    )
    return {"member": member, "evidence": 1, "duplicate": record.get("duplicate", False)}


def ingest_course_progress(
    frappe: FrappeClient, name: str, path: Path | None = None
) -> dict:
    doc = frappe.get_document("LMS Course Progress", str(name)).get("data", {})
    member = str(doc.get("member") or "").strip()
    modified = str(doc.get("modified") or "")
    if str(doc.get("status") or "").casefold() != "complete":
        return {"member": member, "evidence": 0}
    lesson = str(doc.get("lesson") or "")
    course = str(doc.get("course") or "")
    concept_id = _ensure_lesson_concept(lesson, course, path)
    if not concept_id:
        return {"member": member, "evidence": 0}
    record = learner.record_evidence(
        member, concept_id, "lesson_complete", 0.6,
        ref_doctype="LMS Course Progress", ref_name=str(name),
        ref_modified=modified, path=path,
    )
    return {"member": member, "evidence": 1, "duplicate": record.get("duplicate", False)}


def ingest_video_watch(
    frappe: FrappeClient, name: str, path: Path | None = None
) -> dict:
    doc = frappe.get_document("LMS Video Watch Duration", str(name)).get("data", {})
    member = str(doc.get("member") or "").strip()
    lesson = str(doc.get("lesson") or "")
    concept_id = _ensure_lesson_concept(lesson, str(doc.get("course") or ""), path)
    if not member or not concept_id:
        return {"member": member, "evidence": 0}
    record = learner.record_evidence(
        member, concept_id, "rewatch", 0.4,
        ref_doctype="LMS Video Watch Duration", ref_name=str(name),
        ref_modified=str(doc.get("modified") or ""), path=path,
    )
    return {"member": member, "evidence": 1, "duplicate": record.get("duplicate", False)}


def poll_since_cursor(
    frappe: FrappeClient, doctype: str, limit: int = 200,
    path: Path | None = None,
) -> dict:
    """Poller đối soát: đọc bản ghi modified > cursor, ingest, tiến cursor."""
    cursor = _cursor_get(doctype, path)
    filters = [["modified", ">", cursor]] if cursor else []
    rows = frappe.list_documents(
        doctype, filters=filters, fields=["name", "modified"],
        limit=max(1, min(200, int(limit or 200))), order_by="modified asc",
    ).get("data", [])
    ingested = 0
    last = cursor
    for item in rows if isinstance(rows, list) else []:
        name = str(item.get("name") or "")
        modified = str(item.get("modified") or "")
        if not name:
            continue
        if doctype == "LMS Quiz Submission":
            ingest_quiz_submission(frappe, name, path)
        elif doctype == "LMS Assignment Submission":
            ingest_assignment_submission(frappe, name, path)
        elif doctype == "LMS Course Progress":
            ingest_course_progress(frappe, name, path)
        elif doctype == "LMS Programming Exercise Submission":
            ingest_programming_submission(frappe, name, path)
        elif doctype == "LMS Video Watch Duration":
            ingest_video_watch(frappe, name, path)
        else:
            continue
        ingested += 1
        if modified:
            last = modified
    if last:
        _cursor_set(doctype, last, path)
    return {"doctype": doctype, "ingested": ingested, "cursor": last}


def handle_event_payload(
    frappe: FrappeClient, payload: dict, path: Path | None = None
) -> dict:
    """Webhook payload mỏng {doctype, name, modified}: đọc lại rồi ingest."""
    doctype = str(payload.get("doctype") or "")
    name = str(payload.get("name") or "")
    if not doctype or not name:
        return {"ok": False, "reason": "missing doctype/name"}
    started = time.time()
    if doctype == "LMS Quiz Submission":
        result = ingest_quiz_submission(frappe, name, path)
    elif doctype == "LMS Assignment Submission":
        result = ingest_assignment_submission(frappe, name, path)
    elif doctype == "LMS Course Progress":
        result = ingest_course_progress(frappe, name, path)
    elif doctype == "LMS Programming Exercise Submission":
        result = ingest_programming_submission(frappe, name, path)
    elif doctype == "LMS Video Watch Duration":
        result = ingest_video_watch(frappe, name, path)
    elif doctype == "Course Lesson":
        # Course Lesson has a custom enrollment/instructor gate in LMS 2.62.1.
        # The service account deliberately cannot bypass it; teacher extraction later
        # resolves content with the teacher's own sid.
        concept_id = _ensure_lesson_concept(name, "", path)
        result = {"concept_id": concept_id, "evidence": 0}
    else:
        return {"ok": True, "doctype": doctype, "ingested": 0}
    result["ok"] = True
    result["doctype"] = doctype
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result
