import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway.tools.verbs_client import validate_route


@pytest.mark.parametrize("route", ["/admin", "javascript:alert(1)", "//evil.com", "../..", "https://evil.com"])
def test_navigation_whitelist_rejects_unsafe_routes(route):
    with pytest.raises(ValueError):
        validate_route(route)


@pytest.mark.parametrize("route", ["/lms", "/lms/courses/PY-101", "/lms/courses/PY-101/learn/1-2", "/lms/batches/B-1"])
def test_navigation_whitelist_accepts_lms_routes(route):
    assert validate_route(route) == route
