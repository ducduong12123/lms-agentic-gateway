# Frappe LMS 2.62.1 Contract

The engine treats Frappe as the system of record and uses only existing HTTP contracts.

| Purpose | Identity | Method | Contract |
|---|---|---|---|
| Resolve current lesson | Browser `sid` | GET | `lms.lms.utils.get_lesson(course, chapter, lesson)` |
| Course outline | Browser `sid` | GET | `lms.lms.utils.get_course_outline(course, progress)` |
| Own progress | Browser `sid` | GET | `lms.lms.utils.get_course_progress(course)` |
| Event reread | Service token | GET | `/api/resource/{doctype}/{name}` |
| Reconciliation | Service token | GET | `/api/resource/{doctype}?filters=[["modified",">",cursor]]` |
| Notification | Service token | POST | `/api/resource/Notification Log` |

The SPA lesson route is `/lms/courses/:courseName/learn/:chapterNumber-:lessonNumber`.
`route_adapter.py` and `widget/route-adapter.js` isolate this dependency.

Run before upgrading LMS:

```powershell
$env:PYTHONPATH='src'
python scripts/contract_check.py
python -m pytest tests/test_route_adapter.py tests/test_storage_events.py -q
```

POSTs issued through a user session are avoided. Existing permission-aware GET methods use the
browser session; aggregate sync and `Notification Log` use the least-privilege service account.
