# LMS Agentic Gateway (repo tách Frappe)

Gateway đứng ngoài Frappe LMS. Frappe giữ nguyên, giao tiếp qua REST + Webhook.
Widget là 1 file JS, nhúng vào Frappe bằng 1 thẻ `<script>`.

```
Người dùng -> Widget (<script>) -> Gateway :8001 -> Frappe REST :8000
Frappe Webhook -> Gateway /webhook/frappe (HMAC)
```

## Runtime (code đơn, stdlib-only)

- `src/gateway/runtime/model_client.py` — OpenAI-compatible `/chat/completions` (Ollama/vLLM/OpenAI đều được)
- `src/gateway/runtime/agent_loop.py` — vòng lặp đơn: chat -> tool -> chat (6 vòng), ép `member/_role`
- `src/gateway/runtime/tool_registry.py` — LLM chỉ thấy business tool, không thấy CRUD generic
- `src/gateway/runtime/policy.py` — 4 role: student/teacher/evaluator/admin
- `src/gateway/runtime/approval.py` — human approval + audit sqlite
- `src/gateway/connector/frappe_client.py` — wrapper REST duy nhất chạm Frappe
- `src/gateway/api/server.py` — `/health /chat /approve/:id /webhook/frappe /widget/*.js`
- `widget/agentic-copilot.js` — shell nhúng Frappe

## List tools (LLM chỉ được gọi chỗ này)

| Tool | Role | Ghi? | Map DocType thật |
|---|---|---|---|
| `search_courses` | all | đọc | LMS Course |
| `get_course_outline` | student/teacher/admin | đọc | Course + Chapter Reference + Lesson Reference |
| `get_lesson_context` | all | đọc, student-safe (lọc instructor_content/notes) | Course Lesson |
| `get_my_progress` | student | đọc (member ép từ session) | LMS Enrollment + LMS Course Progress |
| `get_batch_progress` | teacher/admin | đọc | LMS Batch Enrollment |
| `find_at_risk_students` | teacher/admin | đọc | LMS Enrollment |
| `get_quiz_submissions` | teacher/evaluator | đọc | LMS Quiz Submission |
| `get_assignment_submissions` | teacher/evaluator | đọc | LMS Assignment Submission |
| `draft_quiz` | teacher | nháp, không ghi | LMS Question + LMS Quiz (draft ngoài) |
| `draft_assignment_feedback` | teacher/evaluator | nháp | LMS Assignment Submission |
| `update_course_content_after_approval` | admin | **ghi, cần duyệt** | Course Lesson (atomic nhiều bước để agent_bridge sau) |

Generic `list/get/create/update/call_method` nằm trong `FrappeClient`, KHÔNG expose cho LLM.

## Chạy

```bash
copy .env.example .env
python examples/chat_cli.py "demo"
python tests/test_tools.py
cd src && python -m gateway.api.server
# -> http://127.0.0.1:8001/health
```
