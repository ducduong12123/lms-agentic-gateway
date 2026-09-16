from pathlib import Path


def test_widget_does_not_claim_identity():
    source = (Path(__file__).resolve().parents[1] / "widget" / "agentic-copilot.js").read_text(
        encoding="utf-8"
    )
    assert "data-role" not in source
    assert "role: roleSel" not in source
    assert "user: user()" not in source
    assert 'GATEWAY + "/identity"' in source
    assert "attachShadow" in source
    assert 'GATEWAY + "/me/mastery' in source
    assert 'page: location.pathname + location.search' in source
    assert "approval_mode: CURRENT_APPROVAL_MODE" in source
    assert '"ask"' in source
    assert '"auto"' in source
    assert '"full_access"' in source
    assert "Hỏi trước khi làm" in source
    assert "Toàn quyền" in source
