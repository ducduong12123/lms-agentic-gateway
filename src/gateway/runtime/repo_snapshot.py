"""Tải repo GitHub công khai theo commit và giải nén an toàn (chỉ stdlib).

- SHA lấy từ GitHub REST (không xác thực), tarball từ codeload.github.com.
- Giới hạn dung lượng tải, số file văn bản, kích thước từng file và tổng dung lượng giải nén.
- Không bao giờ ghi ra ngoài thư mục đích: đường dẫn tuyệt đối hoặc có ``..`` làm hỏng cả job;
  symlink, hardlink, thiết bị bị bỏ qua. Thư mục build/phụ thuộc và file nhị phân cũng bị bỏ qua.
"""
from __future__ import annotations

import io
import json
import posixpath
import re
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

GITHUB_REPO = re.compile(
    r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")
SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", "dist", "build", ".git", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", ".idea", ".vscode", ".next", "target", "coverage",
    ".ruff_cache", "site-packages", "bower_components",
}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
              "uv.lock", "composer.lock", "Cargo.lock"}
BINARY_EXT = {
    "png", "jpg", "jpeg", "gif", "bmp", "ico", "webp", "tif", "tiff", "psd", "pdf", "zip", "gz",
    "tgz", "bz2", "xz", "7z", "rar", "tar", "exe", "dll", "so", "dylib", "bin", "o", "a", "class",
    "jar", "war", "pyc", "pyo", "pyd", "whl", "egg", "mp3", "mp4", "wav", "ogg", "mov", "avi",
    "mkv", "webm", "woff", "woff2", "ttf", "otf", "eot", "sqlite", "sqlite3", "db", "pkl",
    "pickle", "npy", "npz", "h5", "hdf5", "parquet", "onnx", "pt", "pth",
    "docx", "xlsx", "pptx", "doc", "xls", "ppt", "iso", "dmg", "apk",
}
MAX_FILE_BYTES = 256 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
USER_AGENT = "lms-agentic-gateway-review"


class RepoError(RuntimeError):
    """Lỗi có thông điệp tiếng Việt, hiển thị được cho giáo viên."""


@dataclass
class RepoSnapshot:
    owner: str
    repo: str
    sha: str
    files: dict[str, str] = field(default_factory=dict)  # đường dẫn tương đối -> nội dung
    skipped: int = 0
    truncated: bool = False
    root: Path | None = None  # thư mục đã ghi file ra (cho sandbox), nếu có

    def line_count(self, path: str) -> int:
        text = self.files.get(path)
        if text is None:
            return 0
        return len(text.splitlines()) or (1 if text else 0)

    def has_tests(self) -> bool:
        for path in self.files:
            parts = path.split("/")
            name = parts[-1]
            if "tests" in parts[:-1] or "test" in parts[:-1]:
                return True
            if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
                return True
        return False


def parse_repo_url(url: str) -> tuple[str, str]:
    match = GITHUB_REPO.match(str(url or "").strip())
    if not match:
        raise RepoError("Link repo không phải repo GitHub công khai hợp lệ.")
    return match.group(1), match.group(2)


