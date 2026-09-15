"""Small external scheduler for opt-in daily plans."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from gateway.runtime import features, learner


class ProactiveWorker:
    def __init__(self, frappe, timezone: str = "Asia/Ho_Chi_Minh", hour: int = 7,
                 transcript_retention_days: int = 90):
        self.frappe = frappe
        self.timezone = ZoneInfo(timezone)
        self.hour = max(0, min(23, int(hour)))
        self.transcript_retention_days = max(1, int(transcript_retention_days))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_date = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="proactive-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(60):
            now = datetime.now(self.timezone)
            if now.hour == self.hour and self._last_date != now.date().isoformat():
                self.run_daily(now)

    def run_daily(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(self.timezone)
        plan_date = now.date().isoformat()
        sent, failed = 0, 0
        with learner._conn() as conn:
            rows = conn.execute(
                "SELECT member FROM member_pref WHERE daily_plan_opt_in=1"
            ).fetchall()
        for row in rows:
            member = str(row["member"])
            plan = features.save_daily_plan(member, plan_date)
            if plan.get("notified_at"):
                continue
            try:
                self.frappe.create_document("Notification Log", {
                    "type": "Alert", "for_user": member,
                    "subject": "Kế hoạch học 15 phút hôm nay",
                    "email_content": plan["content"], "link": "/ai/plan",
                })
                with learner._conn() as conn:
                    conn.execute(
                        "UPDATE daily_plan SET notified_at=? WHERE id=?",
                        (time.time(), plan["id"]),
                    )
                sent += 1
            except Exception:
                failed += 1
        self._last_date = plan_date
        features.purge_old_transcripts(self.transcript_retention_days)
        return {"date": plan_date, "sent": sent, "failed": failed}
