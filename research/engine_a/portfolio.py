"""Vol-targeted risk parity portfolio construction for Engine A."""

from __future__ import annotations

from dataclasses import dataclass

from research.artifacts import RegimeSnapshot


@dataclass(frozen=True)
class TargetPosition:
    instrument: str
    contracts: int
    notional: float
    weight: float
    forecast: float
    vol_contribution: float


class PortfolioConstructor:
    """Volatility-targeted risk parity portfolio construction."""

    def __init__(self, target_vol: float = 0.12, max_leverage: float = 4.0):
        self.target_vol = float(target_vol)
        self.max_leverage = float(max_leverage)

    def construct(
        self,
        forecasts: dict[str, float],
        vol_estimates: dict[str, float],
        correlations: dict[str, dict[str, float]],
        regime: RegimeSnapshot | dict,
        capital: float,
        contract_sizes: dict[str, float],
    ) -> dict[str, TargetPosition]:
        if capital <= 0:
            raise ValueError("capital must be positive")
        regime_model = regime if isinstance(regime, RegimeSnapshot) else RegimeSnapshot.model_validate(regime)
        instruments = [instrument for instrument, forecast in forecasts.items() if abs(float(forecast)) > 0]
        if not instruments:
            return {}

        raw_weights: dict[str, float] = {}
        for instrument in instruments:
            vol = max(1e-6, float(vol_estimates[instrument]))
            forecast = float(forecasts[instrument])
            corr_penalty = self._diversification_penalty(instrument, correlations)
            raw_weights[instrument] = forecast * (1.0 / vol) * corr_penalty

        gross = sum(abs(weight) for weight in raw_weights.values())
        if gross == 0:
            return {}
        normalized = {instrument: weight / gross for instrument, weight in raw_weights.items()}
        portfolio_vol = sum(abs(normalized[instrument]) * float(vol_estimates[instrument]) for instrument in instruments)
        scale = self.target_vol / max(1e-6, portfolio_vol)
        scale *= float(regime_model.sizing_factor)

        scaled = {instrument: normalized[instrument] * scale for instrument in instruments}
        total_leverage = sum(abs(weight) for weight in scaled.values())
        if total_leverage > self.max_leverage:
            lever_scale = self.max_leverage / total_leverage
            scaled = {instrument: weight * lever_scale for instrument, weight in scaled.items()}

        positions: dict[str, TargetPosition] = {}
        for instrument in instruments:
            contract_size = float(contract_sizes[instrument])
            target_notional = capital * scaled[instrument]
            contracts = int(round(target_notional / contract_size))
            notional = contracts * contract_size
            weight = notional / capital
            positions[instrument] = TargetPosition(
                instrument=instrument,
                contracts=contracts,
                notional=round(notional, 6),
                weight=round(weight, 6),
                forecast=round(float(forecasts[instrument]), 6),
                vol_contribution=round(abs(weight) * float(vol_estimates[instrument]), 6),
            )
        return positions

    @staticmethod
    def _diversification_penalty(instrument: str, correlations: dict[str, dict[str, float]]) -> float:
        row = correlations.get(instrument, {})
        peers = [abs(float(value)) for other, value in row.items() if other != instrument]
        if not peers:
            return 1.0
        avg_corr = sum(peers) / len(peers)
        return max(0.5, 1.0 - 0.5 * avg_corr)
