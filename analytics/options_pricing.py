"""
Options Pricing Engine — Black-Scholes model with full Greeks.

Provides:
  - European call/put pricing
  - All Greeks: delta, gamma, theta, vega, rho
  - Implied volatility solver (bisection method)
  - Credit spread / iron condor P&L and risk calculations
  - Position sizing via fractional Kelly criterion

No external dependencies beyond numpy and scipy (already in our stack).

Usage:
    from analytics.options_pricing import BlackScholes, CreditSpread, IronCondor

    # Price a call
    bs = BlackScholes(S=5200, K=5100, T=30/365, r=0.05, sigma=0.18)
    print(bs.call_price, bs.call_delta, bs.call_theta)

    # Implied vol from market price
    iv = BlackScholes.implied_vol(S=5200, K=5100, T=30/365, r=0.05,
                                   market_price=180.0, option_type="call")

    # Credit put spread
    spread = CreditSpread(
        short_strike=5000, long_strike=4900, premium_received=35.0,
        S=5200, T=30/365, r=0.05, sigma=0.18, spread_type="put"
    )
    print(spread.max_profit, spread.max_loss, spread.breakeven, spread.prob_profit)

    # Iron condor
    ic = IronCondor(
        short_put=5000, long_put=4900, short_call=5400, long_call=5500,
        put_credit=35.0, call_credit=30.0,
        S=5200, T=30/365, r=0.05, sigma=0.18
    )
    print(ic.max_profit, ic.max_loss, ic.prob_profit)
"""
import math
from typing import Optional

import numpy as np
from scipy.stats import norm


# ─── Black-Scholes Model ────────────────────────────────────────────────────

