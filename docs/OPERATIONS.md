# Operations and Privacy

- Use `http://localhost:8080/lms` for the AI-enhanced LMS. Port `8000` remains a clean Frappe endpoint.
- Start with `scripts/start.ps1`; it discovers the current Windows host address visible from WSL so the container can reach the model proxy on port 20128.
- Health: `GET http://127.0.0.1:8001/health` reports queue, evidence, concept, and scheduler state.
- Reconciliation runs every `POLL_INTERVAL_SECONDS`; webhook delivery is deduplicated by doctype, name, and modified timestamp.
- Daily plans run at `DAILY_PLAN_HOUR` in `SCHEDULER_TIMEZONE` and only for explicit opt-ins.
- Feynman/check transcripts are redacted after `TRANSCRIPT_RETENTION_DAYS` (default 90).
- Chat is limited to 30 requests per authenticated user per minute.
- `DELETE /ai/me/data` removes one user's evidence, mastery, sessions, plans, preferences, memories, feedback, and trace rows.

Rollback does not require a Frappe deploy: stop the external compose stack, disable the six/seven
Webhook records, revoke the `ai-engine@…` API key, then disable the service user. Frappe course,
submission, progress, and certificate records are untouched.
