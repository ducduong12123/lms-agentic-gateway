# Plan: từ Chatbot sang Agentic Shell

Tài liệu giao việc cho AI thực thi. Đọc hết mục 0–3 trước khi viết dòng code đầu tiên.

---

## 0. Bối cảnh

**Repo liên quan**

- `D:\lms-agentic-gateway` — engine FastAPI, chạy `:8001`. Toàn bộ việc trong plan này nằm ở đây.
- `D:\frappelms` — Frappe LMS 2.62.1, chạy `:8000`. **Không sửa gì trong repo này.**
- nginx `:8080` proxy `/lms`, `/api`, `/assets` → Frappe; `/ai/*` → engine; inject widget Shadow DOM vào HTML `/lms`.

**Hiện trạng engine (đã có, đừng làm lại)**

Evidence log append-only; mastery per-concept có decay (`decayed_p`); concept extraction + teacher approval; Feynman / dialogue check; soft gate; risk detection; daily plan 07:00 qua `Notification Log`; webhook HMAC + poller reconcile; erasure; audit; rate limit. Khoảng 4000 dòng, chất lượng tốt.

**Vấn đề cần giải**

`src/gateway/tools/catalog.py` có ~17 tool. Đếm theo động từ:

- đọc: `search_courses`, `get_course_outline`, `get_lesson_context`, `get_my_progress`, `get_my_mastery`, `get_batch_progress`, `list_at_risk_students`, `find_at_risk_students`, `get_student_mastery`, `get_quiz_submissions`, `get_assignment_submissions`, `recall_user_facts`
- nháp rồi nói ra: `draft_quiz`, `draft_assignment_feedback`
- **ghi thật: đúng 1 cái** — `update_course_content_after_approval`, chỉ `admin`, chỉ set được `Course Lesson.body`

Trong `src/gateway/runtime/policy.py`, role `student` có **0 tool ghi**.

⇒ Shell không thể thực thi ý định người dùng. Nó chỉ tra cứu rồi mô tả. Đó là định nghĩa của chatbot, và không UI nào sửa được. Thiếu sót không nằm ở chỗ đặt AI — nằm ở **vốn động từ**.

**Litmus test (dùng để nghiệm thu toàn bộ plan)**

> Không câu trả lời nào được phép kết thúc bằng "bạn vào mục X rồi bấm Y nhé".

Mỗi lần câu đó xuất hiện = thiếu đúng một verb. Ghi lại, bổ sung.

---

## 1. Nguyên tắc bất di bất dịch

Vi phạm bất kỳ điều nào dưới đây = làm lại.

1. **Một điểm vào duy nhất: shell bên phải.** TUYỆT ĐỐI không inject UI rải rác vào DOM của Frappe — không badge, không mastery ring trong course outline, không nút nhỏ cạnh lesson. Trí tuệ chỉ hiện qua shell, hoặc qua canvas mà agent chiếm. Người dùng không bao giờ phải đi mò xem AI nằm ở đâu.
2. **Frappe là system of record.** Mọi ghi đi qua `FrappeClient`. Không chạm DB Frappe trực tiếp. Không thêm code AI vào app Frappe.
3. **Ghi với tư cách người dùng.** Dùng `FrappeClient.with_session(sid)` cho mọi verb do người dùng kích hoạt — quyền và audit của Frappe tự nhiên đúng. Service account (`api_key`/`api_secret`) chỉ dành cho scheduler và notification.
4. **Idempotent.** Mọi verb kiểm tra tồn tại trước khi tạo. Gọi hai lần không được sinh hai bản ghi.
5. **Model không bao giờ sinh HTML.** `render_view` chỉ nhận ViewSpec có `type` nằm trong whitelist; widget render bằng renderer tĩnh + `esc()` sẵn có. Không `innerHTML` với dữ liệu từ model.
6. **Phân hạng quyền ghi.** Ghi chạm người khác ⇒ `needs_approval=True`. Ghi self-scope ⇒ chạy ngay, nhưng **bắt buộc hoàn tác được**.
7. **Giữ `audit()`.** `agent_loop` đã audit mọi tool call. Không bỏ, không bọc lại.
8. **Mỗi verb mới phải có pytest** với `FrappeClient` giả. Không test = chưa xong.

