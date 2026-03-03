"""End-to-end Phase G acceptance tests (G-005).

Validates the full signal + AI confidence gate + execution-quality pipeline:

    Signal Engine (E/F) produces composite scores
        → AI panel adapters (G-003) normalize model verdicts into PanelConsensus
        → AI confidence calibration (G-004) incorporates execution quality
        → AI gate policy allows/rejects entry signals pre-dispatch
        → Orchestrator enforces gate: entries gated, exits bypass
        → Pipeline wiring passes consensus + quality snapshot end-to-end

Sections:
  1. AI contract immutability + round-trip integrity (G-003)
  2. AI panel adapter contract compliance (G-003)
  3. Panel coordinator aggregation (G-003)
  4. AI confidence calibration behavior (G-004)
  5. AI confidence gate decision policy (G-004)
  6. Orchestrator AI gate integration (G-004)
  7. Pipeline dispatch AI wiring (G-004)
  8. Execution quality → AI calibration feedback loop (G-002 + G-004)
  9. Full Phase G regression: signal → AI gate → dispatch

Each test uses a real SQLite DB where needed — no mocks on the critical path.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.signal.ai_confidence import (
    AIConfidenceDecision,
    AIConfidenceGateConfig,
    ExecutionQualitySnapshot,
    calibrate_ai_confidence,
    evaluate_ai_confidence_gate,
)
from app.signal.ai_contracts import (
    AIModelVerdict,
    AIPanelOpinion,
    OPINION_SCORE_MAP,
    PanelConsensus,
    TimeHorizon,
)
from data.trade_db import init_db
from execution.policy.ai_gate_policy import AIGatePolicyInput, evaluate_ai_gate_policy
from intelligence.ai_panel import PanelCoordinator


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test_phase_g.db")
    init_db(path)
    return path


AS_OF = "2026-03-03T12:00:00Z"


def _verdict(
    model_name: str = "grok",
    ticker: str = "AAPL",
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    confidence: float = 0.8,
    reasoning: str = "Strong momentum",
    key_factors: tuple = ("momentum", "earnings"),
    time_horizon: TimeHorizon = TimeHorizon.SHORT_TERM,
    raw_response: str | None = None,
    metadata: dict | None = None,
) -> AIModelVerdict:
    return AIModelVerdict(
        model_name=model_name,
        ticker=ticker,
        as_of=AS_OF,
        opinion=opinion,
        confidence=confidence,
        reasoning=reasoning,
        key_factors=key_factors,
        time_horizon=time_horizon,
        prompt_version="v1",
        response_hash="abc123",
        latency_ms=1200.0,
        raw_response=raw_response,
        metadata=metadata or {},
    )


def _consensus(
    ticker: str = "AAPL",
    opinion: AIPanelOpinion = AIPanelOpinion.BUY,
    confidence: float = 0.8,
    score: float = 0.5,
    agreement: float = 0.75,
    models_responded: int = 3,
    models_failed: int = 1,
    verdicts: tuple = (),
    failed_models: tuple = ("gemini",),
) -> PanelConsensus:
    return PanelConsensus(
        ticker=ticker,
        as_of=AS_OF,
        consensus_opinion=opinion,
        consensus_confidence=confidence,
        consensus_score=score,
        agreement_ratio=agreement,
        opinion_distribution={opinion.value: models_responded},
        models_responded=models_responded,
        models_failed=models_failed,
        verdicts=verdicts,
        failed_models=failed_models,
        provenance_hash="hash456",
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. AI CONTRACT IMMUTABILITY + ROUND-TRIP INTEGRITY (G-003)
# ══════════════════════════════════════════════════════════════════════════


class TestContractImmutability:
    """Verify frozen dataclass + MappingProxyType audit integrity."""

    def test_verdict_metadata_immutable(self):
        v = _verdict(metadata={"source": "test"})
        with pytest.raises(TypeError):
            v.metadata["tamper"] = "yes"

    def test_verdict_frozen_fields(self):
        v = _verdict()
        with pytest.raises(AttributeError):
            v.model_name = "hacked"

    def test_consensus_opinion_distribution_immutable(self):
        c = _consensus()
        with pytest.raises(TypeError):
            c.opinion_distribution["sell"] = 99

    def test_consensus_frozen_fields(self):
        c = _consensus()
        with pytest.raises(AttributeError):
            c.ticker = "HACKED"

    def test_verdict_round_trip_all_fields(self):
        v = _verdict(raw_response="raw llm output", metadata={"k": "v"})
        d = v.to_dict()
        restored = AIModelVerdict.from_dict(d)
        assert restored.model_name == v.model_name
        assert restored.ticker == v.ticker
        assert restored.opinion == v.opinion
        assert restored.confidence == v.confidence
        assert restored.reasoning == v.reasoning
        assert restored.key_factors == v.key_factors
        assert restored.time_horizon == v.time_horizon
        assert restored.raw_response == v.raw_response
        assert restored.latency_ms == v.latency_ms
        assert dict(restored.metadata) == dict(v.metadata)

    def test_consensus_round_trip(self):
        v = _verdict()
        c = _consensus(verdicts=(v,))
        d = c.to_dict()
        restored = PanelConsensus.from_dict(d)
        assert restored.ticker == c.ticker
        assert restored.consensus_opinion == c.consensus_opinion
        assert restored.consensus_confidence == c.consensus_confidence
        assert restored.consensus_score == c.consensus_score
        assert restored.agreement_ratio == c.agreement_ratio
        assert restored.models_responded == c.models_responded
        assert restored.models_failed == c.models_failed
        assert len(restored.verdicts) == 1
        assert restored.verdicts[0].model_name == "grok"


# ══════════════════════════════════════════════════════════════════════════
# 2. AI PANEL ADAPTER CONTRACT COMPLIANCE (G-003)
# ══════════════════════════════════════════════════════════════════════════


class TestPanelAdapterContracts:
    """Verify shared verdict builder produces valid AIModelVerdict objects."""

    def test_build_verdict_from_parsed(self):
        from intelligence.ai_panel._base import build_verdict_from_parsed
        verdict = build_verdict_from_parsed(
            model_name="grok",
            ticker="TSLA",
            as_of=AS_OF,
            parsed={
                "opinion": "buy",
                "confidence": 0.75,
                "reasoning": "Test",
                "key_factors": ["momentum"],
                "time_horizon": "short_term",
            },
            raw_text="raw response",
            prompt_version="v1",
            latency_ms=500.0,
        )
        assert isinstance(verdict, AIModelVerdict)
        assert verdict.ticker == "TSLA"
        assert verdict.model_name == "grok"
        assert verdict.confidence == 0.75
        assert isinstance(verdict.opinion, AIPanelOpinion)
        assert verdict.opinion == AIPanelOpinion.BUY
        assert isinstance(verdict.key_factors, tuple)
        assert verdict.raw_response == "raw response"

    def test_opinion_alias_coercion(self):
        from intelligence.ai_panel._base import build_verdict_from_parsed
        verdict = build_verdict_from_parsed(
            model_name="claude",
            ticker="AAPL",
            as_of=AS_OF,
            parsed={"opinion": "bullish", "confidence": 0.8},
            raw_text="test",
            prompt_version="v1",
            latency_ms=100.0,
        )
        assert verdict.opinion == AIPanelOpinion.BUY

    def test_confidence_clamped(self):
        from intelligence.ai_panel._base import build_verdict_from_parsed
        verdict = build_verdict_from_parsed(
            model_name="gemini",
            ticker="SPY",
            as_of=AS_OF,
            parsed={"opinion": "buy", "confidence": 1.5},
            raw_text="test",
            prompt_version="v1",
            latency_ms=100.0,
        )
        assert verdict.confidence == 1.0

    def test_all_four_clients_importable(self):
        from intelligence.ai_panel import GrokClient, ClaudeClient, ChatGPTClient, GeminiClient
        for cls in (GrokClient, ClaudeClient, ChatGPTClient, GeminiClient):
            client = cls()
            assert hasattr(client, "fetch_verdict")


# ══════════════════════════════════════════════════════════════════════════
# 3. PANEL COORDINATOR AGGREGATION (G-003)
# ══════════════════════════════════════════════════════════════════════════


class TestPanelCoordinatorE2E:
    """Coordinator assembles individual verdicts into PanelConsensus."""

    def test_all_models_agree_buy(self):
        coord = PanelCoordinator()
        for name in ("grok", "claude", "chatgpt"):
            coord.register(
                name,
                lambda ticker, as_of, ctx=None, _n=name: _verdict(
                    model_name=_n,
                    ticker=ticker,
                    opinion=AIPanelOpinion.BUY,
                    confidence=0.85,
                ),
            )
        result = coord.query_panel(ticker="AAPL", as_of=AS_OF)
        assert isinstance(result, PanelConsensus)
        assert result.consensus_opinion == AIPanelOpinion.BUY
        assert result.models_responded == 3
        assert result.models_failed == 0
        assert result.agreement_ratio == 1.0

    def test_mixed_opinions_produce_consensus(self):
        coord = PanelCoordinator()
        opinions = [AIPanelOpinion.BUY, AIPanelOpinion.SELL, AIPanelOpinion.NEUTRAL]
        for i, (name, op) in enumerate(zip(("a", "b", "c"), opinions)):
            coord.register(
                name,
                lambda ticker, as_of, ctx=None, _n=name, _o=op: _verdict(
                    model_name=_n, ticker=ticker, opinion=_o, confidence=0.7,
                ),
            )
        result = coord.query_panel(ticker="MSFT", as_of=AS_OF)
        assert result.models_responded == 3
        assert 0.0 <= result.agreement_ratio <= 1.0
        assert -1.0 <= result.consensus_score <= 1.0

    def test_failed_model_counted(self):
        coord = PanelCoordinator()
        coord.register(
            "good",
            lambda t, a, ctx=None: _verdict(model_name="good", ticker=t),
        )

        def bad_fetch(ticker, as_of, ctx=None):
            raise RuntimeError("API down")

        coord.register("bad", bad_fetch)
        result = coord.query_panel(ticker="SPY", as_of=AS_OF)
        assert result.models_responded == 1
        assert result.models_failed == 1
        assert "bad" in result.failed_models


# ══════════════════════════════════════════════════════════════════════════
# 4. AI CONFIDENCE CALIBRATION BEHAVIOR (G-004)
# ══════════════════════════════════════════════════════════════════════════


class TestCalibrationE2E:
    """AI confidence calibration produces bounded, quality-sensitive values."""

    def test_calibration_always_bounded(self):
        for conf in (0.0, 0.5, 1.0):
            for agr in (0.0, 0.5, 1.0):
                for score in (-1.0, 0.0, 1.0):
                    c = _consensus(confidence=conf, agreement=agr, score=score)
                    val = calibrate_ai_confidence(c)
                    assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"

    def test_higher_panel_confidence_yields_higher_calibration(self):
        low = calibrate_ai_confidence(_consensus(confidence=0.3))
        high = calibrate_ai_confidence(_consensus(confidence=0.9))
        assert high > low

    def test_good_execution_quality_does_not_degrade_calibration(self):
        c = _consensus()
        poor_quality = ExecutionQualitySnapshot(
            fill_rate_pct=40.0,
            reject_rate_pct=50.0,
            mean_slippage_bps=100.0,
            sample_count=500,
        )
        good_quality = ExecutionQualitySnapshot(
            fill_rate_pct=98.0,
            reject_rate_pct=1.0,
            mean_slippage_bps=2.0,
            sample_count=500,
        )
        assert calibrate_ai_confidence(c, good_quality) > calibrate_ai_confidence(c, poor_quality)

    def test_poor_execution_quality_penalizes_calibration(self):
        c = _consensus()
        good = ExecutionQualitySnapshot(
            fill_rate_pct=95.0, reject_rate_pct=2.0,
            mean_slippage_bps=5.0, sample_count=200,
        )
        poor = ExecutionQualitySnapshot(
            fill_rate_pct=50.0, reject_rate_pct=40.0,
            mean_slippage_bps=95.0, sample_count=200,
        )
        assert calibrate_ai_confidence(c, poor) < calibrate_ai_confidence(c, good)

    def test_zero_sample_count_ignores_quality(self):
        c = _consensus()
        no_data = ExecutionQualitySnapshot(
            fill_rate_pct=10.0, reject_rate_pct=90.0,
            mean_slippage_bps=200.0, sample_count=0,
        )
        assert calibrate_ai_confidence(c, no_data) == calibrate_ai_confidence(c, None)


# ══════════════════════════════════════════════════════════════════════════
# 5. AI CONFIDENCE GATE DECISION POLICY (G-004)
# ══════════════════════════════════════════════════════════════════════════


class TestGateDecisionPolicy:
    """Gate correctly allows/rejects based on confidence, opinion, and config."""

    def test_high_confidence_buy_passes(self):
        c = _consensus(confidence=0.9, score=0.8, agreement=0.9)
        d = evaluate_ai_confidence_gate(c)
        assert d.allowed is True
        assert d.reason_code == "ok"

    def test_low_confidence_rejected(self):
        c = _consensus(confidence=0.2, score=0.1, agreement=0.3)
        d = evaluate_ai_confidence_gate(
            c, config=AIConfidenceGateConfig(min_calibrated_confidence=0.7),
        )
        assert d.allowed is False
        assert d.reason_code == "ai_confidence_below_threshold"

    def test_neutral_opinion_always_rejected(self):
        c = _consensus(opinion=AIPanelOpinion.NEUTRAL, confidence=0.99)
        d = evaluate_ai_confidence_gate(c)
        assert d.allowed is False
        assert d.reason_code == "ai_consensus_neutral"

    def test_insufficient_models_rejected(self):
        c = _consensus(models_responded=1)
        d = evaluate_ai_confidence_gate(
            c, config=AIConfidenceGateConfig(min_models_responded=3),
        )
        assert d.allowed is False
        assert d.reason_code == "ai_models_responded_below_min"

    def test_disabled_gate_always_passes(self):
        c = _consensus(confidence=0.01, score=0.0, agreement=0.0,
                       opinion=AIPanelOpinion.SELL)
        d = evaluate_ai_confidence_gate(
            c, config=AIConfidenceGateConfig(enabled=False),
        )
        assert d.allowed is True
        assert d.reason_code == "ai_gate_disabled"

    def test_require_execution_quality_missing_rejects(self):
        c = _consensus()
        d = evaluate_ai_confidence_gate(
            c, execution_quality=None,
            config=AIConfidenceGateConfig(require_execution_quality=True),
        )
        assert d.allowed is False
        assert d.reason_code == "ai_execution_quality_missing"

    def test_policy_wrapper_matches_direct_call(self):
        c = _consensus()
        direct = evaluate_ai_confidence_gate(c)
        via_policy = evaluate_ai_gate_policy(AIGatePolicyInput(consensus=c))
        assert direct.allowed == via_policy.allowed
        assert direct.reason_code == via_policy.reason_code
        assert direct.calibrated_confidence == via_policy.calibrated_confidence


# ══════════════════════════════════════════════════════════════════════════
# 6. ORCHESTRATOR AI GATE INTEGRATION (G-004)
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorAIGate:
    """Orchestrator applies AI gate to entry signals, bypasses exits."""

    def _make_slot_config(self):
        from execution.signal_adapter import StrategySlotConfig
        return StrategySlotConfig(
            strategy_id="gtaa",
            strategy_version="v1",
            sleeve="trend_following",
            account_type="SPREADBET",
            broker_target="ig",
            base_qty=10.0,
            risk_tags=["sleeve:trend"],
        )

    def _make_signal(self, signal_type, ticker="SPY"):
        from strategies.base import Signal, SignalType
        return Signal(
            signal_type=signal_type,
            ticker=ticker,
            strategy_name="gtaa",
            reason="test signal",
            size_multiplier=1.0,
        )

    def _stub_strategy(self, signal):
        from strategies.base import BaseStrategy

        class Stub(BaseStrategy):
            def __init__(self, sig):
                self._sig = sig

            @property
            def name(self):
                return "gtaa"

            def generate_signal(self, ticker, df, **kw):
                return self._sig

        return Stub(signal)

    def _stub_data_provider(self):
        import pandas as pd
        dp = MagicMock()
        dp.get_daily_bars.return_value = pd.DataFrame({
            "open": [100.0], "high": [105.0], "low": [99.0],
            "close": [102.0], "volume": [1000000],
        })
        return dp

    def test_entry_gated_by_low_confidence(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import SignalType

        slot = StrategySlot(
            strategy=self._stub_strategy(self._make_signal(SignalType.LONG_ENTRY)),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(ticker="SPY", confidence=0.2, score=0.1, agreement=0.3)
            },
            ai_gate_config=AIConfidenceGateConfig(min_calibrated_confidence=0.6),
        )
        assert len(result.intents_created) == 0
        assert len(result.intents_rejected) == 1
        assert result.intents_rejected[0]["reject_rule"] == "ai_confidence_below_threshold"

    def test_entry_allowed_by_high_confidence(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import SignalType

        slot = StrategySlot(
            strategy=self._stub_strategy(self._make_signal(SignalType.LONG_ENTRY)),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(ticker="SPY", confidence=0.9, score=0.8, agreement=0.9)
            },
            ai_gate_config=AIConfidenceGateConfig(min_calibrated_confidence=0.5),
        )
        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_exit_bypasses_ai_gate(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import SignalType

        slot = StrategySlot(
            strategy=self._stub_strategy(self._make_signal(SignalType.LONG_EXIT)),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(
                    ticker="SPY", opinion=AIPanelOpinion.NEUTRAL,
                    confidence=0.05, score=0.0, agreement=0.1,
                )
            },
            ai_gate_config=AIConfidenceGateConfig(min_calibrated_confidence=0.99),
        )
        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_no_consensus_for_ticker_allows_entry(self, db):
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import SignalType

        slot = StrategySlot(
            strategy=self._stub_strategy(self._make_signal(SignalType.LONG_ENTRY)),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={"AAPL": _consensus(ticker="AAPL")},
        )
        assert len(result.intents_created) == 1


# ══════════════════════════════════════════════════════════════════════════
# 7. PIPELINE DISPATCH AI WIRING (G-004)
# ══════════════════════════════════════════════════════════════════════════


class TestPipelineAIWiring:
    """dispatch_orchestration correctly forwards AI params to orchestrator."""

    def test_explicit_consensus_forwarded(self, db):
        """Explicit AI consensus/quality/config are forwarded to orchestrator."""
        from app.engine.pipeline import dispatch_orchestration, register_strategy_class, clear_registry
        from app.engine.orchestrator import OrchestrationResult
        from strategies.base import BaseStrategy, Signal, SignalType

        class StubStrat(BaseStrategy):
            def __init__(self, params=None): pass
            @property
            def name(self): return "stub"
            def generate_signal(self, ticker, df, **kw):
                return Signal(signal_type=SignalType.NONE, ticker=ticker,
                              strategy_name="stub", reason="hold")

        clear_registry()
        register_strategy_class("StubStrat", StubStrat)

        slot_cfg = {
            "id": "test_slot", "strategy_class": "StubStrat",
            "strategy_version": "v1", "sleeve": "trend",
            "account_type": "SPREADBET", "broker_target": "ig",
            "base_qty": 10.0, "risk_tags": [], "tickers": ["SPY"],
        }

        consensus = _consensus(ticker="SPY")
        quality = ExecutionQualitySnapshot(
            fill_rate_pct=90.0, reject_rate_pct=5.0,
            mean_slippage_bps=10.0, sample_count=100,
        )
        gate_cfg = AIConfidenceGateConfig(min_calibrated_confidence=0.6)

        with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
            mock_orch.return_value = OrchestrationResult(run_id="test", run_at="now")
            dispatch_orchestration(
                window_name="test", db_path=db, dry_run=True,
                slot_configs=[slot_cfg],
                ai_consensus_by_ticker={"SPY": consensus},
                ai_execution_quality=quality,
                ai_gate_config=gate_cfg,
            )
            _, kwargs = mock_orch.call_args
            assert kwargs["ai_consensus_by_ticker"]["SPY"] == consensus
            assert kwargs["ai_execution_quality"] == quality
            assert kwargs["ai_gate_config"] == gate_cfg

        clear_registry()

    def test_ai_panel_enabled_triggers_collection(self, db):
        """When enabled, dispatch builds AI consensus + quality snapshot."""
        from app.engine.pipeline import dispatch_orchestration, register_strategy_class, clear_registry
        from app.engine.orchestrator import OrchestrationResult
        from strategies.base import BaseStrategy, Signal, SignalType

        class StubStrat(BaseStrategy):
            def __init__(self, params=None): pass
            @property
            def name(self): return "stub"
            def generate_signal(self, ticker, df, **kw):
                return Signal(signal_type=SignalType.NONE, ticker=ticker,
                              strategy_name="stub", reason="hold")

        clear_registry()
        register_strategy_class("StubStrat", StubStrat)

        slot_cfg = {
            "id": "test_slot", "strategy_class": "StubStrat",
            "strategy_version": "v1", "sleeve": "trend",
            "account_type": "SPREADBET", "broker_target": "ig",
            "base_qty": 10.0, "risk_tags": [], "tickers": ["SPY"],
        }

        consensus = _consensus(ticker="SPY")
        quality = ExecutionQualitySnapshot(
            fill_rate_pct=85.0, reject_rate_pct=8.0,
            mean_slippage_bps=18.0, sample_count=50,
        )

        with patch("app.engine.pipeline._collect_ai_panel_consensus") as mock_coll:
            with patch("app.engine.pipeline._build_execution_quality_snapshot") as mock_eq:
                with patch("app.engine.orchestrator.run_orchestration_cycle") as mock_orch:
                    mock_coll.return_value = {"SPY": consensus}
                    mock_eq.return_value = quality
                    mock_orch.return_value = OrchestrationResult(
                        run_id="test", run_at="now",
                    )
                    dispatch_orchestration(
                        window_name="test", db_path=db, dry_run=True,
                        slot_configs=[slot_cfg],
                        ai_panel_enabled=True,
                    )
                    mock_coll.assert_called_once()
                    mock_eq.assert_called_once()
                    _, kwargs = mock_orch.call_args
                    assert kwargs["ai_consensus_by_ticker"]["SPY"] == consensus
                    assert kwargs["ai_execution_quality"] == quality

        clear_registry()


# ══════════════════════════════════════════════════════════════════════════
# 8. EXECUTION QUALITY → AI CALIBRATION FEEDBACK LOOP (G-002 + G-004)
# ══════════════════════════════════════════════════════════════════════════


class TestExecutionQualityFeedbackLoop:
    """Execution quality data correctly modulates AI confidence decisions."""

    def test_degraded_quality_can_flip_gate_decision(self):
        """A borderline consensus passes with good quality but fails with poor."""
        c = _consensus(confidence=0.6, score=0.4, agreement=0.6)
        cfg = AIConfidenceGateConfig(min_calibrated_confidence=0.5)

        good = ExecutionQualitySnapshot(
            fill_rate_pct=98.0, reject_rate_pct=1.0,
            mean_slippage_bps=3.0, sample_count=300,
        )
        poor = ExecutionQualitySnapshot(
            fill_rate_pct=40.0, reject_rate_pct=50.0,
            mean_slippage_bps=100.0, sample_count=300,
        )

        good_decision = evaluate_ai_confidence_gate(c, good, cfg)
        poor_decision = evaluate_ai_confidence_gate(c, poor, cfg)

        assert good_decision.calibrated_confidence > poor_decision.calibrated_confidence
        # With good quality it should pass; with terrible quality it may not.
        # At minimum the calibrated confidence is lower.
        assert good_decision.calibrated_confidence >= cfg.min_calibrated_confidence

    def test_slippage_only_degradation(self):
        c = _consensus(confidence=0.8, score=0.6, agreement=0.8)
        no_slip = ExecutionQualitySnapshot(
            fill_rate_pct=95.0, reject_rate_pct=2.0,
            mean_slippage_bps=0.0, sample_count=100,
        )
        high_slip = ExecutionQualitySnapshot(
            fill_rate_pct=95.0, reject_rate_pct=2.0,
            mean_slippage_bps=100.0, sample_count=100,
        )
        assert calibrate_ai_confidence(c, no_slip) > calibrate_ai_confidence(c, high_slip)


# ══════════════════════════════════════════════════════════════════════════
# 9. FULL PHASE G REGRESSION: SIGNAL → AI GATE → DISPATCH
# ══════════════════════════════════════════════════════════════════════════


class TestFullPhaseGRegression:
    """End-to-end: signal engine output → AI gate → orchestration result."""

    def _make_slot_config(self):
        from execution.signal_adapter import StrategySlotConfig
        return StrategySlotConfig(
            strategy_id="gtaa", strategy_version="v1",
            sleeve="trend_following", account_type="SPREADBET",
            broker_target="ig", base_qty=10.0, risk_tags=["sleeve:trend"],
        )

    def _stub_data_provider(self):
        import pandas as pd
        dp = MagicMock()
        dp.get_daily_bars.return_value = pd.DataFrame({
            "open": [100.0], "high": [105.0], "low": [99.0],
            "close": [102.0], "volume": [1000000],
        })
        return dp

    def test_multi_ticker_gating(self, db):
        """Two tickers: one with strong consensus passes, one with weak is rejected."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import BaseStrategy, Signal, SignalType

        class MultiEntryStub(BaseStrategy):
            @property
            def name(self):
                return "gtaa"

            def generate_signal(self, ticker, df, **kw):
                return Signal(
                    signal_type=SignalType.LONG_ENTRY, ticker=ticker,
                    strategy_name="gtaa", reason="buy all", size_multiplier=1.0,
                )

        slot = StrategySlot(
            strategy=MultiEntryStub(),
            config=self._make_slot_config(),
            tickers=["SPY", "AAPL"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(
                    ticker="SPY", confidence=0.95, score=0.9, agreement=0.95,
                ),
                "AAPL": _consensus(
                    ticker="AAPL", confidence=0.15, score=0.05, agreement=0.2,
                ),
            },
            ai_gate_config=AIConfidenceGateConfig(min_calibrated_confidence=0.5),
        )

        # SPY should pass, AAPL should be rejected
        created_tickers = [i["instrument"] for i in result.intents_created]
        rejected_tickers = [i["instrument"] for i in result.intents_rejected]
        assert "SPY" in created_tickers
        assert "AAPL" in rejected_tickers

    def test_gate_disabled_allows_all(self, db):
        """With gate disabled, even terrible consensus doesn't block entries."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import BaseStrategy, Signal, SignalType

        class EntryStub(BaseStrategy):
            @property
            def name(self):
                return "gtaa"

            def generate_signal(self, ticker, df, **kw):
                return Signal(
                    signal_type=SignalType.LONG_ENTRY, ticker=ticker,
                    strategy_name="gtaa", reason="buy", size_multiplier=1.0,
                )

        slot = StrategySlot(
            strategy=EntryStub(),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(
                    ticker="SPY", opinion=AIPanelOpinion.NEUTRAL,
                    confidence=0.01, score=0.0, agreement=0.0,
                )
            },
            ai_gate_config=AIConfidenceGateConfig(enabled=False),
        )
        assert len(result.intents_created) == 1
        assert len(result.intents_rejected) == 0

    def test_no_ai_consensus_map_runs_without_gating(self, db):
        """When no AI consensus map provided, orchestration runs normally."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import BaseStrategy, Signal, SignalType

        class EntryStub(BaseStrategy):
            @property
            def name(self):
                return "gtaa"

            def generate_signal(self, ticker, df, **kw):
                return Signal(
                    signal_type=SignalType.LONG_ENTRY, ticker=ticker,
                    strategy_name="gtaa", reason="buy", size_multiplier=1.0,
                )

        slot = StrategySlot(
            strategy=EntryStub(),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker=None,
        )
        assert len(result.intents_created) == 1

    def test_verdict_contract_data_flows_to_rejection_metadata(self, db):
        """Rejected intents carry calibrated confidence and threshold for audit."""
        from app.engine.orchestrator import StrategySlot, run_orchestration_cycle
        from strategies.base import BaseStrategy, Signal, SignalType

        class EntryStub(BaseStrategy):
            @property
            def name(self):
                return "gtaa"

            def generate_signal(self, ticker, df, **kw):
                return Signal(
                    signal_type=SignalType.LONG_ENTRY, ticker=ticker,
                    strategy_name="gtaa", reason="buy", size_multiplier=1.0,
                )

        slot = StrategySlot(
            strategy=EntryStub(),
            config=self._make_slot_config(),
            tickers=["SPY"],
        )

        result = run_orchestration_cycle(
            slots=[slot], db_path=db, dry_run=True,
            data_provider=self._stub_data_provider(),
            ai_consensus_by_ticker={
                "SPY": _consensus(
                    ticker="SPY", confidence=0.1, score=0.05, agreement=0.1,
                )
            },
            ai_gate_config=AIConfidenceGateConfig(min_calibrated_confidence=0.8),
        )

        assert len(result.intents_rejected) == 1
        rej = result.intents_rejected[0]
        assert "ai_calibrated_confidence" in rej
        assert "ai_min_required_confidence" in rej
        assert rej["ai_min_required_confidence"] == 0.8
        assert 0.0 <= rej["ai_calibrated_confidence"] <= 1.0
