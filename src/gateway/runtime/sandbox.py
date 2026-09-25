"""Chạy test của bài nộp trong container Docker dùng một lần.

Container không có mạng, không chạy bằng root, 1 CPU, 512 MB RAM, 128 tiến trình, hệ thống file
chỉ đọc (repo mount ``:ro``, chỉ ``/tmp`` ghi được) và bị dừng sau ``timeout`` giây.
Khi ``REVIEW_SANDBOX=off`` hoặc máy không có Docker thì không chạy gì và trả lý do "không chạy test".
"""
from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

MAX_RESULTS = 500
MAX_MESSAGE = 1500
MAX_NAME = 300
# Dòng tóm tắt của pytest -rA: "PASSED tests/test_a.py::test_x" / "FAILED ... - AssertionError: ..."
_SUMMARY = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)\s+(\S+)(?:\s+-\s+(.*))?$")


@dataclass
class SandboxRun:
    ran: bool
    results: list[dict] = field(default_factory=list)
    note: str = ""  # lý do không chạy / lỗi chạy test, hiển thị cho giáo viên và học viên
    exit_code: int | None = None
    output: str = ""


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_command(workdir: Path, command: str, image: str, name: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--user", "1000:1000",
        "--cpus", "1",
        "--memory", "512m", "--memory-swap", "512m",
        "--pids-limit", "128",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/work",
        "-v", f"{Path(workdir).resolve()}:/work:ro",
        "-w", "/work",
        image, "sh", "-c", command,
    ]


def parse_pytest(output: str, exit_code: int | None) -> list[dict]:
    results: list[dict] = []
    for line in output.splitlines():
        match = _SUMMARY.match(line.strip())
        if not match:
            continue
        status, name, message = match.groups()
        if status in ("SKIPPED", "XFAIL"):
            continue
        results.append({
            "name": name[:MAX_NAME],
            "passed": status in ("PASSED", "XPASS"),
            "message": (message or ("" if status == "PASSED" else status))[:MAX_MESSAGE],
        })
        if len(results) >= MAX_RESULTS:
            break
    if not results and exit_code is not None and exit_code != 5:
        # Không đọc được từng test: ghi một dòng cho cả lệnh test.
        results.append({
            "name": "Lệnh test",
            "passed": exit_code == 0,
            "message": output.strip()[-MAX_MESSAGE:],
        })
    return results


def run_tests(workdir: Path, command: str, *, mode: str = "off", image: str = "python:3.12-slim",
              timeout: int = 60, runner=subprocess.run) -> SandboxRun:
    if (mode or "off").lower() != "docker":
        return SandboxRun(False, note="Không chạy test: sandbox đang tắt trên Gateway.")
    if runner is subprocess.run and not docker_available():
        return SandboxRun(False, note="Không chạy test: máy chủ Gateway không có Docker.")
    name = "lms-review-" + uuid.uuid4().hex[:12]
    argv = docker_command(workdir, command, image, name)
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                      errors="replace")
    except subprocess.TimeoutExpired:
        _force_remove(name, runner)
        return SandboxRun(True, [{"name": "Lệnh test", "passed": False,
                               "message": f"Quá thời gian {timeout} giây."}],
                       note=f"Test chạy quá {timeout} giây nên bị dừng.")
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return SandboxRun(False, note=f"Không chạy test: không gọi được Docker ({exc.__class__.__name__}).")
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode in (125, 126, 127) and not _SUMMARY.search(output):
        # 125: lỗi của chính docker run (image thiếu, daemon tắt...), không phải lỗi bài làm.
        return SandboxRun(False, note="Không chạy test: sandbox lỗi khi khởi động container.",
                       exit_code=proc.returncode, output=output[-4000:])
    results = parse_pytest(output, proc.returncode)
    note = "Không tìm thấy test nào để chạy." if proc.returncode == 5 and not results else ""
    return SandboxRun(True, results, note=note, exit_code=proc.returncode, output=output[-4000:])


def _force_remove(name: str, runner) -> None:
    try:
        runner(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=20)
    except Exception:
        pass