class BlackScholes:
    """
    Black-Scholes European option pricing with full Greeks.

    Parameters:
        S:     Current underlying price
        K:     Strike price
        T:     Time to expiration in years (e.g. 30/365 for 30 days)
        r:     Risk-free rate (annualised, e.g. 0.05 for 5%)
        sigma: Volatility (annualised, e.g. 0.18 for 18%)
        q:     Continuous dividend yield (default 0)
    """

    def __init__(self, S: float, K: float, T: float, r: float, sigma: float,
                 q: float = 0.0):
        self.S = S
        self.K = K
        self.T = max(T, 1e-10)  # Avoid division by zero at expiry
        self.r = r
        self.sigma = max(sigma, 1e-10)
        self.q = q

        # Pre-compute d1 and d2
        self._d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * self.T) / \
                    (sigma * math.sqrt(self.T))
        self._d2 = self._d1 - sigma * math.sqrt(self.T)

    # ─── Option prices ───────────────────────────────────────────────

    @property
    def call_price(self) -> float:
        """European call option price."""
        return (self.S * math.exp(-self.q * self.T) * norm.cdf(self._d1) -
                self.K * math.exp(-self.r * self.T) * norm.cdf(self._d2))

    @property
    def put_price(self) -> float:
        """European put option price."""
        return (self.K * math.exp(-self.r * self.T) * norm.cdf(-self._d2) -
                self.S * math.exp(-self.q * self.T) * norm.cdf(-self._d1))

    # ─── Greeks ──────────────────────────────────────────────────────

    @property
    def call_delta(self) -> float:
        """Call delta: dC/dS. Range [0, 1]."""
        return math.exp(-self.q * self.T) * norm.cdf(self._d1)

    @property
    def put_delta(self) -> float:
        """Put delta: dP/dS. Range [-1, 0]."""
        return -math.exp(-self.q * self.T) * norm.cdf(-self._d1)

    @property
    def gamma(self) -> float:
        """Gamma (same for calls and puts): d²C/dS²."""
        return (math.exp(-self.q * self.T) * norm.pdf(self._d1)) / \
               (self.S * self.sigma * math.sqrt(self.T))

    @property
    def call_theta(self) -> float:
        """
        Call theta: dC/dT (per day).
        Negative = option loses value as time passes (good for sellers).
        """
        term1 = -(self.S * self.sigma * math.exp(-self.q * self.T) *
                   norm.pdf(self._d1)) / (2 * math.sqrt(self.T))
        term2 = self.q * self.S * math.exp(-self.q * self.T) * norm.cdf(self._d1)
        term3 = self.r * self.K * math.exp(-self.r * self.T) * norm.cdf(self._d2)
        return (term1 + term2 - term3) / 365  # Per calendar day

    @property
    def put_theta(self) -> float:
        """Put theta: dP/dT (per day)."""
        term1 = -(self.S * self.sigma * math.exp(-self.q * self.T) *
                   norm.pdf(self._d1)) / (2 * math.sqrt(self.T))
        term2 = -self.q * self.S * math.exp(-self.q * self.T) * norm.cdf(-self._d1)
        term3 = self.r * self.K * math.exp(-self.r * self.T) * norm.cdf(-self._d2)
        return (term1 + term2 + term3) / 365  # Per calendar day

    @property
    def vega(self) -> float:
        """Vega (same for calls and puts): dC/dσ per 1% move in vol."""
        return (self.S * math.exp(-self.q * self.T) *
                math.sqrt(self.T) * norm.pdf(self._d1)) / 100

    @property
    def call_rho(self) -> float:
        """Call rho: dC/dr per 1% move in rate."""
        return self.K * self.T * math.exp(-self.r * self.T) * norm.cdf(self._d2) / 100

    @property
    def put_rho(self) -> float:
        """Put rho: dP/dr per 1% move in rate."""
        return -self.K * self.T * math.exp(-self.r * self.T) * norm.cdf(-self._d2) / 100

    # ─── Probability calculations ────────────────────────────────────

    @property
    def prob_itm_call(self) -> float:
        """Probability of call expiring in-the-money (risk-neutral)."""
        return norm.cdf(self._d2)

    @property
    def prob_itm_put(self) -> float:
        """Probability of put expiring in-the-money (risk-neutral)."""
        return norm.cdf(-self._d2)

    # ─── Implied volatility ──────────────────────────────────────────

    @staticmethod
    def implied_vol(S: float, K: float, T: float, r: float,
                    market_price: float, option_type: str = "call",
                    q: float = 0.0, tol: float = 1e-6,
                    max_iter: int = 100) -> Optional[float]:
        """
        Solve for implied volatility using bisection method.

        Args:
            market_price: Observed option premium
            option_type: "call" or "put"

        Returns:
            Implied volatility (annualised), or None if no solution found.
        """
        low, high = 0.001, 5.0  # Vol range: 0.1% to 500%

        for _ in range(max_iter):
            mid = (low + high) / 2
            bs = BlackScholes(S=S, K=K, T=T, r=r, sigma=mid, q=q)
            price = bs.call_price if option_type == "call" else bs.put_price

            if abs(price - market_price) < tol:
                return mid
            elif price > market_price:
                high = mid
            else:
                low = mid

        return (low + high) / 2  # Best approximation

    # ─── Summary ─────────────────────────────────────────────────────

    def summary(self, option_type: str = "call") -> dict:
        """Return a summary dict of price and all Greeks."""
        if option_type == "call":
            return {
                "price": round(self.call_price, 4),
                "delta": round(self.call_delta, 4),
                "gamma": round(self.gamma, 6),
                "theta": round(self.call_theta, 4),
                "vega": round(self.vega, 4),
                "rho": round(self.call_rho, 4),
                "prob_itm": round(self.prob_itm_call, 4),
            }
        else:
            return {
                "price": round(self.put_price, 4),
                "delta": round(self.put_delta, 4),
                "gamma": round(self.gamma, 6),
                "theta": round(self.put_theta, 4),
                "vega": round(self.vega, 4),
                "rho": round(self.put_rho, 4),
                "prob_itm": round(self.prob_itm_put, 4),
            }


# ─── Credit Spread ──────────────────────────────────────────────────────────

