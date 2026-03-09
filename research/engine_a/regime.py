"""Deterministic market regime classification for Engine A and shared context."""

from __future__ import annotations

from research.artifacts import RegimeSnapshot


class RegimeClassifier:
    """Classify broad market state from deterministic numeric inputs."""

    def classify(self, as_of: str, market_data: dict) -> RegimeSnapshot:
        vol_regime = self._classify_vol(
            vix=float(market_data["vix"]),
            vix_percentile=float(market_data.get("vix_percentile", 50.0)),
        )
        trend_regime = self._classify_trend(market_data.get("index_data", {}))
        carry_regime = self._classify_carry(market_data.get("yield_data", {}))
        macro_regime = self._classify_macro(
            vol_regime=vol_regime,
            carry_regime=carry_regime,
            macro_inputs=market_data.get("macro_data", {}),
        )
        sizing_factor = self._compute_sizing_factor(
            vol_regime=vol_regime,
            trend_regime=trend_regime,
            carry_regime=carry_regime,
        )
        return RegimeSnapshot(
            as_of=as_of,
            vol_regime=vol_regime,
            trend_regime=trend_regime,
            carry_regime=carry_regime,
            macro_regime=macro_regime,
            sizing_factor=sizing_factor,
            active_overrides=self._derive_overrides(
                vol_regime=vol_regime,
                trend_regime=trend_regime,
                carry_regime=carry_regime,
                macro_regime=macro_regime,
            ),
            indicators={
                "vix": float(market_data["vix"]),
                "vix_percentile": float(market_data.get("vix_percentile", 50.0)),
                "yield_curve_10y_2y_bps": self._yield_curve_spread_bps(market_data.get("yield_data", {})),
                "trend_score": float(market_data.get("index_data", {}).get("trend_score", 0.0)),
                "breadth": float(market_data.get("index_data", {}).get("breadth", 0.0)),
                "reversal_probability": float(market_data.get("index_data", {}).get("reversal_probability", 0.0)),
            },
        )

    def _classify_vol(self, vix: float, vix_percentile: float) -> str:
        if vix > 35 or vix_percentile >= 95:
            return "crisis"
        if vix >= 25 or vix_percentile >= 80:
            return "high"
        if vix >= 15:
            return "normal"
        return "low"

    def _classify_trend(self, index_data: dict) -> str:
        trend_score = float(index_data.get("trend_score", 0.0))
        breadth = float(index_data.get("breadth", 0.5))
        reversal_probability = float(index_data.get("reversal_probability", 0.0))
        if reversal_probability >= 0.7:
            return "reversal"
        if abs(trend_score) >= 1.0 and breadth >= 0.55:
            return "strong_trend"
        return "choppy"

    def _classify_carry(self, yield_data: dict) -> str:
        spread_bps = self._yield_curve_spread_bps(yield_data)
        if spread_bps < 0:
            return "inverted"
        if spread_bps <= 100:
            return "flat"
        return "steep"

    def _classify_macro(self, vol_regime: str, carry_regime: str, macro_inputs: dict) -> str:
        credit_spread_bps = float(macro_inputs.get("credit_spread_bps", 100.0))
        equity_drawdown_pct = float(macro_inputs.get("equity_drawdown_pct", -3.0))
        if (
            vol_regime in {"high", "crisis"}
            or carry_regime == "inverted"
            or credit_spread_bps >= 175
            or equity_drawdown_pct <= -10.0
        ):
            return "risk_off"
        if vol_regime == "normal" or carry_regime == "flat" or credit_spread_bps >= 130:
            return "transition"
        return "risk_on"

    def _compute_sizing_factor(self, vol_regime: str, trend_regime: str, carry_regime: str) -> float:
        score = 1.0
        score -= {
            "low": 0.0,
            "normal": 0.05,
            "high": 0.20,
            "crisis": 0.35,
        }[vol_regime]
        score -= {
            "strong_trend": 0.0,
            "choppy": 0.10,
            "reversal": 0.20,
        }[trend_regime]
        score -= {
            "steep": 0.0,
            "flat": 0.05,
            "inverted": 0.15,
        }[carry_regime]
        if vol_regime in {"high", "crisis"} and carry_regime == "inverted":
            score -= 0.10
        if vol_regime == "crisis" and trend_regime == "reversal":
            score -= 0.05
        return round(min(1.0, max(0.5, score)), 2)

    def _derive_overrides(
        self,
        vol_regime: str,
        trend_regime: str,
        carry_regime: str,
        macro_regime: str,
    ) -> list[str]:
        overrides: list[str] = []
        if vol_regime in {"high", "crisis"}:
            overrides.append("reduce_trend_weight")
        if trend_regime == "strong_trend" and vol_regime != "crisis":
            overrides.append("increase_trend_weight")
        if carry_regime == "inverted":
            overrides.append("reduce_carry_weight")
        if macro_regime == "risk_off":
            overrides.append("de_risk")
        return overrides

    @staticmethod
    def _yield_curve_spread_bps(yield_data: dict) -> float:
        if "spread_bps" in yield_data:
            return float(yield_data["spread_bps"])
        ten_year = float(yield_data.get("ten_year_yield", 0.0))
        two_year = float(yield_data.get("two_year_yield", 0.0))
        return round((ten_year - two_year) * 100, 2)
