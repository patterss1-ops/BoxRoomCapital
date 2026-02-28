# Grid Trading Deep Dive: Evidence and Backtest

## Question
Should grid trading be a regular/core component of your crypto strategy stack?

## Short answer
Not as a core sleeve. Use it conditionally in range regimes, with strict risk gates.

## Why this report exists
You asked for an evidence-based view of when grid works and why it may fail as a regular strategy in a one-person, automation-first operation.

## External Evidence (Primary Sources)

1. Traditional grid can have weak/near-zero expectancy before costs:
   - The 2025 DGT paper explicitly states traditional grid expected return is effectively zero under simple assumptions and highlights fee drag.
2. Grid is range-dependent by design:
   - Exchange docs (OKX, Bybit, Binance Academy) repeatedly state grid is designed for sideways/volatile ranges.
3. Trend breakouts are the key failure mode:
   - Exchange docs note bots stop placing new orders outside configured bounds; futures versions carry liquidation risk.

## Local Backtest (This Repo)

Backtest script:
- `analytics/grid_backtest.py`

Outputs:
- `.runtime/grid_backtest_1h.csv`
- `.runtime/grid_backtest_1d.csv`

Data and setup:
- Asset: BTC-USD via Yahoo Finance
- Timeframes:
  - 730 days, 1-hour bars
  - 10 years, 1-day bars
- Strategies:
  - Buy-and-hold benchmark
  - Static grid (band/levels/fee sensitivity)
  - Dynamic reset grid (band reset after breakout)
- Fees:
  - 4 bps and 10 bps per trade

## Key Results

## A) 730d hourly (recent, more range/chop impact)
- Full sample:
  - Buy-and-hold: +7.0%
  - Best static grid: +38.4%
  - Dynamic grid variants: as low as -23.2%
- Trend-up window:
  - Buy-and-hold: +67.3%
  - Grid: +3.7% to +21.5% (large opportunity cost)
- Trend-down window:
  - Buy-and-hold: -30.5%
  - Grid: roughly -19.9% to -25.5% (drawdown cushioning)
- Range-like window:
  - Buy-and-hold: ~0.0%
  - Grid: +0.9% to +3.9%

Interpretation:
- Grid can shine in chop/range windows.
- It lags badly in strong uptrends unless heavily modified.
- Dynamic resets can overtrade and become fee-fragile.

## B) 10y daily (long-horizon drift reality check)
- Full sample:
  - Buy-and-hold: +15,065.9%
  - Best static grid config: +385.4%
  - Other grid configs: near +7% to +302%
- Trend-up window:
  - Buy-and-hold: +435.5%
  - Best grid: +82.5%
- Trend-down window:
  - Buy-and-hold: -62.9%
  - Best grid: -42.7%
- Range-like window:
  - Buy-and-hold: ~0.0%
  - Best grid: +8.6%

Interpretation:
- Over long horizons with positive drift, grid gives up too much upside.
- Grid improves some drawdown outcomes but creates severe long-run opportunity cost.
- This is the central reason grid is weak as a permanent core sleeve.

## Additional Benchmark Check (Simple Trend Alternative)
Ad-hoc check on the same 10y daily BTC data:
- Buy-and-hold:
  - Total return: +15,061.5%
  - Max drawdown: -83.4%
  - Sharpe: 1.09
- SMA-200 trend filter:
  - Total return: +9,570.1%
  - Max drawdown: -70.1%
  - Sharpe: 1.14

Interpretation:
- A simple trend filter preserved much more upside than grid while still reducing drawdown.
- For a core sleeve, trend-following dominates grid's risk/reward tradeoff in this sample.

## Why Grid Should Not Be "Regular/Core" in Your Stack
1. Strong regime dependency:
   - Works mostly in ranges; degrades in directional runs.
2. High parameter fragility:
   - Small changes in band, levels, reset rules, and fees can flip outcomes.
3. Fee/turnover sensitivity:
   - Frequent fills and resets create cost drag.
4. Opportunity-cost risk:
   - In secular uptrends, grid often underparticipates badly.
5. Operational complexity:
   - Needs tight breakout/risk controls to avoid becoming a hidden short-vol behavior.

## Recommended Role in Your Architecture
- Status: Candidate/Satellite sleeve, not Core.
- Allocation: Start <=5-10% of crypto sleeve.
- Activate only when range classifier is true.
- Mandatory controls:
  - breakout stop / out-of-range terminate
  - trend veto (disable grid in high-trend regimes)
  - per-day trade cap and fee budget cap
  - hard drawdown kill switch

## Promotion Criteria (Before Any Scale-Up)
1. Out-of-sample live-paper period passes risk and slippage limits.
2. Positive net PnL after all fees and execution costs.
3. Lower portfolio correlation to core sleeves (carry + momentum).
4. No control breaches for a full evaluation cycle.

## Reproduce
Run:
```bash
python3 analytics/grid_backtest.py --period 730d --interval 1h --out-json .runtime/grid_backtest_1h.json --out-csv .runtime/grid_backtest_1h.csv
python3 analytics/grid_backtest.py --period 10y --interval 1d --out-json .runtime/grid_backtest_1d.json --out-csv .runtime/grid_backtest_1d.csv
```

## Sources
- Dynamic Grid Trading Strategy (arXiv 2506.11921): https://arxiv.org/abs/2506.11921
- OKX Futures Grid docs: https://www.okx.com/en-us/help/ii-futures-grid
- Bybit Futures Grid docs: https://www.bybit.com/en/help-center/article/Introduction-to-Futures-Grid-Bot-on-Bybit
- Binance Academy Grid guide: https://academy.binance.com/en/articles/step-by-step-guide-to-grid-trading-on-binance-futures
