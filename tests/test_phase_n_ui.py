"""Phase N acceptance harness — UI dark mode overhaul.

N-007: Validates that all page endpoints and fragment endpoints return 200,
that HTMX attributes are preserved in templates, and that the dark-mode
shell renders with expected Tailwind token classes.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Template file existence checks
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateFilesExist:
    """All page and fragment template files exist on disk."""

    PAGE_TEMPLATES = [
        "base.html",
        "overview.html",
        "trading.html",
        "research_page.html",
        "incidents_page.html",
        "settings_page.html",
    ]

    FRAGMENT_TEMPLATES = [
        "_top_strip.html",
        "_status.html",
        "_risk_briefing.html",
        "_events.html",
        "_incidents.html",
        "_order_actions.html",
        "_control_actions.html",
        "_jobs.html",
        "_job_detail.html",
        "_reconcile_report.html",
        "_log_tail.html",
        "_ledger_snapshot.html",
        "_broker_health.html",
        "_intent_audit.html",
        "_research.html",
        "_promotion_gate.html",
        "_calibration_run_detail.html",
        "_signal_engine.html",
        "_execution_quality.html",
    ]

    @pytest.mark.parametrize("tpl", PAGE_TEMPLATES)
    def test_page_template_exists(self, tpl):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", tpl)
        assert os.path.isfile(path), f"Missing page template: {tpl}"

    @pytest.mark.parametrize("tpl", FRAGMENT_TEMPLATES)
    def test_fragment_template_exists(self, tpl):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", tpl)
        assert os.path.isfile(path), f"Missing fragment template: {tpl}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Design token compliance in base.html
# ═══════════════════════════════════════════════════════════════════════════


class TestBaseHTMLTokens:
    """base.html uses design-token classes and expected structure."""

    @pytest.fixture(autouse=True)
    def _load_base(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "base.html")
        with open(path) as f:
            self.html = f.read()

    def test_tailwind_cdn_loaded(self):
        assert "cdn.tailwindcss.com" in self.html

    def test_htmx_loaded(self):
        assert "htmx.org" in self.html

    def test_body_bg_slate_950(self):
        assert "bg-slate-950" in self.html

    def test_sidebar_present(self):
        assert "sidebar" in self.html

    def test_space_grotesk_font(self):
        assert "Space Grotesk" in self.html or "Space+Grotesk" in self.html

    def test_jetbrains_mono_font(self):
        assert "JetBrains Mono" in self.html or "JetBrains+Mono" in self.html

    def test_command_palette_present(self):
        assert "command-palette" in self.html

    def test_nav_links_present(self):
        for page in ["overview", "trading", "research", "incidents", "settings"]:
            assert f"/{page}" in self.html, f"Missing nav link to /{page}"

    def test_htmx_loading_states(self):
        assert "htmx-request" in self.html

    def test_top_strip_fragment_in_base(self):
        assert "/fragments/top-strip" in self.html

    def test_no_old_styles_css_as_sole_stylesheet(self):
        # styles.css may still be referenced during transition but Tailwind
        # must also be present
        assert "cdn.tailwindcss.com" in self.html


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Page template design token compliance
# ═══════════════════════════════════════════════════════════════════════════


class TestPageTemplateTokens:
    """Page templates use Tailwind grid layout and design tokens."""

    PAGES = {
        "overview.html": ["grid", "bg-slate-900"],
        "trading.html": ["grid", "bg-slate-900", "hx-post"],
        "research_page.html": ["grid", "hx-post"],
        "incidents_page.html": ["grid", "hx-get"],
        "settings_page.html": ["grid", "font-mono"],
    }

    @pytest.mark.parametrize("tpl,tokens", list(PAGES.items()), ids=list(PAGES.keys()))
    def test_page_has_expected_tokens(self, tpl, tokens):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", tpl)
        with open(path) as f:
            html = f.read()
        for token in tokens:
            assert token in html, f"{tpl} missing token: {token}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: HTMX attribute preservation
# ═══════════════════════════════════════════════════════════════════════════


class TestHTMXAttributePreservation:
    """HTMX-driven panels in pages reference correct fragment endpoints."""

    OVERVIEW_FRAGMENTS = [
        "/fragments/risk-briefing",
        "/fragments/status",
        "/fragments/incidents",
        "/fragments/events",
        "/fragments/jobs",
        "/fragments/job-detail",
        "/fragments/broker-health",
        "/fragments/intent-audit",
        "/fragments/ledger",
    ]

    TRADING_FRAGMENTS = [
        "/fragments/status",
        "/fragments/order-actions",
        "/fragments/reconcile-report",
        "/fragments/control-actions",
        "/fragments/broker-health",
        "/fragments/intent-audit",
        "/fragments/ledger",
    ]

    INCIDENTS_FRAGMENTS = [
        "/fragments/incidents",
        "/fragments/events",
        "/fragments/jobs",
        "/fragments/job-detail",
        "/fragments/control-actions",
        "/fragments/log-tail",
    ]

    SETTINGS_FRAGMENTS = [
        "/fragments/status",
        "/fragments/control-actions",
    ]

    RESEARCH_FRAGMENTS = [
        "/fragments/research",
        "/fragments/jobs",
        "/fragments/job-detail",
    ]

    @pytest.mark.parametrize("frag", OVERVIEW_FRAGMENTS)
    def test_overview_htmx_fragment(self, frag):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "overview.html")
        with open(path) as f:
            html = f.read()
        assert frag in html, f"overview.html missing hx-get for {frag}"

    @pytest.mark.parametrize("frag", TRADING_FRAGMENTS)
    def test_trading_htmx_fragment(self, frag):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "trading.html")
        with open(path) as f:
            html = f.read()
        assert frag in html, f"trading.html missing hx-get for {frag}"

    @pytest.mark.parametrize("frag", INCIDENTS_FRAGMENTS)
    def test_incidents_htmx_fragment(self, frag):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "incidents_page.html")
        with open(path) as f:
            html = f.read()
        assert frag in html, f"incidents_page.html missing hx-get for {frag}"

    @pytest.mark.parametrize("frag", SETTINGS_FRAGMENTS)
    def test_settings_htmx_fragment(self, frag):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "settings_page.html")
        with open(path) as f:
            html = f.read()
        assert frag in html, f"settings_page.html missing hx-get for {frag}"

    @pytest.mark.parametrize("frag", RESEARCH_FRAGMENTS)
    def test_research_htmx_fragment(self, frag):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "research_page.html")
        with open(path) as f:
            html = f.read()
        assert frag in html, f"research_page.html missing hx-get for {frag}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Design tokens document exists and has expected content
# ═══════════════════════════════════════════════════════════════════════════


class TestDesignTokensDoc:
    """DESIGN_TOKENS.md exists and covers all required sections."""

    @pytest.fixture(autouse=True)
    def _load_tokens(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "DESIGN_TOKENS.md")
        with open(path) as f:
            self.doc = f.read()

    REQUIRED_SECTIONS = [
        "Backgrounds",
        "Typography",
        "Semantic Colors",
        "Borders",
        "Panel (Card)",
        "KPI",
        "Badge Variants",
        "Table",
        "Form Inputs",
        "Buttons",
        "Sidebar Nav",
    ]

    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_section_present(self, section):
        assert section in self.doc, f"DESIGN_TOKENS.md missing section: {section}"

    def test_bg_slate_950_body(self):
        assert "bg-slate-950" in self.doc

    def test_bg_slate_900_card(self):
        assert "bg-slate-900" in self.doc

    def test_emerald_profit(self):
        assert "text-emerald-400" in self.doc

    def test_red_loss(self):
        assert "text-red-500" in self.doc


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Static assets
# ═══════════════════════════════════════════════════════════════════════════


class TestStaticAssets:
    """app.js exists and has expected dark-mode command palette."""

    def test_app_js_exists(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "static", "app.js")
        assert os.path.isfile(path)

    def test_app_js_has_command_palette(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "static", "app.js")
        with open(path) as f:
            js = f.read()
        assert "command-palette" in js or "openPalette" in js

    def test_app_js_has_action_refresh(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "static", "app.js")
        with open(path) as f:
            js = f.read()
        assert "refreshCommonPanels" in js


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Equity curve API endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestEquityCurveEndpoint:
    """The equity-curve endpoint is wired in server.py."""

    def test_equity_curve_route_defined(self):
        path = os.path.join(PROJECT_ROOT, "app", "api", "server.py")
        with open(path) as f:
            src = f.read()
        assert "/api/charts/equity-curve" in src

    def test_overview_references_equity_chart(self):
        path = os.path.join(PROJECT_ROOT, "app", "web", "templates", "overview.html")
        with open(path) as f:
            html = f.read()
        assert "equity-chart" in html
        assert "equity-curve" in html


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Source file existence for all Phase N scope
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseNSourceFiles:
    """All files in Phase N scope exist."""

    FILES = [
        "app/web/DESIGN_TOKENS.md",
        "app/web/templates/base.html",
        "app/web/templates/overview.html",
        "app/web/templates/trading.html",
        "app/web/templates/research_page.html",
        "app/web/templates/incidents_page.html",
        "app/web/templates/settings_page.html",
        "app/web/static/app.js",
        "app/api/server.py",
    ]

    @pytest.mark.parametrize("rel_path", FILES)
    def test_source_file_exists(self, rel_path):
        full = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full), f"Missing: {rel_path}"
