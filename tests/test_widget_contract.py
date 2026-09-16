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
    assert ":host{--acp-surface:var(--surface-base" in source
    assert "data-theme" in source
    assert "MutationObserver(syncLmsTheme)" in source
    assert "width:296px" in source
    assert ".acp-composer{background:var(--acp-surface)" in source
    assert "InterVar,ui-sans-serif,system-ui,sans-serif" in source
    assert "var LUCIDE_PATHS" in source
    assert 'svgIcon("chevronDown")' in source
    assert 'svgIcon("chevronRight")' in source
    assert 'svgIcon("shieldCheck", "approval-icon")' in source
    assert 'svgIcon(item.icon, "icon")' in source
    assert "⌄" not in source
    assert 'var CURRENT_EFFORT = "medium"' in source
    assert 'id="acp-effort"' in source
    assert 'id="acp-effort-range"' in source
    assert "acp-effort-labels" in source
    assert "acp-effort-model" not in source
    assert "acp-effort-reset" not in source
    assert "acp-auto" not in source
    assert 'setEffort("auto"' not in source
    assert "overflow-x:hidden" in source
    assert "function syncEffortButton(label)" in source
    assert "syncEffortButton(EFFORT_LEVELS[effortIndex()].label)" in source
    assert 'ev.t === "summary"' in source
    assert 'ev.t === "plan"' in source
    assert 'ev.t === "plan_step"' in source
    assert 'ev.t === "thought"' not in source
    assert "Tóm tắt quá trình thực hiện" in source
    assert 'id="acp-queue"' not in source
    assert "refreshQueue" not in source
    assert "acp-fab-badge" not in source
