from pathlib import Path

from app.api import server


def test_engine_a_control_routes_are_registered():
    paths = {route.path for route in server.app.routes}

    assert "/api/actions/engine-a-start" in paths
    assert "/api/actions/engine-a-stop" in paths
    assert "/api/actions/engine-b-start" in paths
    assert "/api/actions/engine-b-stop" in paths


def test_pipeline_status_template_mentions_engine_a_and_b_controls():
    template = Path("app/web/templates/_pipeline_status.html").read_text(encoding="utf-8")

    assert "pipeline.engine_a.running" in template
    assert "/api/actions/engine-a-start" in template
    assert "/api/actions/engine-a-stop" in template
    assert "pipeline.engine_b.running" in template
    assert "/api/actions/engine-b-start" in template
    assert "/api/actions/engine-b-stop" in template


def test_research_templates_include_engine_a_diagnostics_fragments():
    research_template = Path("app/web/templates/_research.html").read_text(encoding="utf-8")
    regime_template = Path("app/web/templates/_research_regime_panel.html").read_text(encoding="utf-8")
    heatmap_template = Path("app/web/templates/_research_signal_heatmap.html").read_text(encoding="utf-8")
    targets_template = Path("app/web/templates/_research_portfolio_targets.html").read_text(encoding="utf-8")
    rebalance_template = Path("app/web/templates/_research_rebalance_panel.html").read_text(encoding="utf-8")
    journal_template = Path("app/web/templates/_research_regime_journal.html").read_text(encoding="utf-8")

    assert "/fragments/research/regime-panel" in research_template
    assert "/fragments/research/signal-heatmap" in research_template
    assert "/fragments/research/portfolio-targets" in research_template
    assert "/fragments/research/rebalance-panel" in research_template
    assert "/fragments/research/regime-journal" in research_template
    assert "Regime Panel" in regime_template
    assert "Signal Heatmap" in heatmap_template
    assert "Portfolio Targets" in targets_template
    assert "Where accepted research becomes position intent." in targets_template
    assert "Open rebalance chain" in targets_template
    assert "Rebalance Panel" in rebalance_template
    assert "Latest Engine A proposal waiting for execution or dismissal." in rebalance_template
    assert "Largest Changes Queued" in rebalance_template
    assert "/api/actions/research/execute-rebalance" in rebalance_template
    assert "/api/actions/research/dismiss-rebalance" in rebalance_template
    assert "Regime Journal" in journal_template
