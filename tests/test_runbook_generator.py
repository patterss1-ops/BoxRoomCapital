"""Tests for K-005 operational runbook generator."""

from __future__ import annotations

from ops.runbook_generator import (
    ChecklistItem,
    Runbook,
    RunbookGenerator,
)


class TestChecklistItem:
    def test_to_dict(self):
        item = ChecklistItem(step=1, action="Test action", subsystem="broker")
        d = item.to_dict()
        assert d["step"] == 1
        assert d["subsystem"] == "broker"
        assert d["automated"] is False

    def test_defaults(self):
        item = ChecklistItem(step=1, action="x", subsystem="y")
        assert item.severity == "info"
        assert item.automated is False
        assert item.notes == ""


class TestRunbook:
    def test_to_dict(self):
        rb = Runbook(
            title="Test", phase="pre_market", generated_at="now",
            items=[ChecklistItem(step=1, action="check", subsystem="broker")],
        )
        d = rb.to_dict()
        assert d["title"] == "Test"
        assert d["total_items"] == 1

    def test_to_text(self):
        rb = Runbook(
            title="Pre-Market", phase="pre_market", generated_at="2026-03-03",
            items=[
                ChecklistItem(step=1, action="Check feeds", subsystem="data", automated=True),
                ChecklistItem(step=2, action="Review risk", subsystem="risk", severity="warning"),
            ],
        )
        text = rb.to_text()
        assert "Pre-Market" in text
        assert "[AUTO]" in text
        assert "[WARNING]" in text


class TestRunbookGenerator:
    def test_pre_market(self):
        gen = RunbookGenerator()
        rb = gen.generate_pre_market()
        assert rb.phase == "pre_market"
        assert len(rb.items) > 0
        assert any("data" in i.subsystem for i in rb.items)
        assert any("broker" in i.subsystem for i in rb.items)
        assert any("risk" in i.subsystem for i in rb.items)

    def test_pre_market_custom_config(self):
        gen = RunbookGenerator(
            strategies=["gtaa", "dual_momentum", "ibs"],
            brokers=["ig", "ibkr"],
            data_providers=["yfinance", "iqfeed"],
        )
        rb = gen.generate_pre_market()
        assert len(rb.items) > 5  # More items with more subsystems
        assert rb.context["strategies"] == ["gtaa", "dual_momentum", "ibs"]

    def test_post_market(self):
        gen = RunbookGenerator()
        rb = gen.generate_post_market()
        assert rb.phase == "post_market"
        assert len(rb.items) > 0
        assert any("reconciliation" in i.subsystem for i in rb.items)
        assert any("fund" in i.subsystem for i in rb.items)

    def test_incident_generic(self):
        gen = RunbookGenerator()
        rb = gen.generate_incident("generic")
        assert rb.phase == "on_demand"
        assert any("critical" in i.severity for i in rb.items)

    def test_incident_broker_disconnect(self):
        gen = RunbookGenerator()
        rb = gen.generate_incident("broker_disconnect")
        assert rb.context["incident_type"] == "broker_disconnect"
        assert any("circuit breaker" in i.action.lower() for i in rb.items)

    def test_incident_data_stale(self):
        gen = RunbookGenerator()
        rb = gen.generate_incident("data_stale")
        assert any("backup" in i.action.lower() for i in rb.items)

    def test_steps_sequential(self):
        gen = RunbookGenerator()
        rb = gen.generate_pre_market()
        steps = [i.step for i in rb.items]
        assert steps == list(range(1, len(steps) + 1))

    def test_automated_items_present(self):
        gen = RunbookGenerator()
        rb = gen.generate_pre_market()
        auto_count = sum(1 for i in rb.items if i.automated)
        assert auto_count > 0

    def test_json_serialisable(self):
        import json
        gen = RunbookGenerator()
        rb = gen.generate_pre_market()
        j = json.dumps(rb.to_dict())
        parsed = json.loads(j)
        assert parsed["phase"] == "pre_market"
