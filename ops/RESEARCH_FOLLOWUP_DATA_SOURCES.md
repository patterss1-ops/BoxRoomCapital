# Research Follow-Up: Solo Operator Data Sources & Numeric Infrastructure

**Date:** 2026-03-08 | **Source:** ChatGPT Pro Deep Research
**Prompt:** What data sources (textual + numeric), market snapshots, time series, charting tools, and homegrown vs commercial services do solo operators (Carver, Alvarez, Davey, Darwinex/Wim) use?

---

## Key Conclusion

The solo winners are mostly not gathering "all available data." They are gathering a small number of critical datasets with painful care. The recurring essentials are: clean price history, accurate session definitions, contract or universe metadata, corporate actions, delisted securities, historical constituents, costs/liquidity, and broker/account state. Textual data appears as an extra, not as the base layer.

**Build sequence implication:** BoxRoomCapital should be built numeric-data-first, NOT text-ingestion-first.

---

## 1. Rob Carver — Serious One-Person Futures Stack

**Evidence quality:** High (public pysystemtrade documentation).

**Textual data:** Almost anti-news. System is "purely technical" using only price data. Has not explored IB news, fundamental data, option data, or scanners.

**Numeric data (load-bearing):**
- Individual futures contract prices
- Intraday mid prices for currently traded contract
- Inside-spread width and size for liquidity
- Closing prices of nearby and other strip contracts (carry/rolldown/roll decisions)
- Volume for roll decisions
- Roll calendars
- "Multiple prices" frames (current/next/carry contracts)
- Back-adjusted continuous prices
- Spot FX prices
- Spread-cost data
- Storage: MongoDB for static, Parquet for time series

**Snapshots:** Intraday prices ~hourly with varying times + closing-price records. Broker position and accounting snapshots with reconciliation. Market snapshot = price + liquidity + broker state + reconciliation state.

**Key time series:** Synchronized contract-level closes, carry-relationship series across strip, roll schedules, volatility estimates, FX conversion series, spread-cost histories, broker/account state. Fixed end-of-day timestamp to avoid look-ahead bias.

**Charting:** Minimal. Does not use candlesticks or bar charts. Works with series of price points. Charts for diagnostics, roll inspection, volatility inspection, trade replay — not primary research interface.

**Stack:** Hybrid. Commercial broker/data (IBKR), homegrown research + production in Python, self-managed storage.

**Lesson:** Build futures-native market-data core with contract-level storage first, then derive roll calendars, carry series, continuous prices, liquidity snapshots, broker/account reconciliation. Do not start with generic OHLC database.

---

## 2. Cesar Alvarez — Daily-Equities Researcher's Data Stack

**Evidence quality:** High for equities research workflow.

**Textual data:** Secondary. Core is daily stock data and numerical regime/timing models. Even with access to alt/textual data (Quantopian), still chose clean price/universe data as center of stack.

**Numeric data:**
- AmiBroker + CSI Data (original), then Norgate Data
- Delisted stocks, dividend/capital-gain adjustments, as-traded prices
- Excel + MySQL for analysis
- Multiple AmiBroker databases: 2yr (daily scans), 11yr (backtests), 25yr (archive)

**Snapshots:** Daily snapshot operator. Next-open entries/exits, daily closes, daily ranking, daily market filters. "Market Barometer" = current + 1-week-ago + 1-month-ago states, combining S&P 500 models with bonds/other markets for regime rating.

**Key time series:** Daily OHLCV, as-traded + adjusted price, delisted-stock histories, historical index membership, liquidity (21d avg dollar volume), volatility (100d HV), moving averages, RSI, ATR, benchmark/regime series (SPY, bonds), industry classification.

**Charting:** Quant-research style via AmiBroker. Connected to backtest output, trade lists, regime state, universe filters.

**Stack:** Commercial data + commercial backtester, custom AFL + spreadsheet/database analysis.

**Lesson:** Non-negotiables: delisted securities, historical constituents, as-traded and adjusted pricing, nightly scans, regime snapshots, integrated ranking/exploration tooling. If app can't answer "was this stock in the universe on that date?" it's a survivorship-bias generator.

---

## 3. Kevin Davey — Platform-Centric Futures/Strategy-Factory

**Evidence quality:** Medium-high for workflow/platform.

**Textual data:** Basically absent from core workflow.

**Numeric data:** TradeStation primary (charting, scanning, RadarScreen, Portfolio Maestro, real-time + historical data). Also NinjaTrader, MultiCharts.

**Snapshots:** Bar-based at multiple frequencies + scanner-based market state. RadarScreen = real-time snapshot grid (each row = chart equivalent). Portfolio Maestro supports daily/60min/10min in one portfolio. Documented session-definition change that broke crude-oil strategy.

**Key time series:** 10-20+ years history, multiple resolutions, stable session definitions, realistic slippage/commission, portfolio-level aggregation.

**Charting:** Central. Part of idea discovery, debugging, operator monitoring.

**Stack:** Commercial core + custom strategy code.

**Lesson:** App needs scanner layer, portfolio-level backtest layer, session/version layer.

---

