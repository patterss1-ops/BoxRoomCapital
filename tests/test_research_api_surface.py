from pathlib import Path

from app.api import server


def test_research_dashboard_routes_are_registered():
    paths = {route.path for route in server.app.routes}

    assert "/fragments/research/pipeline-funnel" in paths
    assert "/fragments/research/active-hypotheses" in paths
    assert "/fragments/research/engine-status" in paths
    assert "/fragments/research/recent-decisions" in paths
    assert "/fragments/research/alerts" in paths
    assert "/fragments/research/artifact-chain" in paths
    assert "/fragments/research/artifact-chain/{chain_id}" in paths
    assert "/fragments/research/operator-output" in paths
    assert "/fragments/research/archive" in paths
    assert "/fragments/research/regime-panel" in paths
    assert "/fragments/research/signal-heatmap" in paths
    assert "/fragments/research/portfolio-targets" in paths
    assert "/fragments/research/rebalance-panel" in paths
    assert "/fragments/research/regime-journal" in paths
    assert "/api/actions/research/review-ack" in paths
    assert "/api/actions/research/engine-b-run" in paths
    assert "/api/actions/research/synthesize" in paths
    assert "/api/actions/research/post-mortem" in paths
    assert "/api/research/artifact-chain/{chain_id}" in paths
    assert "/api/research/artifact/{artifact_id}" in paths


def test_research_template_loads_dashboard_fragments():
    template = Path("app/web/templates/_research.html").read_text(encoding="utf-8")

    assert "/fragments/research/engine-status" in template
    assert "/fragments/research/alerts" in template
    assert "/fragments/research/pipeline-funnel" in template
    assert "/fragments/research/active-hypotheses" in template
    assert "/fragments/research/recent-decisions" in template
    assert "/fragments/research/regime-panel" in template
    assert "/fragments/research/signal-heatmap" in template
    assert "/fragments/research/portfolio-targets" in template
    assert "/fragments/research/rebalance-panel" in template
    assert "/fragments/research/regime-journal" in template


def test_research_page_mentions_manual_engine_b_form():
    template = Path("app/web/templates/research_page.html").read_text(encoding="utf-8")

    assert "/api/actions/research/engine-b-run" in template
    assert "Manual Engine B Intake" in template
    assert "research-artifact-chain-viewer" in template
    assert "/fragments/research/artifact-chain" in template
    assert "research-operator-output" in template
    assert "/fragments/research/operator-output" in template
    assert "research-archive" in template
    assert "/fragments/research/archive" in template


def test_research_fragment_templates_expose_chain_viewer_controls():
    active_template = Path("app/web/templates/_research_active_hypotheses.html").read_text(encoding="utf-8")
    recent_template = Path("app/web/templates/_research_recent_decisions.html").read_text(encoding="utf-8")
    chain_template = Path("app/web/templates/_research_artifact_chain.html").read_text(encoding="utf-8")
    operator_template = Path("app/web/templates/_research_operator_output.html").read_text(encoding="utf-8")
    archive_template = Path("app/web/templates/_research_archive.html").read_text(encoding="utf-8")

    assert "/fragments/research/artifact-chain/{{ row.chain_id }}" in active_template
    assert "#research-artifact-chain-viewer" in active_template
    assert "/fragments/research/artifact-chain/{{ row.chain_id }}" in recent_template
    assert "Research Chain Viewer" in chain_template
    assert "Raw Body" in chain_template
    assert "/api/actions/research/synthesize" in chain_template
    assert "/api/actions/research/post-mortem" in chain_template
    assert "#research-operator-output" in chain_template
    assert "Research Operator Output" in operator_template
    assert "Research Archive" in archive_template
    assert "/fragments/research/archive" in archive_template
    assert "Completed Chains" in archive_template
    assert "Lifecycle" in archive_template
    assert "#research-operator-output" in archive_template