---

## 2. Ba workstream

| # | Tên | Giải quyết | Phase |
|---|---|---|---|
| W1 | Verb layer | Shell có tay để làm, không chỉ có miệng để nói | 0, 1, 4 |
| W2 | Canvas control | Shell lái được app và chiếm được vùng chính | 2 |
| W3 | Shell furniture | Shell trông như agent, không như ChatGPT | 3 |

---

## 3. Hợp đồng dữ liệu — định nghĩa trước, code sau

### 3.1 ActionResult — mọi tool ghi trả về đúng hình này

```json
{
  "kind": "action",
  "action": "enroll_course",
  "action_id": "act_ab12cd34",
  "status": "done",
  "title": "Đã ghi danh Python cơ bản",
  "summary": "Bạn được thêm vào khóa PY-101 với vai trò Member.",
  "changes": [
    {"doctype": "LMS Enrollment", "name": "abc123", "op": "create"}
  ],
  "undo": {"available": true, "expires_at": 1789000000},
  "view": {"type": "course", "course": "PY-101"}
}
```

- `status`: `done` | `pending_approval` | `noop` | `failed`
- `noop` = đã tồn tại sẵn (idempotency chạm). Vẫn trả card, `title` nói rõ "đã có sẵn".
- `view` = ViewSpec hoặc `null`. Nếu có, widget hiện nút "Mở".
- `undo.expires_at` = `created + 600` (10 phút) cho verb self-scope; `available: false` cho verb không đảo được.

### 3.2 ViewSpec — whitelist đóng

| type | trường bắt buộc |
|---|---|
| `review_set` | `set_id` |
| `session` | `mode` (`feynman` \| `viva` \| `check`), `concept_id` |
| `knowledge_map` | `course` (có thể rỗng = toàn bộ) |
| `mastery_detail` | `concept_id` |
| `plan` | `date` |
| `diff` | `doctype`, `name`, `before`, `after` |
| `course` | `course` |
| `lesson` | `course`, `chapter`, `lesson_number` |

`type` ngoài bảng ⇒ từ chối ngay ở server, không gửi xuống client.

### 3.3 ClientDirective

```json
{"kind": "client", "op": "navigate",    "route": "/lms/courses/PY-101/learn/1-2"}
{"kind": "client", "op": "render_view", "spec": {"type": "review_set", "set_id": "rs_x1"}}
```

### 3.4 SSE — event mới trong `run_agent_stream`

Đang có: `session`, `round`, `tool`, `token`, `thought`, `done`.

Thêm hai loại, yield **ngay khi tool trả về**, không đợi `done`:

```json
{"t": "action", "card": { ...ActionResult... }}
{"t": "client", "directive": { ...ClientDirective... }}
```

`run_agent` (non-stream) trả thêm hai khóa: `"actions": [...]`, `"directives": [...]`.

---

## 4. Các phase

### Phase 0 — Nền móng (bắt buộc làm trước, không bỏ qua)

**`src/gateway/connector/frappe_client.py`**

- Thêm `delete_document(doctype, name)` → `DELETE /api/resource/{doctype}/{name}`.
  *Hiện chưa có method này — không có nó thì không undo được gì.*

**`src/gateway/runtime/actions.py`** (file mới)

- Bảng `actions` trong `data/gateway.db`:
  `id TEXT PK, member TEXT, tool TEXT, args_json TEXT, result_json TEXT, undo_json TEXT, status TEXT, created REAL, undone_at REAL`
- Hàm: `record_action(...) -> action_id`, `get_action(id)`, `mark_undone(id)`, `recent_actions(member, limit=20)`, `undo_action(id, frappe) -> dict`
- `undo_json` lưu đủ để đảo:
  `{"op": "delete", "doctype": "LMS Enrollment", "name": "abc"}`
  hoặc `{"op": "restore", "doctype": "...", "name": "...", "fields": {"status": "Incomplete"}}`
- `undo_action` từ chối nếu: quá `expires_at`, đã `undone`, hoặc `member` không khớp người gọi.

