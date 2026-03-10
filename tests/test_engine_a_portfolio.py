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


def test_micro_contract_sizes_allow_live_like_engine_a_exposure():
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
        contract_sizes={"ES": 26_000.0, "NQ": 36_400.0},
    )

    assert positions["ES"].contracts > 0


def test_live_like_contract_sizes_need_larger_engine_a_capital_base():
    constructor = PortfolioConstructor(target_vol=0.12, max_leverage=4.0)
    correlations = {
        instrument: {
            other: (1.0 if instrument == other else 0.4)
            for other in [
                "CL",
                "GC",
                "HG",
                "NG",
                "RTY",
                "YM",
                "ES",
                "NQ",
                "6B",
                "6E",
                "6J",
                "SI",
                "ZC",
                "ZS",
                "ZW",
                "ZN",
                "ZF",
                "ZB",
            ]
        }
        for instrument in [
            "CL",
            "GC",
            "HG",
            "NG",
            "RTY",
            "YM",
            "ES",
            "NQ",
            "6B",
            "6E",
            "6J",
            "SI",
            "ZC",
            "ZS",
            "ZW",
            "ZN",
            "ZF",
            "ZB",
        ]
    }
    common = {
        "forecasts": {
            "CL": 0.485716,
            "GC": 0.41313,
            "HG": 0.408611,
            "NG": -0.22,
            "RTY": 0.316329,
            "YM": 0.18,
            "ES": 0.15,
            "NQ": 0.384547,
            "6B": 0.12,
            "6E": 0.10,
            "6J": -0.08,
            "SI": 0.09,
            "ZC": 0.07,
            "ZS": -0.06,
            "ZW": 0.05,
            "ZN": 0.11,
            "ZF": 0.09,
            "ZB": 0.08,
        },
        "vol_estimates": {
            "CL": 0.346656,
            "GC": 0.240676,
            "HG": 0.364133,
            "NG": 0.963222,
            "RTY": 0.227544,
            "YM": 0.20,
            "ES": 0.21,
            "NQ": 0.229031,
            "6B": 0.14,
            "6E": 0.13,
            "6J": 0.12,
            "SI": 0.541118,
            "ZC": 0.20,
            "ZS": 0.22,
            "ZW": 0.241103,
            "ZN": 0.11,
            "ZF": 0.09,
            "ZB": 0.13,
        },
        "correlations": correlations,
        "regime": _regime(0.7),
        "contract_sizes": {
            "CL": 8759.9998,
            "GC": 52089.0,
            "HG": 52089.0,
            "NG": 3042.0,
            "RTY": 11275.0,
            "YM": 21435.0,
            "ES": 33952.5,
            "NQ": 50089.0,
            "6B": 78750.0,
            "6E": 136250.0,
            "6J": 111250.0,
            "SI": 32890.0,
            "ZC": 2250000.0,
            "ZS": 6017500.0,
            "ZW": 2995000.0,
            "ZN": 109562.5,
            "ZF": 107421.875,
            "ZB": 117656.25,
        },
    }

    flat_positions = constructor.construct(capital=100_000.0, **common)
    actionable_positions = constructor.construct(capital=750_000.0, **common)

    assert sum(1 for position in flat_positions.values() if position.contracts) == 0
    assert {instrument for instrument, position in actionable_positions.items() if position.contracts} == {
        "CL",
        "GC",
        "NG",
        "RTY",
        "YM",
        "NQ",
    }