class CreditSpread:
    """
    Credit spread (bull put or bear call) analysis.

    A credit spread collects premium upfront with defined max loss.
    No overnight financing because the option has a fixed expiry.

    Bull Put Spread (bullish):
      - Sell higher-strike put (collect premium)
      - Buy lower-strike put (cap risk)
      - Profit if underlying stays above short put strike

    Bear Call Spread (bearish):
      - Sell lower-strike call (collect premium)
      - Buy higher-strike call (cap risk)
      - Profit if underlying stays below short call strike
    """

    def __init__(self, short_strike: float, long_strike: float,
                 premium_received: float,
                 S: float, T: float, r: float, sigma: float,
                 spread_type: str = "put", q: float = 0.0):
        """
        Args:
            short_strike: Strike of option sold (higher for puts, lower for calls)
            long_strike: Strike of option bought (lower for puts, higher for calls)
            premium_received: Net credit received (short premium - long premium)
            spread_type: "put" (bull put spread) or "call" (bear call spread)
        """
        self.short_strike = short_strike
        self.long_strike = long_strike
        self.premium = premium_received
        self.spread_type = spread_type
        self.S = S
        self.T = T
        self.r = r
        self.sigma = sigma
        self.q = q

        # Width of spread
        self.width = abs(short_strike - long_strike)

        # Greeks for individual legs
        self.short_bs = BlackScholes(S=S, K=short_strike, T=T, r=r, sigma=sigma, q=q)
        self.long_bs = BlackScholes(S=S, K=long_strike, T=T, r=r, sigma=sigma, q=q)

    @property
    def max_profit(self) -> float:
        """Maximum profit = premium received. Achieved if both options expire OTM."""
        return self.premium

    @property
    def max_loss(self) -> float:
        """Maximum loss = spread width - premium received."""
        return self.width - self.premium

    @property
    def risk_reward_ratio(self) -> float:
        """Risk/reward ratio. Lower is better."""
        return self.max_loss / max(self.max_profit, 0.01)

    @property
    def breakeven(self) -> float:
        """Breakeven price at expiry."""
        if self.spread_type == "put":
            return self.short_strike - self.premium
        else:
            return self.short_strike + self.premium

    @property
    def prob_profit(self) -> float:
        """
        Estimated probability of profit (option expires OTM).
        For puts: P(S > short_strike at expiry)
        For calls: P(S < short_strike at expiry)
        Uses Black-Scholes risk-neutral probability.
        """
        if self.spread_type == "put":
            return 1 - self.short_bs.prob_itm_put
        else:
            return 1 - self.short_bs.prob_itm_call

    @property
    def delta(self) -> float:
        """Net delta of the spread (short - long)."""
        if self.spread_type == "put":
            return -(self.short_bs.put_delta - self.long_bs.put_delta)
        else:
            return -(self.short_bs.call_delta - self.long_bs.call_delta)

    @property
    def theta(self) -> float:
        """Net theta (per day). Positive = time decay works in your favour."""
        if self.spread_type == "put":
            return -(self.short_bs.put_theta - self.long_bs.put_theta)
        else:
            return -(self.short_bs.call_theta - self.long_bs.call_theta)

    @property
    def vega(self) -> float:
        """Net vega. Negative = benefits from falling volatility."""
        return -(self.short_bs.vega - self.long_bs.vega)

    @property
    def gamma(self) -> float:
        """Net gamma."""
        return -(self.short_bs.gamma - self.long_bs.gamma)

    def pnl_at_expiry(self, price: float) -> float:
        """Calculate P&L at a given underlying price at expiry."""
        if self.spread_type == "put":
            if price >= self.short_strike:
                return self.premium  # Both OTM
            elif price <= self.long_strike:
                return self.premium - self.width  # Max loss
            else:
                return self.premium - (self.short_strike - price)
        else:
            if price <= self.short_strike:
                return self.premium  # Both OTM
            elif price >= self.long_strike:
                return self.premium - self.width  # Max loss
            else:
                return self.premium - (price - self.short_strike)

    def summary(self) -> dict:
        return {
            "type": f"{'Bull Put' if self.spread_type == 'put' else 'Bear Call'} Spread",
            "short_strike": self.short_strike,
            "long_strike": self.long_strike,
            "premium": round(self.premium, 2),
            "max_profit": round(self.max_profit, 2),
            "max_loss": round(self.max_loss, 2),
            "risk_reward": round(self.risk_reward_ratio, 2),
            "breakeven": round(self.breakeven, 2),
            "prob_profit": round(self.prob_profit * 100, 1),
            "delta": round(self.delta, 4),
            "theta_day": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "gamma": round(self.gamma, 6),
        }


