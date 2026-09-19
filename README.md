# LMS Agentic Gateway

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![Tests](https://img.shields.io/badge/tests-85%20passing-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

An external, AI-native learning engine for **Frappe LMS 2.62.1**. It adds an agentic copilot
(student, teacher, evaluator and admin roles), an intelligent-tutoring learner model, and
approval-gated course authoring **without changing a single line inside Frappe**.

Frappe stays the system of record. The engine talks to it through the existing REST API and
removable Webhook / User / Role records; a one-file widget is injected into the LMS pages by an
nginx proxy. Remove the proxy and the webhooks and the LMS is exactly as it was.

---

## Table of contents

- [Why an external engine](#why-an-external-engine)
- [Architecture](#architecture)
- [What is implemented](#what-is-implemented)
- [Tool surface](#tool-surface)
- [HTTP API](#http-api)
- [Quick start](#quick-start)
- [Embedding the widget](#embedding-the-widget)
- [Configuration](#configuration)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Status and limitations](#status-and-limitations)

---

## Why an external engine

The design follows a few rules that are enforced in code, not just in docs:

1. **Do not fork the LMS.** All state the AI needs (evidence, mastery, approvals, memory, plans)
   lives in the engine's own SQLite store under `data/`. Frappe only receives the writes it would
   receive from a human.
2. **The model never sees raw REST.** The LLM can only call a closed `ToolRegistry`
   (`src/gateway/tools/catalog.py`). `FrappeClient` is the single object allowed to touch Frappe,
   and tools call it with the caller's own session.
3. **Identity is resolved server-side.** The widget sends Frappe's HttpOnly `sid` cookie; the
   gateway verifies it against Frappe and injects `member` / `role` into every tool call. The
   model cannot impersonate another user by passing a different id.
4. **Writes are plans, not side effects.** Mutating verbs produce a durable write plan → the user
   (or a teacher/admin) approves it → `plan_apply` executes the selected items with optimistic
   concurrency, single-use approvals, an audit record and a bounded, identity-scoped undo.
5. **Litmus test for every answer:** no response may end with *"go to menu X and click Y"*. If the
   copilot has to say that, a verb is missing and gets added.

## Architecture

```text
Browser :8080 --> nginx --> /lms, /api, /assets --> Frappe LMS :8000   (system of record)
                        \-> /ai/*               --> Gateway    :8001   (this repo, FastAPI)

Gateway :8001
  api/server.py            chat + SSE stream, learner endpoints, teacher console, approvals, webhook
  runtime/agent_loop       chat -> tool -> chat, at most 6 rounds, streaming with timing
  runtime/policy           which role may call which tool
  tools/                   38 business tools: read, draft, approval-gated write, client-directed
  runtime/learner          append-only evidence -> recomputable mastery with decay
  runtime/events           durable webhook inbox + reconciliation poller
  runtime/approval, runs,  single-use approvals, durable runs (pause/resume), write plans,
    write_plans, plan_apply, plan apply with optimistic concurrency, bounded undo
    actions
  connector/frappe_client  the only code path that calls Frappe REST

Frappe Webhooks --> durable inbox --> re-read record --> evidence --> mastery
Poller ----------------^ (reconciliation after downtime)
```

The proxy injects the Shadow-DOM widget only into `/lms` HTML. Per-user reads use the caller's
Frappe session; sync and notifications use a least-privilege service account.

The model client is OpenAI-compatible and stdlib-only (`urllib`), so any local or hosted endpoint
works: Ollama, vLLM, LM Studio, OpenAI.

## What is implemented

**Learner model (ITS)**
- Append-only evidence log ingested from Frappe events (quiz, assignment, lesson progress).
- Recomputable per-concept mastery with time decay (`decayed_p`); `POST /ops/recompute` rebuilds
  it from evidence.
- Draft concept extraction from course content → question mapping → **teacher approval** before a
  concept becomes live.

**Learning features (stored entirely outside Frappe)**
- Personalised tutor prompt with route-aware lesson context (the copilot knows which course /
  lesson page the learner is on), plus short-term and long-term memory.
- Feynman mode, dialogue checks, scored external learning sessions, review sets and soft review
  gates before moving on.
- Opt-in daily learning plan at 07:00 (configurable) delivered through Frappe `Notification Log`.
- Teacher risk console: at-risk learners, batch progress, one-use approved messages to students.

**Agentic course authoring**
- Complete authoring verbs for teacher / admin (course, chapter, lesson, lesson block, quiz,
  assignment, programming exercise, live class), all approval-gated.
- Tier-1 plan → approve → apply flow with item selection, optimistic concurrency and undo.

**Operations, privacy, safety**
- HMAC-verified webhooks with a durable inbox; the poller reconciles after downtime.
- Per-user erasure (`DELETE /me/data`), transcript retention window, request rate limiting,
  audit trail of every approval and action.
- Mock mode: with no Frappe credentials configured, tools return sample data so the runtime and
  the widget can be exercised standalone.

## Tool surface

38 tools are registered; `runtime/policy.py` decides which of them each role (`student`,
`teacher`, `evaluator`, `admin`) can see. Grouped by what they are allowed to do:

| Group | Examples | Notes |
| --- | --- | --- |
| Read (self-scoped) | `search_courses`, `get_course_outline`, `get_lesson_context`, `get_my_progress`, `get_my_mastery` | A student never sees `instructor_notes`; `member` is forced from the verified session. |
| Read (teacher / admin) | `get_batch_progress`, `list_at_risk_students`, `get_student_mastery`, `get_quiz_submissions`, `get_assignment_submissions`, `analyze_course_gaps` | |
| Draft (no side effect) | `draft_quiz`, `draft_assignment_feedback` | Output is shown, never written. |
| Student writes (idempotent, undoable) | `enroll_course`, `mark_lesson_complete`, `save_note`, `set_goal`, `start_session`, `create_review_set`, `schedule_review`, `record_feedback_correction` | Bounded undo via `POST /actions/{id}/undo`. |
| Authoring writes (approval-gated) | `manage_course`, `manage_chapter`, `manage_lesson`, `manage_lesson_block`, `manage_quiz`, `manage_assignment`, `manage_programming_exercise`, `create_live_class`, `publish_lesson_draft`, `message_students`, `update_course_content_after_approval` | Produce a write plan; nothing reaches Frappe before approval. |
| Client-directed | `navigate`, `render_view` | Validated navigation and closed view specs only — the model cannot emit arbitrary HTML / JS. |
| Memory | `remember_user_fact`, `recall_user_facts`, `forget_user_fact` | Long-term personal memory, per user, erasable. |

Every mutating tool returns a closed envelope (`tools/envelope.py`), so the widget renders
results from a typed contract rather than from free text.

## HTTP API

| Area | Endpoints |
| --- | --- |
| Health / identity | `GET /health`, `GET /identity` |
| Chat | `POST /chat`, `POST /chat/stream` (SSE), `GET /chat/sessions`, `GET/DELETE /chat/sessions/{id}` |
| Learner | `GET /me/mastery`, `POST /me/feedback`, `POST /learning/session`, `GET/POST /preferences`, `GET /plans/me`, `GET /queue`, `GET /review-sets/{id}`, `POST /review-sets/{id}/answer` |
| Concepts | `GET /concepts`, `POST /concepts/extract`, `POST /concepts/{id}/approve` |
| Actions and approvals | `GET /actions/recent`, `POST /actions/{id}/undo`, `POST /approve/{approval_id}`, `POST /teacher/actions`, `POST /teacher/actions/{id}/approve` |
| Teacher | `GET /teacher/risk`, `GET /teacher` (console page) |
| Ops | `POST /ops/recompute`, `POST /ops/run-daily`, `DELETE /me/data` |
| Integration | `POST /webhook/frappe`, `GET /widget.js`, `GET /widget/{file}` |

The full request / response contract, including the exact Frappe DocTypes and fields used, is in
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md). Operational and privacy behaviour (retention,
erasure, rate limits, webhook verification) is in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Quick start

Prerequisites: Python 3.10+, a reachable Frappe LMS 2.62.1 (optional — see mock mode) and an
OpenAI-compatible chat endpoint.

```powershell
git clone https://github.com/ducduong12123/lms-agentic-gateway.git
cd lms-agentic-gateway
pip install -r requirements.txt          # fastapi + uvicorn only

Copy-Item .env.example .env              # set LLM_* and FRAPPE_*; leave FRAPPE_API_KEY empty for mock mode
$env:PYTHONPATH = 'src'
python -m gateway.api.server             # http://127.0.0.1:8001/health
python examples/chat_cli.py              # runs one agent turn with a fake LLM + mock Frappe, no keys needed
```

With Docker (engine + nginx on `:8080`; expects the Frappe compose network `lms_default`):

```powershell
Copy-Item .env.example .env
docker compose up --build                # set DOCKER_LLM_BASE_URL if the model is not on the host at :20128
```

`scripts/start.ps1` is a convenience for the Windows + WSL2 setup used during development: it
discovers the Windows host address from inside WSL and runs the compose stack there.

Open `http://localhost:8080/lms`. Frappe stays reachable directly at `:8000`; the engine health
endpoint is `http://127.0.0.1:8001/health`. Teacher and learner pages are served at `/ai/teacher`
and `/ai/plan`.

## Embedding the widget

The widget is a single file, `widget/agentic-copilot.js`, rendered in a Shadow DOM so it cannot
collide with LMS styles. Two ways to load it (details in [`widget/README.md`](widget/README.md)):

```html
<!-- Website Settings -> HTML Block / Custom Script -->
<script src="http://127.0.0.1:8001/widget/agentic-copilot.js"
        data-gateway="http://127.0.0.1:8001" data-role="student"></script>
```

or, for development, `web_include_js` in `lms/hooks.py`. The widget reads
`window.frappe.session.user` for display only — the gateway re-derives identity from the `sid`
cookie on every request.

Webhooks: in Frappe, create a Webhook for `LMS Course`, `Course Chapter`, `Course Lesson`,
`LMS Quiz`, `LMS Enrollment`, … pointing at `http://<gateway>:8001/webhook/frappe`, with the same
secret as `FRAPPE_WEBHOOK_SECRET`.

## Configuration

All settings come from environment variables (a `.env` next to the repo root is loaded with a
stdlib parser; the real environment always wins).

| Variable | Default | Purpose |
| --- | --- | --- |
| `GATEWAY_HOST` / `GATEWAY_PORT` | `127.0.0.1` / `8001` | Bind address of the engine. |
| `PUBLIC_ORIGINS` | localhost `:8080` / `:8000` variants | CORS allow-list for the widget. |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Ollama on `:11434` | Any OpenAI-compatible endpoint. |
| `FRAPPE_URL` / `FRAPPE_SITE_HOST` | `http://learning.test:8000` / `lms.localhost` | Frappe base URL and `Host` header for multi-site setups. |
| `FRAPPE_API_KEY` / `FRAPPE_API_SECRET` | empty → mock mode | Least-privilege service account for sync and notifications. |
| `FRAPPE_WEBHOOK_SECRET` | empty | HMAC secret shared with the Frappe Webhook record. |
| `IDENTITY_CACHE_TTL` | `60` s | Cache of verified `sid` → user lookups. |
| `POLL_INTERVAL_SECONDS` | `900` | Reconciliation poller interval. |
| `SCHEDULER_TIMEZONE` / `DAILY_PLAN_HOUR` | `Asia/Ho_Chi_Minh` / `7` | Opt-in daily plan delivery. |
| `TRANSCRIPT_RETENTION_DAYS` | `90` | Chat transcript retention window. |

## Testing

```powershell
$env:PYTHONPATH = 'src'
python -m pytest -q                      # 85 tests: agent loop, policy, tools, plans, security, widget contract
python scripts/contract_check.py         # checks the Frappe DocType / field contract against docs/API_CONTRACT.md
```

The tests run without a Frappe instance or an LLM; external calls are faked at the client
boundary.

## Project layout

```text
src/gateway/
  api/          server.py (FastAPI), webhook.py (HMAC verify + ingest)
  connector/    frappe_client.py — the only REST caller
  runtime/      agent_loop, model_client, policy, tool_registry, tool_bundles, identity,
                learner (ITS), features, events, scheduler, memory, long_memory,
                approval, runs, write_plans, plan_apply, actions, trace, route_adapter
  tools/        catalog + verbs_student / verbs_teacher / verbs_course_authoring / verbs_client,
                envelope (closed result contracts), plan_executors
widget/         agentic-copilot.js (Shadow DOM), route-adapter.js, README.md
web/            plan.html, teacher.html
proxy/          nginx.conf (routes + widget injection)
docs/           API_CONTRACT.md, OPERATIONS.md, PLAN_AGENTIC_SHELL.md (design notes, Vietnamese)
examples/       chat_cli.py, Frappe integration snippets
tests/          85 tests
```

## Status and limitations

- Version `0.1.0`; built and verified against Frappe LMS **2.62.1**. Other versions may differ in
  DocType fields — run `scripts/contract_check.py` first.
- Storage is SQLite: fine for one gateway process per site, not for horizontal scaling.
- Long-term memory retrieval is lexical; there is no embedding index yet.
- A task-success evaluation harness (fixed tasks per role, including tasks the copilot must
  refuse) is the next milestone; numbers will be published here once they exist.
- Code comments are partly Vietnamese; the design notes in `docs/PLAN_AGENTIC_SHELL.md` are
  Vietnamese.

## License

MIT — see [LICENSE](LICENSE).
