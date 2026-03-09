from research.artifacts import RegimeSnapshot
from research.engine_a.portfolio import PortfolioConstructor


def _regime(sizing_factor=1.0):
    return RegimeSnapshot.model_validate(
        {
            "as_of": "2026-03-09T00:00:00Z",
            "vol_regime": "normal",
            "trend_regime": "strong_trend",
            "carry_regime": "steep",
            "macro_regime": "risk_on",
            "sizing_factor": sizing_factor,
            "active_overrides": [],
            "indicators": {},
        }
    )


def test_construct_builds_signed_positions():
    constructor = PortfolioConstructor(target_vol=0.12, max_leverage=2.0)

    positions = constructor.construct(
        forecasts={"ES": 0.8, "NQ": -0.5},
        vol_estimates={"ES": 0.18, "NQ": 0.24},
        correlations={
            "ES": {"ES": 1.0, "NQ": 0.7},
            "NQ": {"ES": 0.7, "NQ": 1.0},
        },
        regime=_regime(1.0),
        capital=100_000.0,
        contract_sizes={"ES": 5_000.0, "NQ": 10_000.0},
    )

    assert positions["ES"].contracts > 0
    assert positions["NQ"].contracts < 0


def test_regime_sizing_factor_reduces_exposure():
    constructor = PortfolioConstructor(target_vol=0.12, max_leverage=2.0)
    common = {
        "forecasts": {"ES": 0.8, "NQ": 0.6},
        "vol_estimates": {"ES": 0.18, "NQ": 0.24},
        "correlations": {
            "ES": {"ES": 1.0, "NQ": 0.6},
            "NQ": {"ES": 0.6, "NQ": 1.0},
        },
        "capital": 100_000.0,
        "contract_sizes": {"ES": 5_000.0, "NQ": 10_000.0},
    }

    full = constructor.construct(regime=_regime(1.0), **common)
    reduced = constructor.construct(regime=_regime(0.5), **common)

    assert abs(reduced["ES"].notional) < abs(full["ES"].notional)
    assert abs(reduced["NQ"].notional) < abs(full["NQ"].notional)


def test_max_leverage_cap_is_enforced():
    constructor = PortfolioConstructor(target_vol=0.50, max_leverage=1.0)

    positions = constructor.construct(
        forecasts={"ES": 1.0, "NQ": 1.0, "RTY": 1.0},
        vol_estimates={"ES": 0.12, "NQ": 0.14, "RTY": 0.16},
        correlations={
            "ES": {"ES": 1.0, "NQ": 0.8, "RTY": 0.7},
            "NQ": {"ES": 0.8, "NQ": 1.0, "RTY": 0.75},
            "RTY": {"ES": 0.7, "NQ": 0.75, "RTY": 1.0},
        },
        regime=_regime(1.0),
        capital=100_000.0,
        contract_sizes={"ES": 5_000.0, "NQ": 10_000.0, "RTY": 5_000.0},
    )

    gross_leverage = sum(abs(position.weight) for position in positions.values())

    assert gross_leverage <= 1.0


def test_contract_rounding_uses_contract_size():
    constructor = PortfolioConstructor(target_vol=0.12, max_leverage=2.0)

    positions = constructor.construct(
        forecasts={"GC": 0.4},
        vol_estimates={"GC": 0.20},
        correlations={"GC": {"GC": 1.0}},
        regime=_regime(1.0),
        capital=50_000.0,
        contract_sizes={"GC": 12_500.0},
    )

    assert positions["GC"].notional % 12_500.0 == 0