## 4. Darwinex / MQL5 / "Wim" — Platform-Native Solo Operator

**Evidence quality:** Low-medium.

**Textual data:** No evidence of text-dependent edge.

**Numeric data:** Platform-native (MT5 quotes, bars, indicators, Strategy Tester with tick/bar modes + delay simulation). Darwinex REST/WebSocket APIs.

**Charting:** Likely yes (MT5 is chart/indicator/tester heavy).

**Lesson:** If short-horizon strategies, need test modes that degrade realism (tick vs minute OHLC vs open-only + delay simulation).

---

## 5. Five-Layer Data Architecture

The report recommends building in this order:

### Layer 1: Raw Market Data
Store vendor-native bars/ticks exactly as received, with vendor, timestamp convention, session template, and field semantics attached. Don't normalize away provenance.

### Layer 2: Canonical Market Series
- **Equities:** Adjusted + as-traded prices, corporate actions, delisting state, listing state, historical constituent membership
- **Futures:** Individual contracts, roll calendars, current/next/carry relationships, back-adjusted continuous series, FX conversion

### Layer 3: Snapshot Engine
Explicit snapshots rather than recomputing from raw data:
- End-of-day market snapshot
- Intraday signal snapshot (fixed/semi-fixed times)
- Futures term-structure snapshot
- Universe snapshot
- Regime snapshot
- Broker/account snapshot
- Execution-quality snapshot

### Layer 4: Research/Analysis
Ranking, screening, backtesting, walk-forward, Monte Carlo, portfolio interaction, bias-control logic.

### Layer 5: Optional Textual/Event
Add later, not first. Only prioritize earlier if explicitly building event/revision/transcript engine.

---

## 6. Required Time Series

### Equities
- Raw daily OHLCV
- Adjusted OHLCV
- As-traded price/volume
- Corporate actions
- Delisted status
- Major-exchange listing status
- Historical constituent membership
- Benchmark/index series
- Daily dollar volume
- Realized volatility windows
- Common indicator windows
- Optional fundamentals
- Optional earnings/event calendar

### Futures
- Contract-level OHLCV
- Bid/ask or mid + inside spread
- Open interest/volume
- Roll parameters + roll calendars
- Current/next/carry mapping
- Continuous adjusted price
- Carry/term-structure series
- Spot FX conversion
- Spread-cost and commission series
- Broker/account state

### FX / Platform-Native
- Bid/ask or tick history
- Minute OHLC
- Session boundaries
- Spread series
- Execution-delay assumptions
- Broker-specific symbol metadata
- Roll/swap/funding where relevant

---

## 7. Charting Requirements

Four jobs for charts:
1. **Data validation:** Spot bad spikes, broken sessions, roll glitches, vendor mismatches
2. **Trade replay:** Show entry, exit, ranking state, data visible at that moment
3. **Scanner/portfolio context:** Many symbols ranked/monitored at once (RadarScreen-style)
4. **Test/live comparison:** Visual testing to see when execution assumptions fail

Recommended chart types:
- Symbol chart
- Trade replay chart
- Portfolio heat/scanner view
- Regime dashboard
- Futures strip/roll chart

---

## 8. Buy vs Build Recommendations

| Component | Recommendation | Model |
|-----------|---------------|-------|
| Equities research data | Commercial (Norgate-style with delistings/constituents) | Alvarez |
| Futures live + some history | IBKR | Carver |
| Deeper futures history | External historical provider | Carver |
| App logic, snapshots, regime, monitoring, research memory | Homegrown | All |
| Charting and notebooks | Embedded in app | Davey |

---

## 9. Concrete Data Entities

| Entity | Key Fields | Source Lesson |
|--------|-----------|--------------|
| **InstrumentMaster** | Symbol, asset class, venue, currency, trading hours/session template, multiplier, tick size, vendor IDs, active/inactive, listing/delisting dates | IB contracts + Norgate |
| **RawBar / RawTick** | Vendor-native timestamp, OHLCV or bid/ask/last, session code, provenance, ingestion version | Vendor semantics differ |
| **CanonicalBar** | Normalized bar after session rules, adjustments, quality checks. Versioned. Never overwrite silently | Davey/Alvarez pitfalls |
| **CorporateAction / UniverseMembership** | Splits, dividends, constituent membership by date, listing status | Mandatory for equities |
| **FuturesContract / RollCalendar / MultiplePrices / ContinuousSeries** | The Carver block — contract-level storage + derived series | Without this, futures is a toy |
| **LiquidityCostSeries** | Inside spread, width, cost assumptions, commissions, borrow/funding | Carver + Davey |
| **RegimeSnapshot** | Indices, bonds, vol proxies, trends, state labels, calculation time | Alvarez Market Barometer |
| **PortfolioSnapshot / BrokerSnapshot** | Positions, cash, margin, P&L, order state, reconciliation flags | Carver reconciliation |
| **ResearchRun / BacktestRun** | Data version, session template, universe version, params, slippage model, timestamps, artifacts | Davey + Alvarez traceability |

---

## Source
ChatGPT Pro deep research, 2026-03-08. Prompted from BoxRoomCapital solo operator research series.