def _http_get(url: str, max_bytes: int, timeout: int = 30, accept: str = "") -> bytes:
    """GET với giới hạn dung lượng; tách riêng để test thay bằng dữ liệu giả."""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            length = res.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise RepoError(f"Repo vượt quá giới hạn {max_bytes // (1024 * 1024)} MB.")
            chunks, total = [], 0
            while True:
                chunk = res.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RepoError(f"Repo vượt quá giới hạn {max_bytes // (1024 * 1024)} MB.")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RepoError("Không tìm thấy repo hoặc commit trên GitHub (repo có thể đang để riêng tư).") from exc
        if exc.code in (403, 429):
            raise RepoError("GitHub đang giới hạn số lần gọi, thử lại sau.") from exc
        raise RepoError(f"GitHub trả lỗi HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RepoError(f"Không kết nối được GitHub: {exc.reason}") from exc


def resolve_head_sha(owner: str, repo: str) -> str:
    raw = _http_get(
        f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD",
        max_bytes=2 * 1024 * 1024,
        accept="application/vnd.github+json",
    )
    try:
        sha = str(json.loads(raw.decode("utf-8")).get("sha") or "").lower()
    except (ValueError, AttributeError) as exc:
        raise RepoError("GitHub trả dữ liệu commit không đọc được.") from exc
    if not SHA.match(sha):
        raise RepoError("Không xác định được commit mới nhất của repo.")
    return sha


def download_tarball(owner: str, repo: str, sha: str, max_bytes: int) -> bytes:
    return _http_get(f"https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}", max_bytes=max_bytes, timeout=60)


def _safe_relpath(name: str) -> str:
    """Bỏ thư mục gốc ``repo-sha/`` của codeload; từ chối đường dẫn thoát ra ngoài."""
    raw = name.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise RepoError(f"Tarball chứa đường dẫn tuyệt đối không an toàn: {name[:200]}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise RepoError(f"Tarball chứa đường dẫn thoát thư mục không an toàn: {name[:200]}")
    rel = "/".join(parts[1:])  # phần tử đầu là thư mục gốc codeload
    return posixpath.normpath(rel) if rel else ""


def _is_skipped(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_FILES:
        return True
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in BINARY_EXT


def _decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def extract(data: bytes, owner: str, repo: str, sha: str, dest: Path | None = None,
            max_files: int = 200) -> RepoSnapshot:
    """Giải nén các file văn bản vào bộ nhớ (và ``dest`` nếu có)."""
    snap = RepoSnapshot(owner, repo, sha, root=dest)
    unpacked = 0
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise RepoError("Tarball của repo bị hỏng hoặc không đúng định dạng.") from exc
    with tar:
        try:
            for member in tar:
                rel = _safe_relpath(member.name)
                if member.issym() or member.islnk():
                    snap.skipped += 1
                    continue
                if not member.isfile() or not rel:
                    if not member.isdir():
                        snap.skipped += 1
                    continue
                unpacked += max(0, member.size)
                if unpacked > MAX_UNPACKED_BYTES:
                    raise RepoError("Repo giải nén ra quá lớn.")
                if _is_skipped(rel) or member.size > MAX_FILE_BYTES:
                    snap.skipped += 1
                    continue
                if len(snap.files) >= max_files:
                    snap.truncated = True
                    snap.skipped += 1
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    snap.skipped += 1
                    continue
                text = _decode_text(handle.read(MAX_FILE_BYTES + 1))
                if text is None:
                    snap.skipped += 1
                    continue
                snap.files[rel] = text
                if dest is not None:
                    target = (dest / rel).resolve()
                    root = dest.resolve()
                    if root != target and root not in target.parents:
                        raise RepoError(f"Đường dẫn không an toàn: {rel[:200]}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8", newline="")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise RepoError("Tarball của repo bị hỏng hoặc không đúng định dạng.") from exc
    return snap


def fetch(repo_url: str, sha: str | None, dest: Path | None, max_bytes: int,
          max_files: int) -> RepoSnapshot:
    owner, repo = parse_repo_url(repo_url)
    sha = str(sha or "").lower()
    if not SHA.match(sha):
        sha = resolve_head_sha(owner, repo)
    data = download_tarball(owner, repo, sha, max_bytes)
    return extract(data, owner, repo, sha, dest=dest, max_files=max_files)


def _sort_key(path: str) -> tuple[int, str]:
    name = path.rsplit("/", 1)[-1].lower()
    if "/" not in path and name.startswith("readme"):
        return (0, path)
    return (2 if "test" in path.lower() else 1, path)


def render_files(snap: RepoSnapshot, budget: int) -> str:
    """Nội dung repo có đánh số dòng, cắt theo tổng ngân sách ký tự."""
    parts: list[str] = []
    used = 0
    omitted: list[str] = []
    for path in sorted(snap.files, key=_sort_key):
        lines = snap.files[path].splitlines()
        header = f"=== FILE: {path} ({len(lines)} dòng) ===\n"
        if used + len(header) + 40 > budget:
            omitted.append(path)
            continue
        body: list[str] = []
        size = len(header)
        shown = 0
        for number, line in enumerate(lines, 1):
            row = f"{number:>4}| {line[:400]}\n"
            if used + size + len(row) > budget:
                break
            body.append(row)
            size += len(row)
            shown += 1
        if shown < len(lines):
            body.append(f"... (cắt bớt, còn {len(lines) - shown} dòng không hiển thị)\n")
        parts.append(header + "".join(body))
        used += size
    if omitted:
        parts.append("=== FILE KHÔNG HIỂN THỊ VÌ HẾT NGÂN SÁCH: " + ", ".join(omitted[:100]) + " ===\n")
    if snap.truncated or snap.skipped:
        parts.append(f"(Đã bỏ qua {snap.skipped} file nhị phân/phụ thuộc/quá lớn"
                     + (", repo có nhiều hơn giới hạn số file" if snap.truncated else "") + ".)\n")
    return "".join(parts)
