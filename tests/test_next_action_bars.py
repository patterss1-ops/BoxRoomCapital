"""Tests for the 'What to do next' action bars on Intel and Research tabs."""
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import urlencode

from starlette.requests import Request

from app.api import server


def _route_endpoint(path: str, method: str):
    for route in server.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def _build_get_request(path: str, params: dict[str, str] | None = None):
    query_string = urlencode(params or {}).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": query_string,
    }
    return Request(scope, receive)


# ─── Route registration ──────────────────────────────────────────────────────


def test_intel_next_action_route_registered():
    paths = {route.path for route in server.app.routes}
    assert "/fragments/intel-next-action" in paths


def test_research_next_action_route_registered():
    paths = {route.path for route in server.app.routes}
    assert "/fragments/research-next-action" in paths


# ─── Page templates include the action bar divs ──────────────────────────────


def test_intel_page_includes_next_action_bar():
    template = Path("app/web/templates/intel_council_page.html").read_text(encoding="utf-8")
    assert "/fragments/intel-next-action" in template
    assert 'id="intel-next-action"' in template


def test_research_page_includes_next_action_bar():
    template = Path("app/web/templates/research_page.html").read_text(encoding="utf-8")
    assert "/fragments/research-next-action" in template
    assert 'id="research-next-action"' in template


# ─── Intel action bar states ─────────────────────────────────────────────────


def test_intel_next_action_idle_state():
    """When no jobs, no ideas, no analyses, show idle state."""
    endpoint = _route_endpoint("/fragments/intel-next-action", "GET")

    with patch("app.api.routes.fragments.get_conn") as mock_conn, \
         patch("app.api.routes.fragments.get_trade_ideas", return_value=[]), \
         patch("app.api.routes.fragments.EventStore") as mock_es_cls:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = conn
        mock_es_cls.return_value.list_events.return_value = []

        resp = endpoint(_build_get_request("/fragments/intel-next-action"))
        body = resp.body.decode("utf-8")

    assert "Nothing queued" in body
    assert "Start Here" in body


def test_intel_next_action_running_state():
    """When jobs are active, show running state."""
    endpoint = _route_endpoint("/fragments/intel-next-action", "GET")

    with patch("app.api.routes.fragments.get_conn") as mock_conn, \
         patch("app.api.routes.fragments.get_trade_ideas", return_value=[]), \
         patch("app.api.routes.fragments.EventStore") as mock_es_cls:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [("job1",)]
        conn.close = MagicMock()
        mock_conn.return_value = conn
        mock_es_cls.return_value.list_events.return_value = []

        resp = endpoint(_build_get_request("/fragments/intel-next-action"))
        body = resp.body.decode("utf-8")

    assert "Analysis in progress" in body
    assert "View Progress" in body


def test_intel_next_action_review_state():
    """When ideas in 'idea' stage exist, show review state."""
    endpoint = _route_endpoint("/fragments/intel-next-action", "GET")

    ideas = [{"pipeline_stage": "idea", "ticker": "AAPL", "confidence": 0.8}]

    with patch("app.api.routes.fragments.get_conn") as mock_conn, \
         patch("app.api.routes.fragments.get_trade_ideas", return_value=ideas), \
         patch("app.api.routes.fragments.EventStore") as mock_es_cls:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = conn
        mock_es_cls.return_value.list_events.return_value = []

        resp = endpoint(_build_get_request("/fragments/intel-next-action"))
        body = resp.body.decode("utf-8")

    assert "ready for review" in body
    assert "Review Ideas" in body


