# LMS Copilot

Frappe app that gives the Agentic Learning Copilot its tools, proposals and teacher approval flow.
It installs next to Frappe Learning (`lms`) and never changes LMS core code or LMS DocType permissions.

```text
Widget (teacher / learner) -> AI Gateway -> lms_copilot tool API -> Frappe Learning
                                              |
                                              +-> Copilot Proposal / Feedback Draft -> teacher approves -> write
```

## What it provides

| Area | Pieces |
| --- | --- |
| Tool API | `lms_copilot.api.get_tools`, `lms_copilot.api.call_tool` — role-filtered catalog with JSON schemas, one dispatcher, every call logged |
| Read tools | `get_course_outline`, `get_lesson_content`, `search_course_content`, `get_assignment_and_rubric`, `get_submission`, `get_learner_progress`, `gather_weekly_signals` |
| Write tools (drafts only) | `propose_lesson_quiz`, `propose_lesson_change`, `propose_learner_reminder`, `propose_rubric`, `escalate_to_teacher`, `propose_feedback` |
| Record tools | `record_submission_tests` (engine), `log_conversation_turn` (learner), `save_weekly_insight` |
| Approval | Pending → Approved → Applied / Failed, or Rejected / Expired. The reviewer's own permission is checked at approval, the lesson must still match the version the agent read, the write runs in a savepoint and is re-read to verify |
| Screens | `/copilot` review queue, `/copilot/review/<draft>` feedback review, `/copilot/proposal/<name>` diff approval, `/copilot/insight/<course>` weekly report, `/copilot/submit/<assignment>` learner project submission |
| DocTypes | Copilot Settings, Rubric, Project Submission, Feedback Draft, Proposal, Conversation, Weekly Insight, Tool Log |

## Roles

- **Teachers** are course instructors and Moderators. Batch Evaluators can review feedback.
- **Learners** use read tools for courses they are enrolled in, log their Q&A and escalate questions.
- **AI Engine** is created on install for background jobs. It can read and draft, never approve.
  It gets no Custom DocPerm rows, so it cannot override LMS permissions.

Learners reach tools and prompts only as pseudonyms (`L-XXXXXXXXXX`), never by email.

## Install

```bash
bench get-app /path/to/lms-agentic-gateway/lms_copilot
bench --site lms.localhost install-app lms_copilot
bench build --app lms_copilot
```

Set the Gateway URL and key in **Copilot Settings**. Without them, submissions wait in `Submitted`
until the Gateway picks them up.

## Gateway contract

```http
GET  /api/method/lms_copilot.api.get_tools
POST /api/method/lms_copilot.api.call_tool
     {"tool": "search_course_content", "arguments": {...}, "conversation": "CPC-00001",
      "model": "gpt-4o-mini", "tokens_in": 812, "tokens_out": 164}
```

Call with the widget user's session (`sid` cookie), or with the AI Engine service account for
background review and weekly jobs. When a learner submits a project the site POSTs
`{site, project_submission, rewrite_of, model}` to `<gateway_url>/ai/copilot/jobs/review`.

## Tests

```bash
bench --site lms.localhost run-tests --app lms_copilot
```
