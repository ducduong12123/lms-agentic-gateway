# LMS Agentic Gateway

External AI-native learning engine for Frappe LMS 2.62.1. Frappe remains the system of record;
the engine communicates through existing REST methods and removable Webhook/User/Role records.
No AI code is required inside the Frappe app.

## Architecture

```text
Browser :8080 -> nginx -> /lms, /api, /assets -> Frappe :8000
                      -> /ai/*                -> FastAPI :8001
Frappe Webhooks -> durable inbox -> reread record -> evidence -> mastery
Poller ----------^ (reconciliation after downtime)
```

The proxy injects the Shadow DOM widget only into `/lms` HTML. Identity comes from the Frappe
`sid` cookie and is verified server-side. Per-user reads use that session; sync and notifications
use the least-privilege service account.

## Implemented Features

- Append-only evidence and recomputable concept mastery.
- Draft concept extraction, question mapping, and teacher approval.
- Personalized tutor prompt with route-aware lesson context and short/long memory.
- Feynman mode, dialogue checks, scored external learning sessions, and soft review gates.
- Opt-in 07:00 learning plans through Frappe `Notification Log`.
- Teacher risk console with one-use approved messages.
- Per-user erasure, transcript retention, request rate limiting, HMAC webhooks, and audit trails.

## Run and Test

```powershell
Copy-Item .env.example .env
$env:PYTHONPATH='src'
python -m pytest -q
python scripts/contract_check.py
.\scripts\start.ps1
```

Open `http://localhost:8080/lms`. Direct Frappe remains at `http://localhost:8000` and the engine
at `http://127.0.0.1:8001/health`. Teacher and learner pages are `/ai/teacher` and `/ai/plan`.

See [API contract](docs/API_CONTRACT.md) and [operations/privacy](docs/OPERATIONS.md).
