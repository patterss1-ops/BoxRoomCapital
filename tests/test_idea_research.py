"""Tests for the automated idea research pipeline.

Covers:
  - DynamicStrategy signal generation from JSON specs
  - Strategy spec validation
  - DB CRUD for research steps
  - Pipeline automation (promote triggers research)
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

import pandas as pd
import numpy as np
import pytest

from strategies.dynamic_strategy import (
    DynamicStrategy,
    validate_strategy_spec,
    INDICATOR_REGISTRY,
    _compute_indicator,
    _evaluate_operator,
    _evaluate_rule,
)
from strategies.base import SignalType


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_bars(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 100 + np.cumsum(rng.randn(n) * 0.5)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    opn = close + rng.uniform(-1, 1, n)
    volume = rng.randint(1_000_000, 10_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def _basic_spec(**overrides) -> dict:
    """Return a minimal valid strategy spec."""
    spec = {
        "name": "Test Strategy",
        "direction": "long",
        "entry_rules": [
            {"indicator": "rsi", "period": 2, "operator": "<", "value": 30},
        ],
        "exit_rules": [
            {"indicator": "rsi", "period": 2, "operator": ">", "value": 70},
            {"type": "max_hold", "bars": 5},
        ],
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def bars():
    return _make_bars()


@pytest.fixture
def db_path():
    """Temporary database for tests."""
    from data.trade_db import init_db
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


# ── Strategy Spec Validation ─────────────────────────────────────────────────

class TestValidateStrategySpec:
    def test_valid_spec(self):
        errors = validate_strategy_spec(_basic_spec())
        assert errors == []

    def test_missing_name(self):
        spec = _basic_spec()
        del spec["name"]
        errors = validate_strategy_spec(spec)
        assert any("name" in e.lower() for e in errors)

    def test_invalid_direction(self):
        errors = validate_strategy_spec(_basic_spec(direction="sideways"))
        assert any("direction" in e.lower() for e in errors)

    def test_no_entry_rules(self):
        errors = validate_strategy_spec(_basic_spec(entry_rules=[]))
        assert any("entry rule" in e.lower() for e in errors)

    def test_no_exit_rules(self):
        errors = validate_strategy_spec(_basic_spec(exit_rules=[]))
        assert any("exit rule" in e.lower() for e in errors)

    def test_unknown_indicator(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "macd_histogram", "operator": ">", "value": 0}
        ])
        errors = validate_strategy_spec(spec)
        assert any("unknown indicator" in e.lower() for e in errors)

    def test_invalid_operator(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "rsi", "period": 2, "operator": "==", "value": 50}
        ])
        errors = validate_strategy_spec(spec)
        assert any("invalid operator" in e.lower() for e in errors)

    def test_period_out_of_range(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "rsi", "period": 1, "operator": "<", "value": 30}
        ])
        errors = validate_strategy_spec(spec)
        assert any("period" in e.lower() for e in errors)

    def test_max_hold_exit_rule(self):
        spec = _basic_spec(exit_rules=[{"type": "max_hold", "bars": 10}])
        errors = validate_strategy_spec(spec)
        assert errors == []

    def test_max_hold_invalid_bars(self):
        spec = _basic_spec(exit_rules=[{"type": "max_hold", "bars": 0}])
        errors = validate_strategy_spec(spec)
        assert any("bars" in e.lower() for e in errors)

    def test_reference_indicator(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "close", "operator": ">", "reference": "ema", "ref_period": 200}
        ])
        errors = validate_strategy_spec(spec)
        assert errors == []

    def test_unknown_reference(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "close", "operator": ">", "reference": "vwap"}
        ])
        errors = validate_strategy_spec(spec)
        assert any("unknown reference" in e.lower() for e in errors)

    def test_vix_filter_valid(self):
        spec = _basic_spec(vix_filter={"enabled": True, "max_level": 35})
        errors = validate_strategy_spec(spec)
        assert errors == []

    def test_stop_loss_valid(self):
        spec = _basic_spec(stop_loss_atr_multiple=2.0)
        errors = validate_strategy_spec(spec)
        assert errors == []

    def test_stop_loss_invalid(self):
        spec = _basic_spec(stop_loss_atr_multiple=-1.0)
        errors = validate_strategy_spec(spec)
        assert any("stop_loss" in e.lower() for e in errors)

    def test_not_a_dict(self):
        errors = validate_strategy_spec("not a dict")
        assert any("dict" in e.lower() for e in errors)

    def test_missing_value_and_reference(self):
        spec = _basic_spec(entry_rules=[
            {"indicator": "rsi", "period": 2, "operator": "<"}
        ])
        errors = validate_strategy_spec(spec)
        assert any("value" in e.lower() or "reference" in e.lower() for e in errors)


# ── DynamicStrategy Signal Generation ────────────────────────────────────────

class TestDynamicStrategy:
    def test_init_valid_spec(self):
        strategy = DynamicStrategy(_basic_spec())
        assert "Test Strategy" in strategy.name

    def test_init_invalid_spec_raises(self):
        with pytest.raises(ValueError):
            DynamicStrategy({"name": "Bad", "direction": "sideways"})

    def test_long_entry_signal(self, bars):
        spec = _basic_spec(entry_rules=[
            {"indicator": "rsi", "period": 2, "operator": "<", "value": 99},  # always true
            {"indicator": "close", "operator": ">", "value": 0},  # always true
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0)
        assert signal.signal_type == SignalType.LONG_ENTRY

    def test_long_exit_max_hold(self, bars):
        spec = _basic_spec(exit_rules=[{"type": "max_hold", "bars": 3}])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 1.0, 4)
        assert signal.signal_type == SignalType.LONG_EXIT

    def test_short_direction(self, bars):
        spec = _basic_spec(direction="short", entry_rules=[
            {"indicator": "rsi", "period": 2, "operator": "<", "value": 99},
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0)
        assert signal.signal_type == SignalType.SHORT_ENTRY

    def test_short_exit(self, bars):
        spec = _basic_spec(direction="short", exit_rules=[
            {"type": "max_hold", "bars": 2}
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, -1.0, 3)
        assert signal.signal_type == SignalType.SHORT_EXIT

    def test_no_signal_when_entry_rules_fail(self, bars):
        spec = _basic_spec(entry_rules=[
            {"indicator": "rsi", "period": 2, "operator": "<", "value": -100},  # never true
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0)
        assert signal.signal_type == SignalType.NONE

    def test_vix_filter_blocks_entry(self, bars):
        spec = _basic_spec(
            vix_filter={"enabled": True, "max_level": 20},
            entry_rules=[{"indicator": "rsi", "period": 2, "operator": "<", "value": 99}],
        )
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0, vix_close=30)
        assert signal.signal_type == SignalType.NONE
        assert "VIX" in signal.reason

    def test_vix_filter_allows_when_below(self, bars):
        spec = _basic_spec(
            vix_filter={"enabled": True, "max_level": 40},
            entry_rules=[{"indicator": "rsi", "period": 2, "operator": "<", "value": 99}],
        )
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0, vix_close=15)
        assert signal.signal_type == SignalType.LONG_ENTRY

    def test_exit_indicator_rule(self, bars):
        spec = _basic_spec(exit_rules=[
            {"indicator": "rsi", "period": 2, "operator": ">", "value": -100},  # always true
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 1.0, 1)
        assert signal.signal_type == SignalType.LONG_EXIT

    def test_holding_returns_none(self, bars):
        spec = _basic_spec(exit_rules=[
            {"indicator": "rsi", "period": 2, "operator": "<", "value": -100},  # never true
            {"type": "max_hold", "bars": 999},
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 1.0, 1)
        assert signal.signal_type == SignalType.NONE
        assert "Holding" in signal.reason

    def test_insufficient_data(self):
        small_bars = _make_bars(50)
        strategy = DynamicStrategy(_basic_spec())
        signal = strategy.generate_signal("SPY", small_bars, 0.0, 0)
        assert signal.signal_type == SignalType.NONE
        assert "Insufficient" in signal.reason

    def test_reference_based_rule(self, bars):
        spec = _basic_spec(entry_rules=[
            {"indicator": "close", "operator": ">", "reference": "sma", "ref_period": 50},
        ])
        strategy = DynamicStrategy(spec)
        signal = strategy.generate_signal("SPY", bars, 0.0, 0)
        # Should produce either entry or none — just verify it doesn't crash
        assert signal.signal_type in (SignalType.LONG_ENTRY, SignalType.NONE)


# ── Indicator Registry ───────────────────────────────────────────────────────

class TestIndicatorRegistry:
    def test_all_indicators_callable(self, bars):
        for name, fn in INDICATOR_REGISTRY.items():
            result = fn(bars, period=20)
            assert isinstance(result, pd.Series), f"{name} did not return Series"
            assert len(result) == len(bars), f"{name} length mismatch"

    def test_compute_indicator(self, bars):
        result = _compute_indicator(bars, "rsi", 14)
        assert isinstance(result, pd.Series)

    def test_evaluate_operator_lt(self, bars):
        a = pd.Series([1, 2, 3])
        b = pd.Series([2, 2, 2])
        result = _evaluate_operator(a, "<", b)
        assert list(result) == [True, False, False]

    def test_evaluate_operator_crosses_above(self):
        a = pd.Series([1, 1, 3, 4])
        b = pd.Series([2, 2, 2, 2])
        result = _evaluate_operator(a, "crosses_above", b)
        assert result.iloc[2] == True
        assert result.iloc[3] == False


# ── DB Research Steps CRUD ───────────────────────────────────────────────────

class TestResearchStepsCRUD:
    def test_create_and_get(self, db_path):
        from data.trade_db import (
            create_research_step,
            get_research_steps,
            create_trade_idea,
        )
        idea_id = str(uuid.uuid4())
        create_trade_idea(
            idea_id, analysis_id="test", ticker="SPY", direction="long",
            db_path=db_path,
        )

        step_id = create_research_step(
            idea_id, "hypothesis", status="running",
            input_json='{"test": true}',
            db_path=db_path,
        )
        assert step_id > 0

        steps = get_research_steps(idea_id, db_path=db_path)
        assert len(steps) == 1
        assert steps[0]["step_name"] == "hypothesis"
        assert steps[0]["status"] == "running"

    def test_update_step(self, db_path):
        from data.trade_db import (
            create_research_step,
            update_research_step,
            get_research_steps,
            create_trade_idea,
        )
        idea_id = str(uuid.uuid4())
        create_trade_idea(
            idea_id, analysis_id="test", ticker="QQQ", direction="short",
            db_path=db_path,
        )

        step_id = create_research_step(idea_id, "evidence", db_path=db_path)
        update_research_step(
            step_id, status="completed",
            output_json='{"data": "gathered"}',
            cost_usd=0.05,
            db_path=db_path,
        )

        steps = get_research_steps(idea_id, db_path=db_path)
        assert steps[0]["status"] == "completed"
        assert steps[0]["cost_usd"] == 0.05

    def test_multiple_steps_ordered(self, db_path):
        from data.trade_db import create_research_step, get_research_steps, create_trade_idea
        idea_id = str(uuid.uuid4())
        create_trade_idea(
            idea_id, analysis_id="test", ticker="IWM", direction="long",
            db_path=db_path,
        )

        create_research_step(idea_id, "hypothesis", db_path=db_path)
        create_research_step(idea_id, "evidence", db_path=db_path)
        create_research_step(idea_id, "critical_review", db_path=db_path)
        create_research_step(idea_id, "strategy_spec", db_path=db_path)

        steps = get_research_steps(idea_id, db_path=db_path)
        assert len(steps) == 4
        assert [s["step_name"] for s in steps] == [
            "hypothesis", "evidence", "critical_review", "strategy_spec"
        ]


# ── New trade_ideas columns ──────────────────────────────────────────────────

class TestNewIdeaColumns:
    def test_research_columns_exist(self, db_path):
        from data.trade_db import create_trade_idea, update_trade_idea, get_trade_idea
        idea_id = str(uuid.uuid4())
        create_trade_idea(
            idea_id, analysis_id="test", ticker="SPY", direction="long",
            db_path=db_path,
        )

        update_trade_idea(
            idea_id, db_path=db_path,
            research_job_id="research_abc123",
            review_score=7.5,
            review_verdict="proceed",
            strategy_spec_json='{"name": "test", "direction": "long"}',
        )

        idea = get_trade_idea(idea_id, db_path=db_path)
        assert idea["research_job_id"] == "research_abc123"
        assert idea["review_score"] == 7.5
        assert idea["review_verdict"] == "proceed"
        assert '"name": "test"' in idea["strategy_spec_json"]


# ── Pipeline Automation ──────────────────────────────────────────────────────

class TestPipelineAutomation:
    def test_review_to_backtest_gate_requires_research(self, db_path):
        """When research is auto, review->backtest requires completed research."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        from data.trade_db import create_trade_idea, update_trade_idea
        import config as cfg

        original = cfg.IDEA_RESEARCH_AUTO
        cfg.IDEA_RESEARCH_AUTO = True
        try:
            mgr = IdeaPipelineManager(db_path=db_path)
            idea_id = str(uuid.uuid4())
            create_trade_idea(
                idea_id, analysis_id="test", ticker="SPY", direction="long",
                conviction="high", thesis="Test thesis", confidence=0.8,
                pipeline_stage="review",
                db_path=db_path,
            )

            gate = mgr.validate_transition(idea_id, "backtest")
            assert not gate.allowed
            assert "RESEARCH_NOT_STARTED" in gate.reasons
        finally:
            cfg.IDEA_RESEARCH_AUTO = original

    def test_review_to_backtest_passes_without_research(self, db_path):
        """When research is disabled, review->backtest works with basic gate."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        from data.trade_db import create_trade_idea
        import config as cfg

        original = cfg.IDEA_RESEARCH_AUTO
        cfg.IDEA_RESEARCH_AUTO = False
        try:
            mgr = IdeaPipelineManager(db_path=db_path)
            idea_id = str(uuid.uuid4())
            create_trade_idea(
                idea_id, analysis_id="test", ticker="SPY", direction="long",
                conviction="high", thesis="Test thesis", confidence=0.8,
                pipeline_stage="review",
                db_path=db_path,
            )

            gate = mgr.validate_transition(idea_id, "backtest")
            assert gate.allowed
        finally:
            cfg.IDEA_RESEARCH_AUTO = original

    def test_promote_to_review_triggers_research(self, db_path, monkeypatch):
        """Promoting idea->review should auto-launch research when enabled."""
        from intelligence.idea_pipeline import IdeaPipelineManager
        from data.trade_db import create_trade_idea, get_trade_idea
        import config as cfg

        original = cfg.IDEA_RESEARCH_AUTO
        cfg.IDEA_RESEARCH_AUTO = True

        # Mock the researcher to avoid actual LLM calls
        launched = []

        class MockResearcher:
            def __init__(self, db_path=None):
                pass
            def run_async(self, idea_id):
                launched.append(idea_id)
                return "mock_job_123"

        monkeypatch.setattr(
            "intelligence.idea_pipeline.IdeaResearcher" if False else None,
            MockResearcher,
        ) if False else None

        # Patch at import level
        import intelligence.idea_research
        original_class = intelligence.idea_research.IdeaResearcher

        intelligence.idea_research.IdeaResearcher = MockResearcher

        try:
            mgr = IdeaPipelineManager(db_path=db_path)
            idea_id = str(uuid.uuid4())
            create_trade_idea(
                idea_id, analysis_id="test", ticker="SPY", direction="long",
                conviction="high", thesis="Test thesis", confidence=0.5,
                pipeline_stage="idea",
                db_path=db_path,
            )

            result = mgr.promote_idea(idea_id, "review", actor="test")
            assert result["success"]
            assert result.get("research_job_id") == "mock_job_123"
            assert idea_id in launched
        finally:
            cfg.IDEA_RESEARCH_AUTO = original
            intelligence.idea_research.IdeaResearcher = original_class


# ── IdeaResearcher unit tests (mocked LLM calls) ────────────────────────────

class TestIdeaResearcher:
    def test_call_model_raises_on_missing_key(self, db_path, monkeypatch):
        from intelligence.idea_research import IdeaResearcher
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        researcher = IdeaResearcher(db_path=db_path)
        with pytest.raises(RuntimeError, match="not set"):
            researcher._call_model("grok", "test prompt", "test_step")

    def test_fetch_macro_snapshot_handles_errors(self, db_path, monkeypatch):
        """Macro snapshot should return partial data, not crash."""
        from intelligence.idea_research import IdeaResearcher

        researcher = IdeaResearcher(db_path=db_path)
        # This may fail in test env (no yfinance data), but shouldn't raise
        result = researcher._fetch_macro_snapshot()
        assert isinstance(result, dict)


# ── Config ───────────────────────────────────────────────────────────────────

class TestResearchConfig:
    def test_config_defaults(self):
        import config as cfg
        assert cfg.IDEA_RESEARCH_AUTO in (True, False)
        assert cfg.IDEA_REVIEW_MIN_SCORE >= 0
        assert cfg.IDEA_DYNAMIC_BT_MIN_SHARPE >= -5
        assert cfg.IDEA_DYNAMIC_BT_MIN_PF >= 0
        assert cfg.IDEA_DYNAMIC_BT_MIN_TRADES >= 1