**`src/gateway/tools/envelope.py`** (file mới)

- `action_result(action, title, summary, changes=None, undo=None, view=None, status="done") -> dict`
- `client_directive(op, **kw) -> dict`
- `validate_view_spec(spec) -> dict` (raise nếu `type` ngoài whitelist mục 3.2)

**`src/gateway/runtime/agent_loop.py`**

- Trong `_run_tool`: nếu output là dict có `kind` ∈ `{"action", "client"}` → tách vào hai list tích luỹ trong `context`.
- `run_agent` → thêm `actions`, `directives` vào dict trả về (giữ nguyên `answer`, `tool_calls`, `approvals`, `timings`).
- `run_agent_stream` → yield `{"t": "action"}` / `{"t": "client"}` ngay sau khi tool chạy xong; `done` vẫn kèm đủ cả hai list.
- Tool result gửi ngược lại cho LLM: rút gọn còn `{"status": ..., "title": ...}` để model không lặp lại nội dung card thành văn xuôi.

---

### Phase 1 — Verb layer cho student (phần đổi chất lớn nhất)

**`src/gateway/tools/verbs_student.py`** (file mới), đăng ký từ `catalog.build_registry(frappe)`.

| tool | tham số | ghi gì vào Frappe | idempotency | undo |
|---|---|---|---|---|
| `enroll_course` | `course` | create `LMS Enrollment` `{member, course, member_type: "Student", role: "Member"}` | list `LMS Enrollment` filters `{member, course}` → có thì `noop` | delete doc |
| `mark_lesson_complete` | `course`, `chapter`, `lesson` | create `LMS Course Progress` `{course, chapter, lesson, member, status: "Complete"}` | list theo `{member, lesson}` → có thì update `status` | restore status cũ, hoặc delete nếu vừa tạo |
| `save_note` | `lesson`, `course`, `note`, `highlighted_text?`, `color?` | create `LMS Lesson Note` `{lesson, course, member, note, color}` | không (cho phép nhiều note) | delete doc |
| `create_review_set` | `course?`, `concept_id?`, `size=5` | **không ghi Frappe.** Lấy `learner.weak_concepts()`, sinh câu hỏi bằng LLM, lưu bảng `review_sets` trong `gateway.db` | hash `(member, concepts, ngày)` | xoá set |
| `schedule_review` | `concept_id`, `when` (ISO date) | gateway.db `scheduled_reviews`; job 07:00 sẵn có đẩy ra `Notification Log` | `(member, concept_id, date)` | huỷ lịch |
| `set_goal` | `daily_plan_opt_in?`, `quiet_hours?` | `learner.set_member_pref()` | tự nhiên | giá trị cũ |
| `start_session` | `mode` (`feynman` \| `viva` \| `check`), `concept_id` | dùng `features.evaluate_session` sẵn có | — | `available: false` |

`color` chỉ nhận: `Red`, `Blue`, `Green`, `Yellow`, `Purple`.

**`src/gateway/runtime/policy.py`** — thêm cả 7 verb vào list `"student"`. Không verb nào cần approval (đều self-scope + undo được).

**Bảng mới trong `data/gateway.db`**

- `review_sets`: `id, member, course, concepts_json, items_json, created, status`
- `scheduled_reviews`: `id, member, concept_id, due_date, status, created`

**Endpoint mới trong `src/gateway/api/server.py`**

- `GET /review-sets/{set_id}` → trả set (kiểm tra `identity.user == member`)
- `POST /review-sets/{set_id}/answer` body `{item_id, answer}` → chấm (LLM hoặc so khớp) → ghi evidence:

```python
learner.record_evidence(
    member=identity.user,
    concept_id=item["concept_id"],
    kind="review_item",
    outcome=score_0_to_1,
    ref_doctype="GatewayReviewSet",
    ref_name=f"{set_id}:{item_id}",
)
```

→ trả `{correct, explanation, mastery_after}`

---

### Phase 2 — Canvas control (phần đổi cảm giác)

**`src/gateway/tools/verbs_client.py`** (file mới) — không chạm Frappe, chỉ trả `ClientDirective`.