def test_intel_next_action_advancing_state():
    """When ideas are in review/backtest/paper, show advancing state."""
    endpoint = _route_endpoint("/fragments/intel-next-action", "GET")

    ideas = [{"pipeline_stage": "review", "ticker": "TSLA", "confidence": 0.7}]

    with patch("app.api.routes.fragments.get_conn") as mock_conn, \
         patch("app.api.routes.fragments.get_trade_ideas", return_value=ideas), \
         patch("app.api.routes.fragments.EventStore") as mock_es_cls:
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = conn
        mock_es_cls.return_value.list_events.return_value = []

        resp = endpoint(_build_get_request("/fragments/intel-next-action"))
        body = resp.body.decode("utf-8")

    assert "advancing through pipeline" in body
    assert "View Pipeline" in body


# ─── Research action bar states ───────────────────────────────────────────────


def test_research_next_action_renders_from_operating_summary():
    """Research action bar uses focus data from operating summary context."""
    endpoint = _route_endpoint("/fragments/research-next-action", "GET")

    mock_ctx = {
        "focus_tone": "warning",
        "focus_title": "Pilot sign-off waiting",
        "focus_detail": "1 chain awaiting pilot approval",
        "focus_anchor": "#research-alerts",
    }

    with patch("app.api.routes.fragments._get_research_operating_summary_context", return_value=mock_ctx):
        resp = endpoint(_build_get_request("/fragments/research-next-action"))
        body = resp.body.decode("utf-8")

    assert "Pilot sign-off waiting" in body
    assert "Review" in body
    assert "switchTab" in body


def test_research_next_action_urgent_state():
    """When focus_tone is urgent, show urgent styling."""
    endpoint = _route_endpoint("/fragments/research-next-action", "GET")

    mock_ctx = {
        "focus_tone": "urgent",
        "focus_title": "Urgent operator queue",
        "focus_detail": "2 reviews need immediate attention",
        "focus_anchor": "#research-alerts",
    }

    with patch("app.api.routes.fragments._get_research_operating_summary_context", return_value=mock_ctx):
        resp = endpoint(_build_get_request("/fragments/research-next-action"))
        body = resp.body.decode("utf-8")

    assert "Urgent:" in body
    assert "Urgent operator queue" in body
    assert "Open Queue" in body


def test_research_next_action_idle_state():
    """When no active research, show idle state with 'Start Here'."""
    endpoint = _route_endpoint("/fragments/research-next-action", "GET")

    mock_ctx = {
        "focus_tone": "idle",
        "focus_title": "No active research",
        "focus_detail": "",
        "focus_anchor": "#research-intake",
    }

    with patch("app.api.routes.fragments._get_research_operating_summary_context", return_value=mock_ctx):
        resp = endpoint(_build_get_request("/fragments/research-next-action"))
        body = resp.body.decode("utf-8")

    assert "No active research" in body
    assert "Start Here" in body


def test_research_next_action_clear_state():
    """When research is flowing, show clear state."""
    endpoint = _route_endpoint("/fragments/research-next-action", "GET")

    mock_ctx = {
        "focus_tone": "clear",
        "focus_title": "Research loop flowing",
        "focus_detail": "3 active chains",
        "focus_anchor": "#research-loop",
    }

    with patch("app.api.routes.fragments._get_research_operating_summary_context", return_value=mock_ctx):
        resp = endpoint(_build_get_request("/fragments/research-next-action"))
        body = resp.body.decode("utf-8")

    assert "Research loop flowing" in body
    assert "View Chains" in body


# ─── Template structure ──────────────────────────────────────────────────────


def test_intel_next_action_template_exists():
    assert Path("app/web/templates/_intel_next_action.html").is_file()


def test_research_next_action_template_exists():
    assert Path("app/web/templates/_research_next_action.html").is_file()


def test_intel_next_action_template_has_state_classes():
    template = Path("app/web/templates/_intel_next_action.html").read_text(encoding="utf-8")
    assert "state ==" in template
    assert "action_label" in template
    assert "message" in template


def test_research_next_action_template_has_state_classes():
    template = Path("app/web/templates/_research_next_action.html").read_text(encoding="utf-8")
    assert "state ==" in template
    assert "title" in template
    assert "switchTab" in template
