# BoxRoomCapital Trading Bot - User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Dashboard Overview](#dashboard-overview)
3. [Overview Page](#overview-page)
4. [Trading Page](#trading-page)
5. [Research Page](#research-page)
6. [Incidents & Jobs Page](#incidents--jobs-page)
7. [Settings Page](#settings-page)
8. [Command Palette](#command-palette)
9. [Operating Modes](#operating-modes)
10. [Risk Controls](#risk-controls)
11. [Strategy Reference](#strategy-reference)
12. [Webhooks & External Signals](#webhooks--external-signals)
13. [API Reference](#api-reference)
14. [Configuration](#configuration)
15. [Troubleshooting](#troubleshooting)

---

## Getting Started

The Trading Bot Control Plane is a web-based dashboard for managing automated trading strategies on IG Markets (spread betting) and IBKR (stocks/ETFs). It supports paper trading, shadow mode (signal logging without execution), and live trading.

### First-Time Setup

1. Copy `.env.example` to `.env` and fill in your broker credentials:
   - `BROKER_MODE` - Set to `paper` (no broker needed), `demo` (IG demo account), or `live`
   - `IG_USERNAME`, `IG_PASSWORD`, `IG_API_KEY` - Your IG API credentials from https://labs.ig.com/
   - `IG_ACC_TYPE` - `DEMO` or `LIVE`
   - `IG_ACC_NUMBER` - Your spread bet account number

2. The application starts automatically and is accessible via the web preview.

3. The bot defaults to **paper mode** with the engine **stopped** - no real trades will be placed until you explicitly start the engine in live mode with valid broker credentials.

---

## Dashboard Overview

The interface uses a sidebar on the left for navigation between five pages:

| Icon | Page | Purpose |
|------|------|---------|
| Grid | Overview | System health, equity chart, risk briefing |
| Chart | Trading | Execution controls, risk intervention, positions |
| Monitor | Research | Strategy development, backtesting, calibration |
| Warning | Incidents | Audit trail, event logs, job monitoring |
| Gear | Settings | Configuration overrides |

The top header bar shows real-time status badges:
- **Engine state** (Running/Stopped)
- **Mode** (Shadow/Live)
- **Kill Switch** (On/Off)
- **Throttle** percentage
- **Open positions** count
- **Active cooldowns** count
- **Latest incident** (if any)

All panels update automatically every 5-30 seconds via background polling.

---

## Overview Page

The Overview page (`/overview`) is your main cockpit for monitoring the system at a glance.

### Status Cards

Four cards across the top show:
- **Engine** - Current state (Running/Stopped/Paused) and mode (shadow/live)
- **Kill Switch** - Whether trading is globally blocked, plus throttle percentage
- **Open Spreads** - Number of active positions and cooldown count
- **Total P&L** - Cumulative profit/loss in GBP, with today's figure

### Portfolio Equity Chart

An interactive chart showing your fund's Net Asset Value (NAV) over the last 90 days. The chart auto-refreshes every 30 seconds. You can:
- Hover to see exact values at any point
- Use the crosshair to compare dates
- The chart auto-scales to fit your data

### Risk Briefing

A summary panel showing:
- **Fund NAV** - Current total value
- **Day P&L** - Today's profit or loss
- **Drawdown** - Current drawdown from peak
- **Gross/Net Exposure** - Total and directional market exposure
- **Cash Buffer** - Available capital
- **Open Risk** - Total risk from open positions
- **Alerts** - Actionable warnings (e.g., "No ledger data available")

### Quick Actions

One-click buttons for common operations:
- **Start Shadow** - Begin signal monitoring without placing trades
- **Start Live** - Begin real trading (requires valid broker credentials)
- **Stop** - Halt the engine
- **Pause / Resume** - Temporarily suspend and resume
- **Run Scan** - Trigger a one-off market scan
- **Reconcile** - Compare local records against broker positions

### Additional Panels

- **Recent Events** - Latest system actions and signals
- **Background Jobs** - Status of running tasks (backtests, scans, calibrations)
- **Portfolio Analytics** - Sharpe ratio, volatility, win rate, worst drawdowns

---

## Trading Page

The Trading page (`/trading`) is your active execution and risk management hub.

### Execution Controls

The same Start/Stop/Pause/Resume buttons as Overview, plus:
- **Scan Now** - Forces an immediate market scan cycle
- **Reconcile** - Runs a position reconciliation against the broker

### Risk Controls

#### Kill Switch
A global emergency stop for all trading. When enabled:
- No new positions will be opened
- Existing positions are NOT automatically closed (you must close them manually)
- The switch persists across restarts

To use: Click **Enable Kill Switch**, optionally provide a reason. Click **Disable Kill Switch** to resume.

#### Risk Throttle
Scales all position sizes by a percentage. For example:
- 100% = full size (default)
- 50% = half size on all new trades
- 10% = minimum position sizes

Use the slider or input a percentage (10-100%), then click **Set Throttle**.

#### Market Cooldowns
Block trading on specific tickers for a set period. Useful after unexpected moves or broker issues.
- Enter a ticker (e.g., `SPY`), duration in minutes, and optionally a reason
- Click **Set Cooldown**
- Active cooldowns appear in the cooldown list with a **Clear** button

### Position Management

#### Open Positions Table
Shows all active option spreads and positions with:
- Ticker and trade type
- Entry price and current size
- Maximum potential loss
- Time open

#### Manual Close
Close a specific position by entering either:
- A **Spread ID** (for option spreads), or
- A **Ticker** (closes all positions for that ticker)

### Reconcile Report
After running a reconciliation, this panel shows any discrepancies between your local database records and what the broker reports, including:
- Orphaned positions (in broker but not in local records)
- Stale positions (in local records but not at broker)
- Size mismatches

---

## Research Page

The Research page (`/research`) provides tools for strategy development and parameter tuning.

### Option Discovery
Triggers a background job that searches the broker for available option contracts. Results are stored in the database for analysis. Use this to find tradeable options for new strategies.

### Calibration Runs
Compares IG broker option prices against Black-Scholes theoretical values. This helps you understand the pricing ratio between the model and actual market prices, which is essential for the credit spread strategy.

To start a calibration:
1. Click **Run Calibration**
2. The job runs in the background
3. Results appear in the calibration table showing pricing ratios per instrument

### Strategy Parameter Lab

This is where you manage strategy configurations across environments.

#### Parameter Sets
Each strategy can have multiple parameter sets (e.g., different RSI thresholds, entry levels). You can:
- **Create** a new parameter set with custom JSON overrides
- **View** existing parameter sets and their performance history
- **Promote** a set through the pipeline: Shadow -> Staged-Live -> Live

#### Promotion Gate
Before a parameter set can move from shadow to live, it must pass safety checks:
- Minimum shadow period (cooling-off time)
- Minimum number of shadow trades
- Performance thresholds (positive Sharpe, acceptable drawdown)

The gate report shows which criteria are met and which are blocking promotion.

### Backtest Dashboard
View results of historical backtest runs, including:
- Strategy name and status (running/completed/failed)
- Total return percentage
- Sharpe ratio
- Maximum drawdown
- Number of trades and win rate

To submit a new backtest, use the API endpoint `POST /api/backtest` with the strategy name, date range, and tickers.

---

## Incidents & Jobs Page

The Incidents page (`/incidents`) is your operational audit trail.

### Incident Log
A chronological list of system issues such as:
- Broker disconnections
- Order rejections
- Policy violations (e.g., exceeding risk limits)
- Webhook authentication failures

Each incident shows a timestamp, severity, and description.

### Event Log
A general record of all bot actions:
- Signal generation events
- Order creation and execution
- Engine lifecycle changes (start, stop, pause)
- Control actions (kill switch, throttle changes)

### Job Monitor
Track background tasks with their:
- Job type (backtest, discovery, calibration, scan, reconcile)
- Current status (queued, running, completed, failed)
- Creation and completion times
- Result payload (click to expand)

### Live Log Tail
A scrolling view of the most recent log output from the control plane process. Useful for debugging issues in real time.

---

## Settings Page

The Settings page (`/settings`) lets you modify runtime configuration without editing files.

### Editable Settings

#### Broker Section
- **Broker Mode** - Switch between `paper`, `demo`, and `live`
- **Trading Mode** - Switch between `shadow` (signals only) and `live` (execution)

#### Risk Limits Section
- **Initial Capital** - Starting balance for sizing calculations (GBP 100 - 10,000,000)
- **Default Stake/Point** - Base stake per point for spread bets (0.01 - 1000)
- **Max Open Positions** - Concurrent position limit (1 - 100)
- **Max Exposure %** - Total portfolio exposure cap (1 - 100%)

#### IBS Strategy Parameters
- **IBS Entry Threshold** - IBS value required for entry (lower = more oversold, default 0.3)
- **IBS Exit Threshold** - IBS value required for exit (higher = more overbought, default 0.7)
- **RSI Filter** - Toggle the RSI(2) confirmation filter
- **RSI Period** - Lookback period for RSI (default 2)
- **RSI Entry/Exit Thresholds** - RSI levels for entry (default < 25) and exit (default > 65)
- **EMA Period** - Trend filter period (default 200)

#### Notifications Section
- **Notifications Enabled** - Master toggle for all alerts
- **Email To** - Destination email address
- **Telegram Chat ID** - Telegram bot chat ID for alerts

### Saving Changes
Click **Save Settings** at the bottom of the form. Changes are saved to `.runtime/settings_override.json` and take effect after the next engine restart. A confirmation or error message appears inline.

### Operational Reference
The bottom panels show read-only system information:
- Health and status API endpoints
- Process log and PID file paths
- Database file location
- Useful shell commands

---

## Command Palette

Press **Ctrl+K** (or click the search icon in the sidebar) to open the Command Palette. This provides quick access to common actions:

Type a command or navigate with arrow keys and Enter:
- `start shadow` / `start live` - Start the engine
- `stop` - Stop the engine
- `pause` / `resume` - Pause or resume
- `kill switch` - Toggle the kill switch
- `scan` - Run a market scan
- `reconcile` - Run position reconciliation
- `overview` / `trading` / `research` / `incidents` / `settings` - Navigate to pages

Press **Escape** to close the palette.

---

## Operating Modes

The bot has two independent mode dimensions:

### Broker Mode
Controls which broker backend is used:
| Mode | Description |
|------|-------------|
| `paper` | Simulated broker, no real orders. Safe for testing. |
| `demo` | IG demo account with real market data but virtual money. |
| `live` | Real money trading on IG. Use with caution. |

### Trading Mode
Controls whether signals result in actual orders:
| Mode | Description |
|------|-------------|
| `shadow` | Signals are generated and logged but no orders are placed. Useful for evaluating strategy performance before going live. |
| `live` | Signals result in real order placement on the configured broker. |

### Recommended Progression
1. Start with `paper` + `shadow` to verify signals make sense
2. Move to `demo` + `shadow` to see how signals align with real market data
3. Move to `demo` + `live` to test order execution with virtual money
4. Finally, `live` + `live` for real trading (start with small position sizes)

---

## Risk Controls

### Safety Hierarchy
The system enforces multiple layers of protection before any trade is placed:

1. **Kill Switch** - Global block on all new entries (operator-controlled)
2. **Risk Throttle** - Scales all position sizes (operator-controlled)
3. **Market Cooldowns** - Per-ticker trading blocks (operator-controlled)
4. **Pre-Trade Risk Gate** - Automatic checks:
   - Total portfolio heat limit
   - Single position risk cap
   - Correlated exposure limits
   - Sleeve-level position caps
5. **Promotion Gate** - Strategies must pass performance checks before going live
6. **Safety Controller** - Hard-coded limits that cannot be overridden:
   - Maximum risk per trade (default 2% of capital)
   - Maximum total heat (default 4% of capital)
   - Daily loss circuit breaker

### Kill Switch Best Practices
- Enable during market events you don't want to trade through (e.g., FOMC, NFP)
- Enable if you notice unexpected broker behavior
- Always provide a reason so you remember why you enabled it
- The switch does NOT close existing positions - manage those separately

---

## Strategy Reference

### IBS++ Mean Reversion (Primary)
The core strategy. Buys oversold markets and sells overbought ones.
- **Entry**: IBS below 0.3 AND RSI(2) below 25 AND price above EMA(200)
- **Exit**: IBS above 0.7 OR RSI(2) above 65
- **Markets**: SPY, QQQ, Gold, Oil, GBP/USD (via IG spread betting)
- **Direction**: Long only (for indices and commodities)
- **VIX Filter**: Skips trades when VIX > 35 (extreme volatility)

### IBS Short (Bear Regime)
Short-side mean reversion that earns overnight financing (SONIA rate) as a tailwind.
- **Entry**: IBS above 0.7 AND RSI(2) above 75 AND bearish regime
- **Direction**: Short only

### IBS Credit Spreads (Options)
Uses option credit spreads to capture IBS signals without overnight financing drag.
- **Entry**: Same IBS signal, then finds OTM put/call spreads on IG
- **Sizing**: Risk-limited to max 2% per trade, 4% total heat
- **Management**: Monitors for expiry and early exit opportunities

### Dual Momentum GEM (IBKR)
Academic momentum strategy for tax-advantaged accounts (ISA).
- **Monthly rebalance** between US stocks, international stocks, and bonds
- **Uses**: SPY, VEU, BND via IBKR

### GTAA Trend Following (IBKR)
Multi-asset trend following based on Meb Faber's model.
- **Monthly rebalance** across stocks, bonds, real estate, commodities, gold
- **Rule**: Only hold assets trading above their 10-month moving average

---

## Webhooks & External Signals

### TradingView Integration
The bot accepts signals from TradingView alerts via webhook:

**Endpoint:** `POST /api/webhooks/tradingview`

**Authentication:** Include your webhook token in the JSON payload:
```json
{
  "token": "your_webhook_token",
  "ticker": "SPY",
  "action": "BUY",
  "strategy": "ibs",
  "price": 520.50
}
```

The token is configured via the `TRADINGVIEW_WEBHOOK_TOKEN` environment variable.

Incoming webhooks are validated, logged, and converted to order intents that flow through the normal risk pipeline.

---

## API Reference

### Health & Status
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Simple health check |
| `/api/status` | GET | Full engine status, summary, open positions |
| `/api/broker-health` | GET | Broker connection health and capabilities |
| `/api/log-tail?lines=200` | GET | Recent process log output |

### Control Actions
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/actions/start` | POST | Start engine (form field: `mode=shadow` or `mode=live`) |
| `/api/actions/stop` | POST | Stop engine |
| `/api/actions/pause` | POST | Pause engine |
| `/api/actions/resume` | POST | Resume engine |
| `/api/actions/scan-now` | POST | Trigger market scan |
| `/api/actions/reconcile` | POST | Trigger position reconciliation |
| `/api/actions/kill-switch-enable` | POST | Enable kill switch (form: `reason`) |
| `/api/actions/kill-switch-disable` | POST | Disable kill switch |
| `/api/actions/risk-throttle` | POST | Set throttle (form: `pct`, `reason`) |
| `/api/actions/cooldown-set` | POST | Set cooldown (form: `ticker`, `minutes`, `reason`) |
| `/api/actions/close-spread` | POST | Close position (form: `spread_id` or `ticker`) |

### Data & Monitoring
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/events` | GET | Bot event log |
| `/api/jobs` | GET | Background job list |
| `/api/jobs/{job_id}` | GET | Job details |
| `/api/incidents` | GET | Incident log |
| `/api/order-intents` | GET | Order intent history |
| `/api/order-intents/{id}` | GET | Intent lifecycle detail |
| `/api/reconcile-report` | GET | Latest reconciliation report |
| `/api/charts/equity-curve?days=90` | GET | Equity curve data (JSON) |
| `/api/stream/events` | GET | Server-Sent Events stream |

### Research & Analytics
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/strategy/parameter-sets` | GET | List parameter sets |
| `/api/strategy/promotions` | GET | Promotion history |
| `/api/strategy/promotion-gate` | GET | Promotion eligibility check |
| `/api/signal-shadow` | GET | Shadow signal report |
| `/api/execution-quality` | GET | Execution quality analytics |
| `/api/calibration/runs` | GET | Calibration run history |
| `/api/analytics/portfolio` | GET | Portfolio metrics |
| `/api/backtest` | POST | Submit backtest job |
| `/api/settings` | GET | Current settings |
| `/api/settings` | POST | Update settings |

---

## Configuration

### Environment Variables (.env)
| Variable | Default | Description |
|----------|---------|-------------|
| `BROKER_MODE` | `paper` | Broker backend: `paper`, `demo`, `live` |
| `IG_USERNAME` | | IG Markets username |
| `IG_PASSWORD` | | IG Markets password |
| `IG_API_KEY` | | IG REST API key |
| `IG_ACC_TYPE` | `DEMO` | `DEMO` or `LIVE` |
| `IG_ACC_NUMBER` | | Spread bet account number |
| `NOTIFY_EMAIL` | | Email for trading alerts |
| `TELEGRAM_TOKEN` | | Telegram bot token |
| `TELEGRAM_CHAT_ID` | | Telegram chat ID for alerts |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING` |
| `TRADINGVIEW_WEBHOOK_TOKEN` | | Authentication token for TradingView webhooks |

### Runtime Overrides
Settings changed via the Settings page are saved to `.runtime/settings_override.json` and take precedence over environment variables. These persist across restarts.

### Key Files
| File | Purpose |
|------|---------|
| `.env` | Broker credentials and secrets |
| `config.py` | All strategy parameters and market mappings |
| `.runtime/settings_override.json` | UI-configured overrides |
| `trades.db` | SQLite database with all trades, events, and positions |
| `trades.csv` | CSV export of trade history |
| `.runtime/control_plane.log` | Process log file |

---

## Troubleshooting

### Engine Won't Start
- Check that `BROKER_MODE` is set correctly in `.env` or Settings
- For `demo`/`live` modes, verify your IG credentials are valid
- Check the log tail on the Incidents page for error details
- Ensure the kill switch is not enabled

### No Signals Generated
- Confirm the engine is running (not just started - check the status badge)
- IBS signals only fire near market close times (17:05 UK for EU, 21:15 UK for US)
- The VIX filter may be blocking trades if VIX > 35
- Check that the EMA trend filter isn't blocking (price must be above EMA 200)

### Positions Not Closing
- The kill switch blocks new entries but does NOT auto-close positions
- Use the Manual Close feature on the Trading page
- Check the Reconcile Report for position mismatches

### Webhook Alerts Not Working
- Verify `TRADINGVIEW_WEBHOOK_TOKEN` is set in `.env`
- Check the Incidents page for "webhook rejected" entries
- Ensure the payload includes the correct `token` field
- Maximum payload size is 64KB

### Settings Not Taking Effect
- Runtime overrides require an engine restart to take effect
- Check the Settings page for validation errors after saving
- Verify `.runtime/settings_override.json` exists and contains your changes

### Equity Chart Not Loading
- The chart requires NAV data in the database
- Run the engine in shadow mode for a few days to accumulate data
- Check browser console for JavaScript errors