- `navigate(route)` — validate theo whitelist regex, nghịch đảo của `widget/route-adapter.js`:

```
^/lms$
^/lms/courses/[^/]+$
^/lms/courses/[^/]+/learn/\d+-\d+$
^/lms/batches(/[^/]+)?$
```

  Ngoài whitelist ⇒ raise. Chặn tuyệt đối `javascript:`, `//`, `http://`, `https://`, `..`.
- `render_view(spec)` — chạy `validate_view_spec()` rồi trả directive.

Thêm cả hai vào `policy.py` cho **mọi** role.

**`widget/agentic-copilot.js`**

- Thêm `#acp-canvas` trong **cùng shadow root** (không đụng DOM Frappe):

```css
#acp-canvas{position:fixed;top:0;bottom:0;left:0;right:var(--acp-shell-w,560px);
            background:var(--acp-bg);overflow:auto;z-index:2147483000}
```

  Có nút Đóng → ẩn canvas, lộ lại trang LMS bên dưới nguyên vẹn.
- Renderer riêng cho từng `type` trong whitelist. Mỗi renderer dựng DOM bằng `createElement` + `textContent`/`esc()`. **Không `innerHTML` với dữ liệu từ server.**
- Xử lý `t: "client"`:
  - `navigate` → `location.assign(route)` cho v1. Chấp nhận reload trang; trước khi đi, lưu `{open: true, session_id}` vào `localStorage` để shell tự mở lại đúng phiên sau khi widget được inject lại. *(Nâng cấp sau: hook vào Vue Router của LMS để điều hướng không reload.)*
  - `render_view` → gọi renderer tương ứng, hiện canvas.
- Xử lý `t: "action"` → render ActionCard (xem Phase 3).

---

### Phase 3 — Nội thất shell (phần đổi nhận diện)

**Gỡ khỏi `widget/agentic-copilot.js`** — mỗi phần tử giống ChatGPT là một lần người dùng xếp sản phẩm vào ngăn "chatbot gắn thêm":

- nút `Select effort` (giữ tham số `effort`, mặc định `auto`, bỏ khỏi UI)
- `♡` / `☹` / `↻` / `⤴ share` / `⌖ pin`
- lịch sử chat đang là nút chính ở header → lùi vào menu `⋯`

**Thêm vào shell**

- **ActionCard**: tiêu đề, danh sách `changes` gọn, nút **Hoàn tác** (ẩn khi hết hạn), nút **Mở** khi có `view`. Với `status: "pending_approval"` → nút **Duyệt** gọi `POST /approve/{approval_id}` (endpoint đã có).
- **Hàng đợi `#acp-queue`** trên cùng shell: gộp `features.plans_for(member)` + approvals đang chờ + `scheduled_reviews` đến hạn. Badge số việc trên FAB `✦`.
- Dải **trạng thái người học** thay cho chip ngữ cảnh: concept yếu nhất + mastery, lấy từ `/me/mastery`.

**Endpoint mới**

- `GET /queue` → `{plans, approvals, reviews}`
- `POST /actions/{action_id}/undo`
- `GET /actions/recent`

---

### Phase 4 — Verb layer cho teacher (phần bán được)

**`src/gateway/tools/verbs_teacher.py`** — tất cả `needs_approval=True`:

- `message_students(course|batch, targets[], subject, message)` — bọc `features.propose_teacher_action` + `approve_teacher_action` đã có
- `create_live_class(batch, title, when, duration)`
- `publish_lesson_draft(course, chapter, title, body)` — mở rộng `update_course_content_after_approval` từ "chỉ sửa body" sang tạo lesson/quiz thật

**Cohort insight — khóa học tự sửa chính nó**

- `analyze_course_gaps(course)` → concept nào có >X% học viên hỏng → suy ra kiến thức tiên quyết bị thiếu → trả ViewSpec `type: "diff"` kèm bản nháp bài học còn thiếu; giáo viên duyệt là vào thật.

---

## 5. Bảng tham chiếu — đã kiểm tra trong repo, đừng đoán lại

**Doctype Frappe**

