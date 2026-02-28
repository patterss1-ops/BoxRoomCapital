# Full Spectrum Finance Operating Plan

## Objective
Build a one-human, automation-first trading operation that runs:
- short-horizon automated trading
- medium/long-horizon investment strategies
- research and news intelligence pipelines
- portfolio and risk controls
- broker-agnostic execution

This plan extends the existing control plane (FastAPI + HTMX + SQLite) without throwing away current options work.

## Quality Pass: Latest Claude Draft

### Strong / Immediately Useful
- Correct direction on moving beyond IG-only execution.
- Good emphasis on IBKR for non-spreadbet products.
- Good separation of ISA/SIPP/GIA account roles.
- Includes concrete file targets and phased implementation framing.
- Keeps existing IG options work in scope.

### Gaps / Risks to Fix Before Build
1. Too many strategies launched at once:
   - 20+ strategies in parallel is operationally unsafe for a one-person desk.
2. Compliance and account-risk red flags:
   - VPN-based access paths and grey-area execution flows should not be in core production plan.
3. Insufficient operating model detail:
   - Missing decision rights, risk budget process, model approval gates, and incident response SOP.
4. Missing production constraints:
   - No clear SLOs (latency, uptime, order ack timing), no broker outage failover policy.
5. Weak evidence hygiene:
   - Performance claims are mixed with implementation decisions; each must be independently verified before capital allocation.

### Buildability Matrix
- Build now:
  - IBKR adapter (paper first), strategy routing by broker/account.
  - Preserve/extend IG options engine and current risk controls.
  - Portfolio/risk ledger upgrades, tax-lot tracking, audit trail.
  - Strategy registry with shadow/staged/live promotion gates.
- Build after discovery:
  - Smarkets adapter and paper trading loop.
  - GIA pairs/options expansion with strict guardrails.
  - News-intelligence scoring pipeline.
- Research only:
  - Polymarket/Kalshi latency arb.
  - DeFi/MEV style strategies.
  - High-frequency prediction market making.

## Target Operating Model (One Human, Many Agents)

### Core Control Loop
1. Research agents propose signals/parameter updates.
2. Risk agent validates against portfolio and hard limits.
3. Execution agent routes approved orders by broker/account.
4. Reconciliation agent verifies fills/positions/cash.
5. Oversight dashboard presents exceptions requiring human sign-off.

### System Domains
- Strategy Factory:
  - signal generation, backtest, walk-forward, shadow, promotion.
- Execution Fabric:
  - unified broker interface (IG, IBKR, CityIndex, future adapters).
- Portfolio and Fund Ops:
  - positions, cash, tax lots, PnL decomposition, exposure by strategy.
- Risk and Controls:
  - pre-trade checks, live drawdown throttles, kill switch, cooldown logic.
- Intelligence Pipeline:
  - market/news/event ingestion -> feature store -> signal inputs.
- Operator Console:
  - action center, incidents, jobs, approvals, post-trade analytics.

## Platform Integration Map

### API-First (Automatable Now)
- IG: existing adapter and strategy flows.
- CityIndex: existing adapter in repo.
- IBKR: highest-priority new adapter (paper, then limited live).
- TradingView: webhook ingestion for alerts/triggers.

### Semi-Automated (Human-in-the-Loop Until Stable Integration)
- Interactive Investor (II): treat as manual/semi-manual execution lane initially.
- Sharescope, Koyfin, PensionCraft:
  - ingest exported data/reports first
  - automate only where reliable and policy-safe.

## Architecture Priorities (Next Phases)

### Phase 5: Multi-Broker Execution Kernel
Goal: single execution contract for IG + IBKR while preserving current options workflows.
- Deliverables:
  - `broker/ibkr.py` with paper-trading support.
  - broker capability map (supports_options/supports_short/supports_spot_etf/etc).
  - account router (`ISA`, `SIPP`, `GIA`, `SPREADBET`) with policy checks.
  - order intent -> broker order translation with full audit.
- Exit criteria:
  - paper orders placed/cancelled/reconciled on IBKR from control plane.
  - no regressions in IG option spread flows.

### Phase 6: Fund Ledger + Risk Engine Upgrade
Goal: manage the operation as a portfolio of strategies, not isolated scripts.
- Deliverables:
  - strategy-level NAV, PnL, drawdown, turnover, exposure metrics.
  - hard limits: max DD, max single-strategy allocation, max gross/net leverage.
  - risk budget allocator (capital by strategy sleeve).
  - tax-lot and realized/unrealized gain tracking by account.
- Exit criteria:
  - daily risk report generated automatically.
  - automatic throttling and kill-switch behavior validated in tests.

### Phase 7: Research and Intelligence Ops
Goal: convert external intelligence into controlled, testable inputs.
- Deliverables:
  - ingestion jobs (calendar/news/symbol metadata/alerts).
  - normalized event store with provenance tags.
  - scoring pipelines for event impact and confidence.
  - experiment runner for hypothesis -> backtest -> shadow comparison.
- Exit criteria:
  - every live signal has traceable provenance and confidence.

### Phase 8: Strategy Expansion Program
Goal: broaden strategy portfolio without blowing up complexity.
- Deliverables:
  - 3-lane deployment model:
    - Core (capitalized)
    - Candidate (shadow/paper)
    - Lab (research only)
  - monthly promotion committee checklist (automated report + your sign-off).
- Exit criteria:
  - at least 2 uncorrelated live sleeves beyond current options setup.
  - promotion/demotion decisions reproducible from stored metrics.

## Immediate Backlog (Execution Order)
1. Define broker capability schema and account-routing policy.
2. Implement IBKR paper adapter and health checks.
3. Add unified order intent model and audit envelope.
4. Extend DB schema for multi-broker fills/positions/cash.
5. Add strategy-sleeve performance tables and API endpoints.
6. Implement portfolio risk budget + hard limit evaluator.
7. Build daily risk and operations briefing page in UI.
8. Add TradingView webhook intake endpoint with auth token.
9. Create research-event store with source/provenance metadata.
10. Add promotion gate report (shadow vs live benchmark comparison).

## Decision Rules (Non-Negotiable)
- No new live strategy without:
  - backtest sanity checks
  - shadow period
  - slippage/fee assumptions reviewed
  - explicit risk budget.
- No capital increase without:
  - stable reconciliation
  - incident rate below threshold
  - drawdown within policy.
- No policy-grey integrations in production lanes.

## Success Criteria
- You can run the operation from one console with one daily workflow.
- Every trade is attributable to a strategy, model version, and approval state.
- Risk incidents are detected automatically and escalated with clear actions.
- Strategy portfolio expands without losing reliability of existing options engine.
