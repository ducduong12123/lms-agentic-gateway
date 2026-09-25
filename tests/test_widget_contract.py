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
    assert "Duyệt từng bản xem trước" in source
    assert "Theo kế hoạch" in source
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
    assert "acp-plan-review" in source
    assert "renderPlanReview" in source
    assert "approvePlan" in source
    assert "typed_confirm" in source
    assert "acp-reversibility" in source


def test_widget_renders_closed_tutor_card():
    source = (Path(__file__).resolve().parents[1] / "widget" / "agentic-copilot.js").read_text(
        encoding="utf-8"
    )
    assert "function renderTutor(target, card)" in source
    assert 'card.kind !== "tutor"' in source
    # Link trích dẫn chỉ nhận route bài học của LMS, dựng bằng textContent.
    assert "/^\/lms\/courses\/[^/]+\/learn\/\d+-\d+$/.test(route)" in source
    assert "a.textContent = item.label || item.lesson" in source
    assert 'postCopilot("/copilot/answers/rate"' in source
    assert 'postCopilot("/copilot/escalate"' in source
    assert "Hỏi giáo viên" in source
    assert "renderTutor(box, ev.tutor)" in source
    assert "j.tutor" in source
