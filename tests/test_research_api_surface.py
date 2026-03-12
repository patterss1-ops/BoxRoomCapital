from pathlib import Path
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


def test_research_dashboard_routes_are_registered():
    paths = {route.path for route in server.app.routes}

    assert "/fragments/research/pipeline-funnel" in paths
    assert "/fragments/research/readiness" in paths
    assert "/fragments/research/active-hypotheses" in paths
    assert "/fragments/research/engine-status" in paths
    assert "/fragments/research/recent-decisions" in paths
    assert "/fragments/research/alerts" in paths
    assert "/fragments/research/artifact-chain" in paths
    assert "/fragments/research/artifact-chain/{chain_id}" in paths
    assert "/fragments/research/operator-output" in paths
    assert "/fragments/research/focus-ribbon" in paths
    assert "/fragments/research/archive" in paths
    assert "/fragments/research/operating-summary" in paths
    assert "/fragments/research/regime-panel" in paths
    assert "/fragments/research/signal-heatmap" in paths
    assert "/fragments/research/portfolio-targets" in paths
    assert "/fragments/research/rebalance-panel" in paths
    assert "/fragments/research/regime-journal" in paths
    assert "/api/actions/research/review-ack" in paths
    assert "/api/actions/research/confirm-kill" in paths
    assert "/api/actions/research/override-kill" in paths
    assert "/api/actions/research/execute-rebalance" in paths
    assert "/api/actions/research/dismiss-rebalance" in paths
    assert "/api/actions/research/engine-b-run" in paths
    assert "/api/actions/research/synthesize" in paths
    assert "/api/actions/research/pilot-approve" in paths
    assert "/api/actions/research/pilot-reject" in paths
    assert "/api/actions/research/post-mortem" in paths
    assert "/api/research/artifact-chain/{chain_id}" in paths
    assert "/api/research/artifact/{artifact_id}" in paths


def test_research_template_loads_dashboard_fragments():
    template = Path("app/web/templates/_research.html").read_text(encoding="utf-8")

    assert "/fragments/research/engine-status" in template
    assert "/fragments/research/operating-summary" in template
    assert "/fragments/research/readiness" in template
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
    assert "Submit New Evidence" in template or "Research Operations" in template
    assert "Submit New Evidence" in template or "Research Intake" in template or "Manual Engine B Intake" in template
    assert 'hx-target="#research-operator-output"' in template
    assert "Legacy Labs" in template
    assert "Diagnostics &amp; Feeds" in template
    assert "research-artifact-chain-viewer" in template
    assert "/fragments/research/artifact-chain" in template
    assert "research_selected_chain_id" in template
    assert "research_selected_queue_lane" in template
    assert "research_selected_active_view" in template
    assert "data-initial-research-chain" in template
    assert "research-operator-output" in template
    assert "/fragments/research/operator-output" in template
    assert "research-focus-ribbon" in template
    assert "/fragments/research/focus-ribbon" in template
    assert "research-archive" in template
    assert "/fragments/research/archive" in template
    # UX redesign removed "What changed" changelog panel; workbench id still present
    assert "research-workbench" in template


