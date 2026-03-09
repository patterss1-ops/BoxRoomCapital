from research.engine_a.signals import CarrySignal, MomentumSignal, TrendSignal, ValueSignal


def test_trend_signal_positive_for_uptrend():
    prices = [100 + i * 0.8 for i in range(90)]

    signal = TrendSignal().compute(prices)

    assert 0 < signal <= 1


def test_trend_signal_negative_for_downtrend():
    prices = [200 - i * 1.1 for i in range(90)]

    signal = TrendSignal().compute(prices)

    assert -1 <= signal < 0


def test_trend_signal_returns_zero_for_short_history():
    assert TrendSignal().compute([100, 101, 102]) == 0.0


def test_carry_signal_is_bounded_and_positive_when_front_above_deferred():
    history = [-0.08, -0.04, 0.0, 0.03, 0.05]

    signal = CarrySignal().compute(front_price=102.0, deferred_price=100.0, days_to_roll=30, history=history)

    assert 0 < signal <= 1


def test_value_signal_clips_large_z_scores():
    history = [1.5 + (i % 5) * 0.02 for i in range(300)]

    signal = ValueSignal().compute(current_value=3.5, history=history)

    assert signal == 1.0


def test_momentum_signal_rewards_long_term_strength_with_recent_pullback():
    prices = []
    price = 100.0
    for _ in range(260):
        price *= 1.002
        prices.append(price)
    for _ in range(30):
        price *= 0.999
        prices.append(price)

    signal = MomentumSignal().compute(prices)

    assert 0 < signal <= 1


def test_momentum_signal_returns_zero_for_short_history():
    assert MomentumSignal().compute([100 + i for i in range(100)]) == 0.0
