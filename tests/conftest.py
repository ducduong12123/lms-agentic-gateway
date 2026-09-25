"""Keep tests away from the live data/ directory.

The running engine container mounts data/ and writes data/gateway.db. Tests used to open
the same file from Windows through /mnt/d while the container wrote to it, which
corrupted it (2026-09-25). Every module-level store is pointed at a temporary directory
before any test module imports the server; tests that patch a path themselves still win.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.api import webhook  # noqa: E402
from gateway.runtime import (  # noqa: E402
    actions,
    approval,
    learner,
    long_memory,
    review_jobs,
    trace,
    tutor,
    write_plans,
)

_TMP = Path(tempfile.mkdtemp(prefix="gateway-tests-"))
_DB = _TMP / "gateway.db"
for module in (actions, approval, learner, long_memory, review_jobs, tutor, write_plans):
    module.DB = _DB
webhook.LOG = _TMP / "webhooks.log"
trace.TRACE = _TMP / "memory_trace.jsonl"