def test_research_page_deep_link_primes_initial_fragment_targets():
    endpoint = _route_endpoint("/research", "GET")
    response = endpoint(
        _build_get_request(
            "/research",
            {"research_chain": "chain-deep-link", "research_lane": "pilot", "research_view": "stale"},
        )
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert 'data-initial-research-chain="chain-deep-link"' in body
    assert 'hx-get="/fragments/research?queue_lane=pilot&chain_id=chain-deep-link&active_view=stale"' in body
    assert 'hx-get="/fragments/research/focus-ribbon?chain_id=chain-deep-link&queue_lane=pilot&active_view=stale"' in body
    assert 'hx-get="/fragments/research/artifact-chain/chain-deep-link"' in body
    assert 'hx-get="/fragments/research/operator-output?queue_lane=pilot&chain_id=chain-deep-link&active_view=stale"' in body


def test_research_fragment_deep_link_primes_alert_lane():
    endpoint = _route_endpoint("/fragments/research", "GET")
    response = endpoint(
        _build_get_request(
            "/fragments/research",
            {"queue_lane": "rebalance", "chain_id": "chain-deep-link", "active_view": "operator"},
        ),
        queue_lane="rebalance",
        chain_id="chain-deep-link",
        active_view="operator",
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert 'hx-get="/fragments/research/alerts?queue_lane=rebalance&chain_id=chain-deep-link"' in body
    assert 'hx-get="/fragments/research/active-hypotheses?active_view=operator&chain_id=chain-deep-link"' in body


def test_research_fragment_templates_expose_chain_viewer_controls():
    dashboard_template = Path("app/web/templates/_research.html").read_text(encoding="utf-8")
    active_template = Path("app/web/templates/_research_active_hypotheses.html").read_text(encoding="utf-8")
    recent_template = Path("app/web/templates/_research_recent_decisions.html").read_text(encoding="utf-8")
    chain_template = Path("app/web/templates/_research_artifact_chain.html").read_text(encoding="utf-8")
    operator_template = Path("app/web/templates/_research_operator_output.html").read_text(encoding="utf-8")
    focus_template = Path("app/web/templates/_research_focus_ribbon.html").read_text(encoding="utf-8")
    archive_template = Path("app/web/templates/_research_archive.html").read_text(encoding="utf-8")
    alerts_template = Path("app/web/templates/_research_alerts.html").read_text(encoding="utf-8")
    readiness_template = Path("app/web/templates/_research_readiness.html").read_text(encoding="utf-8")
    summary_template = Path("app/web/templates/_research_operating_summary.html").read_text(encoding="utf-8")
    rebalance_template = Path("app/web/templates/_research_rebalance_panel.html").read_text(encoding="utf-8")
    portfolio_targets_template = Path("app/web/templates/_research_portfolio_targets.html").read_text(encoding="utf-8")
    regime_template = Path("app/web/templates/_research_regime_panel.html").read_text(encoding="utf-8")
    signal_template = Path("app/web/templates/_research_signal_heatmap.html").read_text(encoding="utf-8")
    app_js = Path("app/web/static/app.js").read_text(encoding="utf-8")

    assert "Processing Loop" in dashboard_template
    assert "Active Research" in dashboard_template
    assert "Portfolio Expression" in dashboard_template
    assert "research-portfolio-expression" in dashboard_template
    assert "selected_queue_lane" in dashboard_template
    assert "selected_chain_id" in dashboard_template
    assert "selected_active_view" in dashboard_template
    # UX redesign compacted the operating summary; check key structural elements
    assert "Focus Now" in summary_template
    assert "focus_tone" in summary_template
    assert "lane_cards" in summary_template
    assert "setResearchQueueAndActiveView" in summary_template
    assert "data-research-chain-card" in summary_template
    assert "data-selected-chain-badge" in summary_template
    assert "button_label" in summary_template
    assert "card_label" in summary_template
    assert "recommended_card" in summary_template
    assert "active_view" in summary_template
    assert "Current Artifact Snapshot" in chain_template
    assert "Lineage History &amp; Debug" in chain_template
    assert "Latest Saved Artifact" in operator_template
    assert "Additional Updated Artifacts" in operator_template
    assert "History Lens" in archive_template
    assert "next_queue_item" in alerts_template
    assert "syncResearchWorkbench" in app_js
    assert "refreshResearchChainViewer" in app_js
    assert "refreshResearchFocusRibbon" in app_js
    assert "refreshResearchAlerts" in app_js
    assert "refreshResearchOperatorOutput" in app_js
    assert "getResearchQueueLane" in app_js
    assert "getResearchActionVals" in app_js
    assert "payload.active_view" in app_js
    assert "setResearchActiveView" in app_js
    assert "const currentActiveView = getRememberedResearchActiveView();" in app_js
    assert "window.refreshResearchFocusRibbon(getRememberedResearchChain(), { suppressAutoSync: true });" in app_js
    assert "window.refreshResearchOperatorOutput();" in app_js
    assert "setResearchQueueAndActiveView" in app_js
    assert "const currentQueueLane = getRememberedResearchQueueLane();" in app_js
    assert "const laneChanged = !!normalizedLane && normalizedLane !== currentQueueLane;" in app_js
    assert "const viewChanged = !!normalizedView && normalizedView !== currentActiveView;" in app_js
    assert "inferResearchActiveViewFromQueueLane" in app_js
    assert "restoreResearchActiveView" in app_js
    assert "applyResearchActiveView" in app_js
    assert "updateResearchActiveViewSelectionState" in app_js
    assert "openFirstVisibleResearchActiveCard" in app_js
    assert "stepResearchActiveCard" in app_js
    assert "revealSelectedResearchActiveCard" in app_js
    assert "inferResearchActiveViewForCard" in app_js
    assert "inferResearchQueueLaneForCard" in app_js
    assert "data-research-queue-lane" in app_js
    assert "window.refreshResearchChainViewer(chainId, queueLane, activeView)" in app_js
    assert "window.setResearchQueueAndActiveView(queueLane, targetView, false)" in app_js
    assert "window.refreshResearchChainViewer(chainId, rememberedLane, rememberedView)" in app_js
    assert "buildResearchRequestPath('/fragments/research/focus-ribbon'" in app_js
    assert "suppress_auto_sync: suppressAutoSync ? '1' : ''" in app_js
    assert "const changed = window.setResearchQueueAndActiveView(lane, activeView, false);" in app_js
    assert "data-skip-focus-auto-sync" in focus_template
    assert "researchActiveViewLabel" in app_js
    assert "research:active-view" in app_js
    assert "research_view" in app_js
    assert "data-active-view-banner" in app_js
    assert "data-active-view-empty-state" in app_js
    assert "data-active-view-selection-state" in app_js
    assert "data-active-view-selection-warning" in app_js
    assert "data-active-view-reveal-selected" in app_js
    assert "data-active-view-nav-next" in app_js
    assert "No stale chains are on the board." in app_js
    assert "syncResearchQueueWithFocusRibbon" in app_js
    assert "focusCard.getAttribute('data-focus-active-view')" in app_js
    assert "window.setResearchQueueAndActiveView(lane, activeView, false)" in app_js
    assert "syncResearchRequestTargets" in app_js
    assert "prepareResearchInitialTargets" in app_js
    assert "restoreResearchWorkbenchState" in app_js
    assert "research:selected-chain" in app_js
    assert "research_chain" in app_js
    assert "applyResearchSelectedChain" in app_js
    assert "restoreResearchSelectedChain" in app_js
    assert "clearResearchSelection" in app_js
    assert "rememberResearchActiveView('all')" in app_js
    assert "applyResearchActiveView('all')" in app_js
    assert "returnResearchToQueue" in app_js
    assert "rememberResearchQueueLane('all')" in app_js
    assert "setResearchQueueLane" in app_js
    assert "restoreResearchQueueLane" in app_js
    assert "research:queue-lane" in app_js
    assert "research_lane" in app_js
    assert "history.replaceState" in app_js
    assert "buildResearchRequestPath" in app_js
    assert "'#research-panel'" in app_js or "\"#research-panel\"" in app_js
    assert "research-alerts" in app_js
    assert "'#research-alerts'" in app_js or "\"#research-alerts\"" in app_js
    assert "window.refreshResearchAlerts()" in app_js
    assert "data-initial-research-chain" in app_js
    # UX redesign simplified the active hypotheses board
    assert "Active Chains" in active_template
    assert "data-active-view-button" in active_template
    assert "syncResearchWorkbench" in active_template
    assert "data-research-queue-lane" in active_template
    assert "data-active-view-card" in active_template
    assert "data-active-view-section" in active_template
    assert "data-active-view-empty-state" in active_template
    assert "data-active-view-selection-warning" in active_template
    assert "data-active-view-reveal-selected" in active_template
    assert "operator_rows" in active_template
    assert "flow_rows" in active_template
    assert "flow_lanes" in active_template
    assert "selected_chain_id" in active_template
    assert "data-research-chain-card" in active_template
    assert "Needs Action" in active_template
    assert "In Progress" in active_template
    assert "setResearchActiveView" in active_template
    assert "data-selected-chain-badge" in active_template
    assert "next_action" in active_template
    assert "freshness" in active_template
    assert "/fragments/research/artifact-chain/{{ row.chain_id }}" in active_template
    assert "#research-artifact-chain-viewer" in active_template
    assert "/fragments/research/artifact-chain/{{ row.chain_id }}" in recent_template
    assert "syncResearchWorkbench('{{ row.chain_id }}', 'all', 'all')" in recent_template
    assert "Resolved Decisions" in recent_template
    assert "data-research-chain-card" in recent_template
    assert "decided_label" in recent_template
    assert "Research Chain Viewer" in chain_template
    assert "Chain Activity" in chain_template
    assert "Operator posture" in chain_template
    assert "Timeline Navigation" in chain_template
    assert "Queue Alignment" in operator_template
    assert "Board Slice" in operator_template
    assert "selected_active_view" in operator_template
    assert "return_to_queue_label" in operator_template
    assert "return_to_active_view" in operator_template
    assert "queue_follow_up" in operator_template
    assert "Next Up In" in operator_template
    assert "Lane Clear" in operator_template
    assert "setResearchQueueAndActiveView" in operator_template
    assert "current" in chain_template
    assert "artifact.dom_id" in chain_template
    assert "Review Context" in chain_template
    assert "Rebalance Context" in chain_template
    assert "Raw Body" in chain_template
    assert "Next operator move" in chain_template
    assert "/api/actions/research/review-ack" in chain_template
    assert "/api/actions/research/confirm-kill" in chain_template
    assert "/api/actions/research/override-kill" in chain_template
    assert "/api/actions/research/execute-rebalance" in chain_template
    assert "/api/actions/research/dismiss-rebalance" in chain_template
    assert "/api/actions/research/synthesize" in chain_template
    assert "/api/actions/research/pilot-approve" in chain_template
    assert "/api/actions/research/pilot-reject" in chain_template
    assert "/api/actions/research/post-mortem" in chain_template
    assert "#research-operator-output" in chain_template
    assert "Research Operator Output" in operator_template
    assert "Workbench State" in operator_template
    assert "Selected Chain" in operator_template
    assert "Ready Actions" in operator_template
    assert "Lane Focus" in operator_template
    assert "Review Lane" in operator_template
    assert "Pilot Lane" in operator_template
    assert "Rebalance Lane" in operator_template
    assert "Synthesis Lane" in operator_template
    assert "Review Trigger" in operator_template
    assert "Rebalance Proposal" in operator_template
    assert "Engine B Intake Queued" in operator_template
    assert "Refresh Chain" in operator_template
    assert "Suggested Queue Entry" in operator_template
    assert "primary_action_label" in operator_template
    assert "/api/actions/research/review-ack" in operator_template
    assert "/api/actions/research/execute-rebalance" in operator_template
    assert "Current Focus" in focus_template
    assert "Selected Chain" in focus_template
    assert "Recommended Focus" in focus_template
    assert "Load Suggested Chain" in focus_template
    assert "current_queue_label" in focus_template
    assert "current_active_view_label" in focus_template
    assert "Action Readiness" in focus_template
    assert "which operator lanes are open right now" in focus_template
    assert "data-focus-queue-lane" in focus_template
    assert "data-focus-active-view" in focus_template
    assert "data-auto-queue-sync" in focus_template
    assert "Current queue already matches this chain's current lane." in focus_template
    assert "Board Slice" in focus_template
    assert "Current board already matches this chain's working slice." in focus_template
    assert "Clear Focus" in focus_template
    assert "Open Review Workbench" in focus_template
    assert "setResearchQueueAndActiveView" in focus_template
    assert "data-research-chain-card" in focus_template
    assert "syncResearchWorkbench('{{ selected_chain_id }}', '{{ selected_chain_context.queue_filter }}', '{{ selected_chain_context.active_view }}')" in alerts_template
    assert "syncResearchWorkbench('{{ alert.chain_id }}', 'review', 'operator')" in alerts_template
    assert "syncResearchWorkbench('{{ alert.chain_id }}', 'pilot', 'operator')" in alerts_template
    assert "syncResearchWorkbench('{{ alert.chain_id }}', 'rebalance', 'all')" in alerts_template
    assert "syncResearchWorkbench('{{ alert.chain_id }}', 'all', 'all')" in alerts_template
    assert "syncResearchWorkbench('{{ rebalance.chain_id }}', 'rebalance', 'all')" in rebalance_template
    assert "syncResearchWorkbench('{{ chain_id }}', 'rebalance', 'all')" in portfolio_targets_template
    assert "syncResearchWorkbench('{{ chain_id }}', '{{ queue_filter }}', '{{ active_view }}')" in focus_template
    assert "syncResearchWorkbench('{{ recommended_card.chain_id }}', '{{ recommended_card.queue_filter or '' }}', '{{ recommended_card.active_view or 'all' }}')" in summary_template
    assert "syncResearchWorkbench('{{ queue_follow_up.chain_id }}', '{{ queue_follow_up.lane }}', '{{ queue_follow_up.active_view }}')" in operator_template
    assert "syncResearchWorkbench('{{ synthesis.chain_id }}', '{{ selected_queue_lane }}', '{{ selected_active_view }}')" in operator_template
    assert "syncResearchWorkbench('{{ active_chain.chain_id }}', '{{ selected_queue_lane }}', '{{ selected_active_view }}')" in operator_template
    assert "data-selected-chain-badge" in focus_template
    assert "/api/actions/research/pilot-approve" in focus_template
    assert "/api/actions/research/pilot-reject" in focus_template
    assert "/api/actions/research/execute-rebalance" in focus_template
    assert "/api/actions/research/dismiss-rebalance" in focus_template
    assert "/api/actions/research/synthesize" in focus_template
    assert "/api/actions/research/post-mortem" in focus_template
    # UX redesign compacted the alerts/decision queue template
    assert "Decision Queue" in alerts_template
    assert "active_lane" in alerts_template
    assert "selected_chain_id" in alerts_template
    assert "selected_chain_context" in alerts_template
    assert "Review" in alerts_template
    assert "Pilot" in alerts_template
    assert "Rebalance" in alerts_template
    assert "Retirements" in alerts_template
    assert "data-queue-lane-section" in alerts_template
    assert "data-queue-lane-button" in alerts_template
    assert "data-research-chain-card" in alerts_template
    assert "data-selected-chain-badge" in alerts_template
    assert "/api/actions/research/pilot-approve" in alerts_template
    assert "/api/actions/research/pilot-reject" in alerts_template
    assert "/api/actions/research/execute-rebalance" in alerts_template
    assert "/api/actions/research/dismiss-rebalance" in alerts_template
    assert "Research Readiness" in readiness_template
    assert "Operational blockers for real-data validation" in readiness_template
    assert "Loop State" in readiness_template
    assert "Cutover Guidance" in readiness_template
    assert "Research Archive" in archive_template
    assert "Closed Loops" in archive_template
    assert "/fragments/research/archive" in archive_template
    assert "Completed Chains" in archive_template
    assert "Lifecycle" in archive_template
    assert "data-research-chain-card" in archive_template
    assert "data-selected-chain-badge" in archive_template
    assert "syncResearchWorkbench('{{ row.chain_id }}', 'all', 'all')" in archive_template
    assert "#research-operator-output" in archive_template
    assert "Regime Panel" in regime_template
    assert "Open regime chain" in regime_template
    assert "syncResearchWorkbench('{{ regime.chain_id }}', 'rebalance', 'all')" in regime_template
    assert "Signal Heatmap" in signal_template
    assert "Open signal chain" in signal_template
    assert "syncResearchWorkbench('{{ chain_id }}', 'rebalance', 'all')" in signal_template
    assert "/api/actions/research/confirm-kill" in alerts_template
    assert "/api/actions/research/override-kill" in alerts_template
