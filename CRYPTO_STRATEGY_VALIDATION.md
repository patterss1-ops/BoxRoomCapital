# Crypto Algo Strategy Validation and Upgrade

## Scope
Validate the pasted Perplexity draft, identify weak claims, and replace it with an execution-ready plan for a one-person automated desk.

## Validation Summary

### High-confidence (supported by primary research or exchange docs)
1. Funding/carry exists and can be large:
   - BIS "Crypto carry" reports carry averaging above 10% annually and links high carry to future crash risk.
2. Time-series momentum exists in crypto:
   - Multiple peer-reviewed studies document momentum effects and strategy outperformance vs passive benchmarks in tested samples.
3. Pairs/stat-arb can work in crypto:
   - Research supports cointegration/mean-reversion opportunities, but performance is highly implementation-dependent.
4. Funding mechanics:
   - Major venues document 8-hour (or variable) funding settlement mechanics and formula structure.

### Medium-confidence (directionally plausible, but evidence quality mixed)
1. Grid trading works in ranging regimes:
   - Plausible and widely used, but strong performance claims usually come from platform marketing/leaderboards and are not robust out-of-sample evidence.
2. Mean reversion with regime filter can improve outcomes:
   - Sensible design pattern, but indicator-only claims are often overfit.

### Low-confidence / weakly supported claims in the draft
1. "Closest thing to free money" for funding arb:
   - Incorrect framing. Real risks: exchange default, basis collapse/funding inversion, execution slippage, liquidation from leverage/margin mismanagement.
2. Specific performance numbers from venue marketing:
   - Gate/Binance/Lux-style numbers are not independent evidence and should not be used as expected-return assumptions.
3. Highly precise backtest claims from blogs/theses:
   - Useful as hypotheses, not deployment evidence.

## Critique of the Original Draft

### What was good
- Correctly prioritizes funding carry as a key crypto-native alpha.
- Good call to combine trend and mean-reversion with regime switching.
- Reasonable intent to run market-neutral sleeves alongside directional sleeves.

### What needs fixing
1. Evidence hierarchy is mixed:
   - Institutional/peer-reviewed findings and vendor/blog claims were presented at equal weight.
2. Risk treatment is understated:
   - Counterparty, operational, and liquidation risks need to be first-class, not footnotes.
3. Return expectations are too point-estimate heavy:
   - Should use conservative ranges and net-of-cost assumptions.
4. No explicit promotion gates:
   - Needs hard criteria for moving from research -> paper -> live capital.

## Improved Strategy Stack (Crypto Sleeve)

## Sleeve A: Funding Carry (Market-Neutral Core)
- Objective: stable carry capture with strict risk containment.
- Instruments: top-liquidity perp/spot pairs only.
- Constraints:
  - Max leverage 1.5x initially.
  - Dynamic cap when funding decays or inverts.
  - Venue concentration cap (eg, <=35% capital per venue).
- Kill conditions:
  - Funding regime inversion for N intervals.
  - Exchange health/liquidity anomaly.
  - Drawdown breach.
- Expected profile (conservative, net of costs): mid single-digit to low double-digit annualized, with left-tail event risk.

## Sleeve B: Cross-Sectional + Time-Series Momentum (Directional)
- Objective: capture persistent trends while controlling crash risk.
- Design:
  - Vol-targeted portfolio.
  - Trend filter + volatility throttle.
  - Fewer assets, higher liquidity universe.
- Risk controls:
  - Per-position risk cap.
  - Portfolio VaR/drawdown throttle.
  - Fast de-risk on volatility shock.

## Sleeve C: Mean-Reversion/Range Engine (Conditional)
- Objective: harvest chop when trend signal is weak.
- Design:
  - Regime classifier gates activation.
  - Spread/fee-aware entry thresholds.
  - Time-stop + volatility-stop + inventory cap.
- Hard rule:
  - Disabled automatically in strong trend regimes.

## Deployment Standard (Non-Negotiable)
1. Research:
   - Walk-forward and out-of-sample tests.
   - Fee/slippage/latency assumptions stress-tested.
2. Paper:
   - Minimum live-paper runtime with incident-free operation.
3. Live:
   - Start with smallest tier capital.
   - Auto downgrade back to paper on risk/control violations.
4. Ongoing:
   - Weekly model diagnostics and monthly promotion/demotion review.

## Concrete Build Tasks for This Repo
1. Add `strategy_class` and `regime_state` fields to strategy metadata.
2. Add funding-rate ingestion + carry monitor job.
3. Add venue risk monitor (latency, rejected orders, withdrawal/deposit flags, spread blowout).
4. Add portfolio-level risk budget allocator across crypto sleeves.
5. Add promotion gates:
   - `research -> paper -> shadow_live -> live`.
6. Add runbook pages in UI:
   - "Crypto Sleeve Health", "Carry State", "Regime State", "Auto-Degrade Events".

## Bottom Line
- Keep funding carry as the anchor, but stop calling it near risk-free.
- Use momentum and mean-reversion as mutually exclusive regime sleeves.
- Treat vendor ROI claims as hypotheses only.
- Build with strict risk gating first, alpha scaling second.

## Sources Checked
- BIS Working Paper: Crypto carry
- Bybit and Binance funding-rate documentation
- Peer-reviewed momentum research in crypto (e.g., NAJEF 2021; FRL 2025 risk-managed momentum)
- CoinMetrics funding-rate methodology docs
- Research and institutional notes on basis/funding implementation risk
