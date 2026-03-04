"""Risk attribution engine.

J-005: Factor-based risk attribution decomposing portfolio returns
into market, strategy-specific, and idiosyncratic components.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np


@dataclass
class FactorExposure:
    """Exposure of a strategy to a single factor."""

    factor: str
    beta: float  # Regression coefficient
    r_squared: float  # Explanatory power (0..1)
    contribution_pct: float  # % of total variance explained


@dataclass
class AttributionResult:
    """Risk attribution decomposition for one strategy."""

    strategy: str
    total_return_pct: float
    total_volatility_pct: float
    factor_exposures: list[FactorExposure]
    systematic_return_pct: float  # Explained by factors
    idiosyncratic_return_pct: float  # Alpha / unexplained
    systematic_risk_pct: float  # Systematic volatility fraction
    idiosyncratic_risk_pct: float  # Idiosyncratic volatility fraction
    r_squared_total: float  # Total model R²

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_return_pct": round(self.total_return_pct, 4),
            "total_volatility_pct": round(self.total_volatility_pct, 4),
            "systematic_return_pct": round(self.systematic_return_pct, 4),
            "idiosyncratic_return_pct": round(self.idiosyncratic_return_pct, 4),
            "systematic_risk_pct": round(self.systematic_risk_pct, 4),
            "idiosyncratic_risk_pct": round(self.idiosyncratic_risk_pct, 4),
            "r_squared_total": round(self.r_squared_total, 4),
            "factor_exposures": [
                {
                    "factor": f.factor,
                    "beta": round(f.beta, 4),
                    "r_squared": round(f.r_squared, 4),
                    "contribution_pct": round(f.contribution_pct, 4),
                }
                for f in self.factor_exposures
            ],
        }


@dataclass
class PortfolioAttribution:
    """Attribution across all strategies in a portfolio."""

    strategy_attributions: list[AttributionResult]
    portfolio_systematic_pct: float  # Aggregate systematic risk
    portfolio_idiosyncratic_pct: float  # Aggregate idiosyncratic
    dominant_factor: str  # Factor with highest total contribution

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_systematic_pct": round(self.portfolio_systematic_pct, 4),
            "portfolio_idiosyncratic_pct": round(self.portfolio_idiosyncratic_pct, 4),
            "dominant_factor": self.dominant_factor,
            "strategies": [a.to_dict() for a in self.strategy_attributions],
        }


def attribute_returns(
    strategy_returns: Sequence[float],
    factor_returns: dict[str, Sequence[float]],
    strategy_name: str = "strategy",
) -> AttributionResult:
    """Decompose strategy returns into factor contributions + alpha.

    Uses OLS regression: R_strategy = alpha + sum(beta_i * R_factor_i) + epsilon

    Args:
        strategy_returns: The strategy's return series.
        factor_returns: Dict of {factor_name: return_series}. All same length.
        strategy_name: Label for the strategy.
    """
    y = np.array(strategy_returns, dtype=float)
    n = len(y)

    if n < 5 or not factor_returns:
        return _empty_attribution(strategy_name, y)

    # Build factor matrix
    factor_names = list(factor_returns.keys())
    X_cols = []
    for fname in factor_names:
        f = np.array(factor_returns[fname], dtype=float)[:n]
        X_cols.append(f)

    X = np.column_stack(X_cols)
    # Add intercept (alpha)
    ones = np.ones((n, 1))
    X_full = np.hstack([ones, X])

    # OLS: beta = (X'X)^-1 X'y
    try:
        XtX = X_full.T @ X_full
        Xty = X_full.T @ y
        betas = np.linalg.solve(XtX, Xty)
    except np.linalg.LinAlgError:
        return _empty_attribution(strategy_name, y)

    alpha = float(betas[0])
    factor_betas = betas[1:]

    # Fitted values and residuals
    y_hat = X_full @ betas
    residuals = y - y_hat

    # R² (total)
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2_total = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Per-factor R² (marginal — each factor alone)
    factor_exposures = []
    for i, fname in enumerate(factor_names):
        fi = X_cols[i]
        if len(fi) < n:
            fi = np.pad(fi, (0, n - len(fi)))
        # Single-factor regression
        f_r2 = _single_factor_r2(y, fi)
        total_var = float(np.var(y, ddof=1)) if n > 1 else 0.0
        factor_var = float(factor_betas[i]) ** 2 * float(np.var(fi, ddof=1)) if n > 1 else 0.0
        contribution = (factor_var / total_var * 100.0) if total_var > 0 else 0.0

        factor_exposures.append(FactorExposure(
            factor=fname,
            beta=float(factor_betas[i]),
            r_squared=f_r2,
            contribution_pct=contribution,
        ))

    # Systematic vs idiosyncratic
    total_ret = float(np.sum(y)) * 100.0
    sys_ret = float(np.sum(y_hat - alpha)) * 100.0  # Factor-explained return
    idio_ret = total_ret - sys_ret

    total_vol = float(np.std(y, ddof=1)) * math.sqrt(252) * 100.0 if n > 1 else 0.0
    sys_vol = float(np.std(y_hat - alpha, ddof=1)) * math.sqrt(252) * 100.0 if n > 1 else 0.0
    idio_vol = float(np.std(residuals, ddof=1)) * math.sqrt(252) * 100.0 if n > 1 else 0.0

    sys_risk_share = (sys_vol / total_vol * 100.0) if total_vol > 0 else 0.0
    idio_risk_share = 100.0 - sys_risk_share

    return AttributionResult(
        strategy=strategy_name,
        total_return_pct=total_ret,
        total_volatility_pct=total_vol,
        factor_exposures=factor_exposures,
        systematic_return_pct=sys_ret,
        idiosyncratic_return_pct=idio_ret,
        systematic_risk_pct=sys_risk_share,
        idiosyncratic_risk_pct=idio_risk_share,
        r_squared_total=r2_total,
    )


def attribute_portfolio(
    strategy_returns: dict[str, Sequence[float]],
    factor_returns: dict[str, Sequence[float]],
) -> PortfolioAttribution:
    """Run attribution for each strategy and compute portfolio-level summary."""
    attributions = []
    for name, rets in strategy_returns.items():
        attr = attribute_returns(rets, factor_returns, strategy_name=name)
        attributions.append(attr)

    if not attributions:
        return PortfolioAttribution(
            strategy_attributions=[],
            portfolio_systematic_pct=0.0,
            portfolio_idiosyncratic_pct=0.0,
            dominant_factor="none",
        )

    # Aggregate: average systematic risk share
    sys_pcts = [a.systematic_risk_pct for a in attributions]
    avg_sys = float(np.mean(sys_pcts)) if sys_pcts else 0.0

    # Find dominant factor across all strategies
    factor_total: dict[str, float] = {}
    for a in attributions:
        for fe in a.factor_exposures:
            factor_total[fe.factor] = factor_total.get(fe.factor, 0.0) + fe.contribution_pct

    dominant = max(factor_total, key=factor_total.get) if factor_total else "none"

    return PortfolioAttribution(
        strategy_attributions=attributions,
        portfolio_systematic_pct=avg_sys,
        portfolio_idiosyncratic_pct=100.0 - avg_sys,
        dominant_factor=dominant,
    )


def _single_factor_r2(y: np.ndarray, x: np.ndarray) -> float:
    """R² from single-factor OLS: y = a + b*x + e."""
    n = min(len(y), len(x))
    if n < 3:
        return 0.0
    y_, x_ = y[:n], x[:n]
    X = np.column_stack([np.ones(n), x_])
    try:
        betas = np.linalg.solve(X.T @ X, X.T @ y_)
    except np.linalg.LinAlgError:
        return 0.0
    y_hat = X @ betas
    ss_res = float(np.sum((y_ - y_hat) ** 2))
    ss_tot = float(np.sum((y_ - np.mean(y_)) ** 2))
    return max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _empty_attribution(name: str, y: np.ndarray) -> AttributionResult:
    """Return an attribution with 100% idiosyncratic."""
    import math
    n = len(y)
    total_ret = float(np.sum(y)) * 100.0 if n > 0 else 0.0
    total_vol = float(np.std(y, ddof=1)) * math.sqrt(252) * 100.0 if n > 1 else 0.0
    return AttributionResult(
        strategy=name,
        total_return_pct=total_ret,
        total_volatility_pct=total_vol,
        factor_exposures=[],
        systematic_return_pct=0.0,
        idiosyncratic_return_pct=total_ret,
        systematic_risk_pct=0.0,
        idiosyncratic_risk_pct=100.0,
        r_squared_total=0.0,
    )