# ─── Iron Condor ────────────────────────────────────────────────────────────

class IronCondor:
    """
    Iron Condor = Bull Put Spread + Bear Call Spread.

    Profit if underlying stays within a range.
    Max profit = total premium collected.
    Max loss = wider spread width - total premium.
    No overnight financing (options have fixed expiry).

    This is the ideal structure for our IBS signals:
    - When IBS says market is neutral/ranging, sell an iron condor
    - Collect premium from both sides
    - Time decay (theta) works in your favour every day
    """

    def __init__(self, short_put: float, long_put: float,
                 short_call: float, long_call: float,
                 put_credit: float, call_credit: float,
                 S: float, T: float, r: float, sigma: float,
                 q: float = 0.0):
        self.put_spread = CreditSpread(
            short_strike=short_put, long_strike=long_put,
            premium_received=put_credit,
            S=S, T=T, r=r, sigma=sigma, spread_type="put", q=q
        )
        self.call_spread = CreditSpread(
            short_strike=short_call, long_strike=long_call,
            premium_received=call_credit,
            S=S, T=T, r=r, sigma=sigma, spread_type="call", q=q
        )
        self.total_premium = put_credit + call_credit
        self.S = S

    @property
    def max_profit(self) -> float:
        """Total premium collected from both spreads."""
        return self.total_premium

    @property
    def max_loss(self) -> float:
        """Max loss = wider spread width - total premium."""
        return max(self.put_spread.max_loss, self.call_spread.max_loss) + \
               min(self.put_spread.max_profit, self.call_spread.max_profit)

    @property
    def max_loss_single_side(self) -> float:
        """Max loss on one side (can only lose on one side at a time)."""
        return max(self.put_spread.width, self.call_spread.width) - self.total_premium

    @property
    def prob_profit(self) -> float:
        """
        Probability both spreads expire OTM (underlying stays in range).
        Approximated as P(S > short_put) × P(S < short_call).
        """
        return self.put_spread.prob_profit * self.call_spread.prob_profit

    @property
    def breakeven_low(self) -> float:
        return self.put_spread.short_strike - self.total_premium

    @property
    def breakeven_high(self) -> float:
        return self.call_spread.short_strike + self.total_premium

    @property
    def delta(self) -> float:
        return self.put_spread.delta + self.call_spread.delta

    @property
    def theta(self) -> float:
        return self.put_spread.theta + self.call_spread.theta

    @property
    def vega(self) -> float:
        return self.put_spread.vega + self.call_spread.vega

    def pnl_at_expiry(self, price: float) -> float:
        """P&L at a given underlying price at expiry."""
        return self.put_spread.pnl_at_expiry(price) + \
               self.call_spread.pnl_at_expiry(price)

    def summary(self) -> dict:
        return {
            "type": "Iron Condor",
            "put_spread": self.put_spread.summary(),
            "call_spread": self.call_spread.summary(),
            "total_premium": round(self.total_premium, 2),
            "max_profit": round(self.max_profit, 2),
            "max_loss": round(self.max_loss_single_side, 2),
            "breakeven_low": round(self.breakeven_low, 2),
            "breakeven_high": round(self.breakeven_high, 2),
            "prob_profit": round(self.prob_profit * 100, 1),
            "delta": round(self.delta, 4),
            "theta_day": round(self.theta, 4),
            "vega": round(self.vega, 4),
        }


