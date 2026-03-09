from research.engine_a.regime import RegimeClassifier


def _market_data(**overrides):
    data = {
        "vix": 14.0,
        "vix_percentile": 35.0,
        "index_data": {
            "trend_score": 1.4,
            "breadth": 0.64,
            "reversal_probability": 0.1,
        },
        "yield_data": {
            "ten_year_yield": 4.2,
            "two_year_yield": 2.8,
        },
        "macro_data": {
            "credit_spread_bps": 110.0,
            "equity_drawdown_pct": -4.0,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return data


def test_classify_vol_regimes():
    classifier = RegimeClassifier()

    assert classifier._classify_vol(12.0, 20.0) == "low"
    assert classifier._classify_vol(20.0, 50.0) == "normal"
    assert classifier._classify_vol(28.0, 70.0) == "high"
    assert classifier._classify_vol(36.0, 90.0) == "crisis"


def test_classify_carry_regimes():
    classifier = RegimeClassifier()

    assert classifier._classify_carry({"spread_bps": 125.0}) == "steep"
    assert classifier._classify_carry({"spread_bps": 40.0}) == "flat"
    assert classifier._classify_carry({"spread_bps": -15.0}) == "inverted"


def test_classify_trend_regimes():
    classifier = RegimeClassifier()

    assert classifier._classify_trend({"trend_score": 1.2, "breadth": 0.6, "reversal_probability": 0.1}) == "strong_trend"
    assert classifier._classify_trend({"trend_score": 0.4, "breadth": 0.45, "reversal_probability": 0.2}) == "choppy"
    assert classifier._classify_trend({"trend_score": 1.1, "breadth": 0.62, "reversal_probability": 0.8}) == "reversal"


def test_macro_risk_off_when_curve_inverts_or_stress_rises():
    classifier = RegimeClassifier()

    snapshot = classifier.classify(
        as_of="2026-03-08T22:00:00Z",
        market_data=_market_data(
            vix=31.0,
            yield_data={"spread_bps": -20.0},
            macro_data={"credit_spread_bps": 190.0, "equity_drawdown_pct": -12.0},
        ),
    )

    assert snapshot.vol_regime == "high"
    assert snapshot.carry_regime == "inverted"
    assert snapshot.macro_regime == "risk_off"
    assert snapshot.sizing_factor == 0.55
    assert "de_risk" in snapshot.active_overrides


def test_macro_transition_for_normalized_but_not_clean_market():
    classifier = RegimeClassifier()

    snapshot = classifier.classify(
        as_of="2026-03-08T22:00:00Z",
        market_data=_market_data(
            vix=18.0,
            yield_data={"spread_bps": 35.0},
            macro_data={"credit_spread_bps": 135.0},
            index_data={"trend_score": 0.3, "breadth": 0.49, "reversal_probability": 0.2},
        ),
    )

    assert snapshot.vol_regime == "normal"
    assert snapshot.trend_regime == "choppy"
    assert snapshot.carry_regime == "flat"
    assert snapshot.macro_regime == "transition"
    assert snapshot.sizing_factor == 0.8


def test_macro_risk_on_for_low_vol_and_healthy_curve():
    classifier = RegimeClassifier()

    snapshot = classifier.classify(
        as_of="2026-03-08T22:00:00Z",
        market_data=_market_data(),
    )

    assert snapshot.macro_regime == "risk_on"
    assert snapshot.sizing_factor == 1.0
    assert snapshot.active_overrides == ["increase_trend_weight"]


def test_sizing_factor_has_floor():
    classifier = RegimeClassifier()

    assert classifier._compute_sizing_factor("crisis", "reversal", "inverted") == 0.5