| Doctype | Trường dùng tới |
|---|---|
| `LMS Enrollment` | `member` (User), `course` (LMS Course), `member_type` (Student/Mentor/Staff), `role` (Member/Admin), `current_lesson`, `progress`, `enrollment_from_batch` |
| `LMS Course Progress` | `course`, `chapter`, `lesson`, `member`, `status` (Complete/Partially Complete/Incomplete) |
| `LMS Lesson Note` | `lesson`, `course`, `member`, `color` (Red/Blue/Green/Yellow/Purple), `highlighted_text`, `note` |
| `LMS Quiz Submission` | `quiz`, `result`, `score`, `member`, `course`, `score_out_of`, `percentage`, `passing_percentage`, `quiz_title` |

⚠️ Frappe LMS **không có** whitelisted method `enroll_in_course` (chỉ có `enroll_in_batch`, `enroll_in_program` trong `lms/lms/utils.py`). Ghi danh khóa ⇒ tạo doc `LMS Enrollment` trực tiếp.

**Hàm engine dùng lại, đừng viết mới**

```python
learner.record_evidence(member, concept_id, kind, outcome, ref_doctype, ref_name, ref_modified, weight)
learner.weak_concepts(member, course, limit)
learner.set_member_pref(member, daily_plan_opt_in, quiet_hours)
learner.get_member_pref(member)
learner.record_learning_session(member, mode, concept_id, transcript, rubric_score)
learner.mastery_snapshot(member)
features.evaluate_session(llm, member, mode, concept_id, transcript)
features.plans_for(member)
features.propose_teacher_action(course, target, subject, message)
features.approve_teacher_action(action_id, approved_by, frappe)
approval.request_approval / approve / mark_executed / audit
```

---

## 6. Test

| File | Nội dung |
|---|---|
| `tests/test_actions.py` | record → undo → hết hạn → undo lần hai phải fail |
| `tests/test_envelope.py` | ActionResult đúng schema; ViewSpec ngoài whitelist bị raise |
| `tests/test_verbs_student.py` | mỗi verb: 1 happy path + 1 gọi lại ra `noop` + 1 undo thành công |
| `tests/test_verbs_client.py` | route whitelist chặn `/admin`, `javascript:alert(1)`, `//evil.com`, `../..` |
| `tests/test_stream_events.py` | `run_agent_stream` yield `t: "action"` và `t: "client"` đúng thứ tự |

`FrappeClient` giả: subclass ghi lại call vào list, trả dict cố định. Không gọi mạng trong test.

Chạy:

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python scripts/contract_check.py   # cập nhật cho endpoint mới
```

---

## 7. Definition of done

Sáu câu lệnh này phải chạy được end-to-end, mỗi câu dẫn tới **ghi thật + ActionCard + canvas/điều hướng đổi theo**:

1. "Ghi danh khóa Python cơ bản cho tôi" → `LMS Enrollment` thật, card có nút Hoàn tác, hoàn tác xoá được
2. "Đánh dấu bài này xong rồi cho tôi sang bài tiếp" → `LMS Course Progress` + `navigate` sang lesson kế
3. "Ôn cho tôi 5 câu phần tôi đang yếu nhất" → canvas **trở thành** bộ ôn; trả lời xong `mastery` đổi thật
4. "Lưu ghi chú: closure là hàm nhớ scope lúc nó sinh ra" → `LMS Lesson Note` thật
5. "Nhắc tôi ôn lại cái này thứ Năm" → `scheduled_reviews` + xuất hiện ở hàng đợi shell
6. Teacher: "Nhắn 5 bạn đang tụt lại trong batch này" → approval card → duyệt → gửi thật

Và: mở shell lên, không còn phần tử nào khiến người ta nghĩ tới ChatGPT.

---

## 8. Thứ tự và lý do

```
Phase 0  →   1   →   2   →   3   →   4
  nền      đổi     đổi     đổi     bán
          chất    cảm     nhận    được
                  giác    diện
```

Không nhảy cóc. Phase 1 mà thiếu Phase 0 thì không có undo, không có ActionCard, và toàn bộ verb sẽ phải viết lại.
