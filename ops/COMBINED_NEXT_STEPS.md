# Combined Next Steps — Post-Review Action Plan

**Sources:** Claude Code spec review, Codex build self-assessment, Replit Agent 3 operational review
**Date:** 2026-03-09
**Status:** All three reviewers agree on priorities. This plan merges their findings.

---

## Context

The research system is architecturally complete: 24 tranches, 2500+ tests green, both engines built, L9 integrated, stage-aware scoring landed. The remaining work falls into two categories:

1. **Spec completion** — undelivered backlog items (Claude's review)
2. **Operational readiness** — making the system actually run on real data (Replit's review)

Both matter, but Replit is right that operational readiness is the higher priority. A perfectly spec-compliant system that can't run is less valuable than a 90%-complete system processing real data.

---

## Phase 1: Operational Unblocking (Do First)

These are the deployment-level blockers Replit identified. Nothing else works until these are done.

### 1.1 Provision PostgreSQL on Replit
- The entire research artifact store, market data layer, model call audit, and feature cache require PostgreSQL
- Without it, Engine A, Engine B, and the artifact chain viewer are all dead code
- **Action:** Provision a Replit PostgreSQL instance, set `RESEARCH_DB_DSN` in `.env`, run `init_research_schema()`
- **Owner:** User (infrastructure provisioning)
- **Effort:** 15 minutes

### 1.2 Wire Real Backtester to Experiment Service
- `ExperimentService._default_backtest_runner()` returns hardcoded stub data
- `analytics/backtester.py` already has walk-forward, Monte Carlo, cost modelling, and IG spread assumptions
- **Action:** Write an adapter that wraps `Backtester.run()` output into the `VariantResult` format the experiment service expects. Inject it as `backtest_runner` in the runtime wiring.
- **Key file:** `research/runtime.py` (wire adapter), new adapter in `research/shared/backtest_adapter.py`
- **Effort:** ~100 lines, 1 tranche

### 1.3 Seed Market Data via yfinance
- Engine A needs historical bars to compute signals (EWMA, z-scores, momentum)
- The IBKR adapter already falls back to yfinance — this works for equities/ETFs
- **Action:** Run `seed_mvp_universe()` then ingest 3-5 years of data for the MVP universe (ES, NQ, SPY, TLT, GLD, CL, etc.) via the IBKR/yfinance adapter
- **Effort:** Script + one-time run, 30 minutes

---

## Phase 2: End-to-End Validation (Prove It Works)

Once PostgreSQL is up and data is flowing, validate both engines end-to-end.

### 2.1 Run Engine A on Historical Data
- Feed 3-5 years of SPY/TLT/GLD/commodity data through the full pipeline
- Verify: regime classification → signal computation → portfolio construction → rebalance sheet → artifact chain
- Check the numbers make sense (are signals bounded [-1,+1]? do regime factors scale correctly? are costs realistic?)
- **This is the "does Engine A produce credible output" test**

### 2.2 Run One Full Engine B Cycle
- Feed a real event (earnings surprise, macro data release, or manual text input)
- Watch it flow: intake → signal extraction → hypothesis → challenge → scoring → stage routing
- Verify: does a high-quality signal score >70 and advance to TEST? do blocking objections correctly PARK?
- **This is the "does Engine B produce credible output" test**

### 2.3 Pilot Sign-Off Workflow
- Codex flagged this as the remaining gap: `requires_human_signoff` is set but no endpoint exists
- **Action:** Add `POST /api/actions/research/pilot-approve` and `POST /api/actions/research/pilot-reject` endpoints
- Connect to promotion gate so approved pilots can proceed to paper trading
- **Effort:** ~1 tranche

---

## Phase 3: Spec Completion (Fill Gaps)

These are the undelivered backlog items from Claude's review. Lower priority than operational readiness but needed for completeness.

### 3.1 INT-3: E2E Integration Tests
- 7 scenarios, ~15 tests covering full flows with mocked LLM responses
- Now more valuable because Engine B is stage-aware — test all three stage paths
- **Effort:** 1 tranche

### 3.2 Missing Operator Action Endpoints (UX-9 partial)
- `POST /api/actions/research/confirm-kill`
- `POST /api/actions/research/override-kill`
- `POST /api/actions/research/execute-rebalance`
- `POST /api/actions/research/dismiss-rebalance`
- **Effort:** 1 tranche

### 3.3 Remaining UX Surfaces (if time permits)
- UX-3: Hypothesis board kanban + review queue
- UX-4: LLM cost monitoring views
- UX-5: Strategy health grid
- UX-7: Chart JSON endpoints (regime timeline, signal history, weights)
- UX-11: Top bar KPIs
- **Effort:** 2-3 tranches

---

## Phase 4: Paper Trading (The Goal)

### 4.1 Paper Trade the Winner
- Whichever engine produces credible, cost-adjusted positive expectancy from Phase 2 — run it on IG Demo
- Engine A is more likely to be first (deterministic, no LLM dependency, just needs data)
- Engine B needs LLM API keys provisioned and a few real events to process

### 4.2 Futures Data Source
- Replit correctly noted: Norgate and Barchart adapters are stubs
- For Engine A's primary domain (futures), yfinance is limited
- **Near-term:** Use yfinance for ETF proxies (SPY instead of ES, TLT instead of ZN)
- **Medium-term:** Evaluate Databento, Polygon, or FirstRate Data for real futures bars

---

## Resolved Issues (No Longer on the List)

| Issue | Resolution |
|-------|-----------|
| sizing_factor floor ge=0.0 | Fixed by Codex — now ge=0.5 |
| 3 scoring tiers not 5 | Fixed by Codex — ProgressionStage (TEST/EXPERIMENT/PILOT) with stage-aware routing |
| L9 regression (8→9 layers) | Fixed by Codex in tranche 24 — uses len(LAYER_ORDER) |
| next_stage not driving flow | Fixed by Codex — pipeline, promotion gate, and UI all stage-aware |

---

## Priority Order

```
1. Provision PostgreSQL                    [User, 15 min]
2. Seed market data via yfinance           [Codex, 30 min]
3. Wire backtester to experiment service   [Codex, 1 tranche]
4. Run Engine A on historical data         [Validate manually]
5. Run Engine B full cycle                 [Validate manually]
6. Pilot sign-off endpoints                [Codex, 1 tranche]
7. INT-3 E2E tests                         [Codex, 1 tranche]
8. Missing action endpoints                [Codex, 1 tranche]
9. Paper trade on IG Demo                  [User + system]
10. Remaining UX polish                    [Codex, 2-3 tranches]
```

Items 1-3 unblock everything. Items 4-5 prove it works. Items 6-8 complete the spec. Item 9 is the goal.
