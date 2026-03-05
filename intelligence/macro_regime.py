"""Macro regime classifier for position sizing and strategy allocation.

Reads FeatureStore 'macro_regime' data and classifies the current
environment as risk_on, risk_off, or transition.

Rules:
- Inverted yield curve + widening spreads = risk_off
- Normal yield curve + tight spreads = risk_on
- Mixed signals = transition
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from intelligence.feature_store import FeatureStore

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    """Macro regime classification."""

    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    TRANSITION = "transition"


@dataclass(frozen=True)
class MacroRegimeConfig:
    """Thresholds for regime classification."""

    # Yield curve (T10Y2Y): negative = inverted
    yield_curve_inversion_threshold: float = 0.0
    yield_curve_healthy_threshold: float = 0.5

    # High-yield OAS: wider = more stress
    hy_oas_stress_threshold: float = 5.0  # >500bp = stress
    hy_oas_calm_threshold: float = 3.5    # <350bp = calm

    # Fed funds rate thresholds
    fed_rate_restrictive_threshold: float = 4.5  # Above = restrictive

    # Initial claims thresholds (weekly)
    claims_elevated_threshold: float = 300000.0  # >300K = weakness


@dataclass(frozen=True)
class MacroRegimeResult:
    """Result of macro regime classification."""

    regime: Regime
    confidence: float  # 0.0-1.0
    signals: dict  # Individual signal readings
    reason: str


def classify_regime(
    features: dict[str, float],
    config: MacroRegimeConfig = MacroRegimeConfig(),
) -> MacroRegimeResult:
    """Classify macro regime from feature values.

    Args:
        features: Dict with keys from MACRO_SERIES feature names:
            yield_curve_spread, hy_oas_spread, fed_funds_rate, initial_claims
        config: Classification thresholds.

    Returns:
        MacroRegimeResult with regime, confidence, and reasoning.
    """
    signals: dict[str, str] = {}
    risk_on_count = 0
    risk_off_count = 0
    total_signals = 0

    # 1. Yield curve
    yc = features.get("yield_curve_spread")
    if yc is not None:
        total_signals += 1
        if yc < config.yield_curve_inversion_threshold:
            signals["yield_curve"] = f"INVERTED ({yc:.2f})"
            risk_off_count += 1
        elif yc > config.yield_curve_healthy_threshold:
            signals["yield_curve"] = f"HEALTHY ({yc:.2f})"
            risk_on_count += 1
        else:
            signals["yield_curve"] = f"FLAT ({yc:.2f})"

    # 2. High-yield OAS
    hy = features.get("hy_oas_spread")
    if hy is not None:
        total_signals += 1
        if hy > config.hy_oas_stress_threshold:
            signals["hy_oas"] = f"STRESSED ({hy:.2f})"
            risk_off_count += 1
        elif hy < config.hy_oas_calm_threshold:
            signals["hy_oas"] = f"CALM ({hy:.2f})"
            risk_on_count += 1
        else:
            signals["hy_oas"] = f"NORMAL ({hy:.2f})"

    # 3. Fed funds rate
    ff = features.get("fed_funds_rate")
    if ff is not None:
        total_signals += 1
        if ff > config.fed_rate_restrictive_threshold:
            signals["fed_rate"] = f"RESTRICTIVE ({ff:.2f}%)"
            risk_off_count += 1
        else:
            signals["fed_rate"] = f"ACCOMMODATIVE ({ff:.2f}%)"
            risk_on_count += 1

    # 4. Initial claims
    claims = features.get("initial_claims")
    if claims is not None:
        total_signals += 1
        if claims > config.claims_elevated_threshold:
            signals["initial_claims"] = f"ELEVATED ({claims:.0f})"
            risk_off_count += 1
        else:
            signals["initial_claims"] = f"HEALTHY ({claims:.0f})"
            risk_on_count += 1

    # Classify
    if total_signals == 0:
        return MacroRegimeResult(
            regime=Regime.TRANSITION,
            confidence=0.0,
            signals=signals,
            reason="No macro data available",
        )

    risk_off_pct = risk_off_count / total_signals
    risk_on_pct = risk_on_count / total_signals

    if risk_off_pct >= 0.6:
        regime = Regime.RISK_OFF
        confidence = risk_off_pct
        reason = f"{risk_off_count}/{total_signals} signals bearish"
    elif risk_on_pct >= 0.6:
        regime = Regime.RISK_ON
        confidence = risk_on_pct
        reason = f"{risk_on_count}/{total_signals} signals bullish"
    else:
        regime = Regime.TRANSITION
        confidence = 0.5
        reason = f"Mixed: {risk_on_count} bullish, {risk_off_count} bearish of {total_signals}"

    return MacroRegimeResult(
        regime=regime,
        confidence=round(confidence, 2),
        signals=signals,
        reason=reason,
    )


class MacroRegimeClassifier:
    """Reads from FeatureStore and classifies current macro regime."""

    def __init__(
        self,
        feature_store: Optional[FeatureStore] = None,
        config: MacroRegimeConfig = MacroRegimeConfig(),
    ):
        self._fs = feature_store
        self.config = config

    def _get_store(self) -> FeatureStore:
        if self._fs is None:
            self._fs = FeatureStore()
        return self._fs

    def classify(self) -> MacroRegimeResult:
        """Get current macro regime from stored features."""
        try:
            record = self._get_store().get_latest(
                entity_id="MACRO",
                feature_set="macro_regime",
            )
            if record is None:
                return MacroRegimeResult(
                    regime=Regime.TRANSITION,
                    confidence=0.0,
                    signals={},
                    reason="No macro data in FeatureStore",
                )
            return classify_regime(record.features, self.config)
        except Exception as exc:
            logger.warning("Macro regime classification failed: %s", exc)
            return MacroRegimeResult(
                regime=Regime.TRANSITION,
                confidence=0.0,
                signals={},
                reason=f"Error: {exc}",
            )