# ─── Position Sizing ────────────────────────────────────────────────────────

def kelly_fraction(win_prob: float, win_amount: float, loss_amount: float,
                   fraction: float = 0.25) -> float:
    """
    Fractional Kelly criterion for position sizing.

    Args:
        win_prob: Probability of profit (0-1)
        win_amount: Amount gained on a win
        loss_amount: Amount lost on a loss (positive number)
        fraction: Kelly fraction (0.25 = quarter Kelly, conservative)

    Returns:
        Fraction of bankroll to risk (0-1). 0 means don't trade.
    """
    if loss_amount <= 0 or win_amount <= 0:
        return 0.0

    # Kelly: f* = (p * b - q) / b  where b = win/loss ratio, p = win prob, q = 1-p
    b = win_amount / loss_amount
    q = 1 - win_prob
    kelly = (win_prob * b - q) / b

    if kelly <= 0:
        return 0.0  # Negative edge — don't trade

    return kelly * fraction


def size_credit_spread(equity: float, spread: CreditSpread,
                       max_risk_pct: float = 0.02,
                       kelly_frac: float = 0.25) -> dict:
    """
    Calculate position size for a credit spread.

    Uses fractional Kelly constrained by a hard max risk percentage.

    Args:
        equity: Total account equity
        spread: CreditSpread instance
        max_risk_pct: Maximum risk per trade as % of equity (default 2%)
        kelly_frac: Kelly fraction to use (default 0.25 = quarter Kelly)

    Returns:
        dict with contracts, risk_amount, kelly_size, capped info
    """
    # Kelly sizing
    kelly_pct = kelly_fraction(
        win_prob=spread.prob_profit,
        win_amount=spread.max_profit,
        loss_amount=spread.max_loss,
        fraction=kelly_frac,
    )

    # Convert to risk amount
    kelly_risk = equity * kelly_pct

    # Hard cap
    max_risk = equity * max_risk_pct

    # Use the smaller of Kelly and hard cap
    risk_amount = min(kelly_risk, max_risk)
    capped = kelly_risk > max_risk

    # Number of contracts (each contract risks max_loss)
    contracts = max(int(risk_amount / max(spread.max_loss, 0.01)), 0)

    return {
        "contracts": contracts,
        "risk_amount": round(risk_amount, 2),
        "kelly_pct": round(kelly_pct * 100, 2),
        "capped": capped,
        "max_risk": round(max_risk, 2),
        "expected_profit": round(contracts * spread.max_profit * spread.prob_profit -
                                  contracts * spread.max_loss * (1 - spread.prob_profit), 2),
    }


# ─── Convenience: quick pricing ─────────────────────────────────────────────

def price_option(S: float, K: float, days_to_expiry: int, sigma: float,
                 option_type: str = "call", r: float = 0.05) -> dict:
    """Quick option pricing helper."""
    T = days_to_expiry / 365.0
    bs = BlackScholes(S=S, K=K, T=T, r=r, sigma=sigma)
    return bs.summary(option_type)


def analyse_credit_spread(S: float, short_strike: float, long_strike: float,
                           days_to_expiry: int, sigma: float,
                           spread_type: str = "put", r: float = 0.05) -> dict:
    """
    Quick credit spread analysis.
    Calculates theoretical premium and full risk metrics.
    """
    T = days_to_expiry / 365.0
    short_bs = BlackScholes(S=S, K=short_strike, T=T, r=r, sigma=sigma)
    long_bs = BlackScholes(S=S, K=long_strike, T=T, r=r, sigma=sigma)

    if spread_type == "put":
        premium = short_bs.put_price - long_bs.put_price
    else:
        premium = short_bs.call_price - long_bs.call_price

    spread = CreditSpread(
        short_strike=short_strike, long_strike=long_strike,
        premium_received=premium,
        S=S, T=T, r=r, sigma=sigma, spread_type=spread_type,
    )
    return spread.summary()
