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

### 3.4 Candidate UX Backlog Item (needs discussion + expansion)
- Research workflow shell rewrite: promote research intake, active chains, and decision queue to the primary operator surface; demote legacy discovery/calibration tools; replace card-grid fragments with an explicit `intake -> processing -> decision` flow; separate default operator UX from expandable debug/raw-artifact panels.
- **Status:** Prototype in progress. The workflow shell, operating summary, queue/workbench loop, and chain-viewer decision context are now on the research page, but product discussion, IA/user-flow expansion, and acceptance criteria are still needed before this can be treated as a finished ticket.
- **Reason:** Backend workflow changed substantially, but the current UI still reads as the old dashboard with extra cards, so the new research system does not feel materially different in day-to-day use.
- **Prototype progress so far:**
  - Research page now leads with intake, operating summary, active chains, decision queue, workbench, and archive before legacy labs/diagnostics.
  - Opening a chain now syncs the workbench and exposes lifecycle, operator posture, next move, and review/rebalance action context in one surface.
  - Manual Engine B intake and review/rebalance actions now render into the research workbench instead of generic toast output.
  - The operating summary and workbench now expose lane-level pressure and focus across review, pilot, rebalance, and flow instead of treating all operator activity as one undifferentiated queue.
  - The decision queue itself now spans review acknowledgements, pilot sign-offs, and rebalance calls instead of only surfacing decay-review items.
  - Queue lane focus is now navigable from the operating summary and persists across queue refreshes, so operators can stay in a single lane instead of being dropped back into the generic queue state.
  - The selected chain is now visually highlighted across the main research surfaces, so the current chain stays obvious while Active Chains, Queue, Summary, and Archive fragments refresh.
  - Selected chain and queue-lane state now mirror into the `/research` URL as well as session storage, so a full page reload preserves the operator’s current focus instead of dropping back to a generic landing state.
  - The research page now rewrites its initial HTMX targets from the selected-chain URL state before the first fragment load, so deep links and reloads boot directly into the chosen chain instead of briefly flashing placeholder workbench content.
  - The research shell and alerts fragment now carry the selected queue lane server-side as well, so first paint already shows the correct queue chip state and visible lane sections before client-side restoration runs.
  - The client now keeps `hx-get` targets for the research shell, alerts queue, focus ribbon, chain viewer, and workbench in sync with the current selected chain and lane, so periodic HTMX refreshes no longer drift back to the initial deep-link state after the operator changes context.
  - The decision queue now shows an explicit “following selected chain” banner when a chain is in focus, and the selected-chain banner state is no longer mixed into cached alert data.
  - Queue-banner and selected-chain queue state now refresh immediately when lane sync or `Clear Focus` runs, instead of waiting for the next timed alerts refresh.
  - The queue’s selected-chain banner now includes ticker, posture, latest artifact, and next move, so the operator can understand why the queue is in its current lane without leaving the queue itself.
  - The workbench now detects when the selected chain’s preferred lane and the current queue filter drift apart, and exposes a one-click queue resync action instead of leaving that mismatch implicit.
  - Workbench follow-up and return-to-queue actions now carry the matching `Active Chains` slice too, so post-action handoff stays aligned across the queue, workbench, and active board instead of only preserving queue lane.
  - The shared queue/board-focus helper now commits both states before refreshing the workbench, and focus-ribbon auto-sync now applies its board-slice metadata too, eliminating a stale-slice race where the workbench could refresh against the old board view.
  - Research action results now preserve the operator’s current queue lane and offer a queue-preserving `Return to Queue` path, so finishing a chain does not force the operator back to `All Lanes`.
  - After an action result, the workbench can now nominate the next queued chain in the active lane and open it directly, reducing the dead time between one operator decision and the next.
  - When a lane is cleared, the workbench now says so explicitly and points to the next non-empty lane or intake instead of silently dropping back to a generic result state.
  - The idle workbench now acts as a guided queue-entry surface, surfacing the next suggested chain or a clear-queue/intake prompt even before any chain is selected.
  - The focus ribbon now recommends the next actionable queue item ahead of the latest active chain when operator work is already waiting, keeping the page’s top-level guidance aligned with queue urgency.
  - The operating summary’s recommendation card now follows the same queue-priority model, so the summary, ribbon, and workbench all point at the same next actionable item instead of disagreeing with each other.
  - The operating summary now also drives the matching `Active Chains` slice alongside queue focus, so summary recommendations and lane cards keep the board, queue, and workbench aligned instead of leaving the top summary on a different slice from the board below it.
  - The active-chains board now breaks operator-ready pilot/review work out from the generic in-flight flow, so chains needing a human call are no longer buried inside a flat recency list.
  - The non-operator side of the active-chains board is now grouped into explicit formation, challenge, decision, experiment, and follow-up lanes, so the processing flow reads like a pipeline instead of a single recency feed.
  - The active board now also surfaces a flow-focus recommendation and lane-level quick-open actions, so operators can jump straight into the most backed-up non-operator lane instead of scanning every in-flight card manually.
  - The operator side of the active board now has its own lane summaries and operator-focus recommendation, so pilot and review handoff chains also expose a clear “open this next” path before the user drops into the queue or full workbench.
  - The active-chains panel now resolves those lane recommendations into a single board-level focus card, so the surface itself can tell the operator what to open first instead of forcing a comparison between the operator and flow halves.
  - That board-level focus card now also carries a second-step handoff when both halves are active, so the operator can move from the primary chain to the next flow backlog without rescanning the board.
  - The active-chains board now has a persisted local view mode for `All`, `Board Focus`, `Operator`, `Flow`, and `Stale`, so operators can keep the panel narrowed to the slice they care about even as HTMX refreshes swap the fragment.
  - That active-board view mode now also mirrors into the `/research` URL and fragment request targets, so reloads and deep links preserve the chosen board slice instead of only recovering it from client storage after the page settles.
  - Filtered active-board modes now show a visible slice banner and explicit empty state, so narrowing the board to `Board Focus`, `Operator`, `Flow`, or `Stale` no longer leaves the operator guessing whether the panel is filtered or simply empty.
  - Filtered board modes now also have local first/previous/next navigation through visible chains, so an operator can step through the current slice without rescanning the card grid after every chain open.
  - When a filtered board slice hides the chain that is still selected in the workbench, the board now shows a visible mismatch warning with a one-click `Show Selected Chain` recovery action instead of silently dropping that context.
  - That hidden-selection recovery now switches the board to the narrowest slice that contains the selected chain, so revealing the workbench’s current chain no longer blows the board back out to `All`.
  - The active-board fragment now receives the selected chain on the server side as well, so the hidden-selection warning and targeted reveal action can render correctly on first paint instead of appearing only after client-side state restoration.
  - The filtered slice-navigation banner now also renders its selected-position and button-disabled state from the initial server response, so the board no longer flashes generic navigation copy before client-side selection restoration runs.
  - Latest review/rebalance demands now outrank older pilot-readiness state in the shared chain context, so the page reflects the most recent operator obligation instead of stale pilot prompts.
  - The chain viewer now has explicit timeline navigation and “current artifact” emphasis, so operators can jump through the chain without treating the lineage pane as a raw scroll dump.
  - The chain viewer now promotes a dedicated current-artifact snapshot and tucks prior lineage plus timeline navigation behind an explicit `Lineage History & Debug` disclosure, so the default surface answers “where is this chain now?” before exposing debug history.
  - Multi-artifact workbench action results now surface the latest saved artifact inline and collapse secondary artifacts behind `Additional Updated Artifacts`, reducing post-action scroll noise.
  - The archive now has a persistent `History Lens` control row for `All`, `Completed`, `Syntheses`, `Post-Mortems`, and `Retirements`, so operators can move through closed-loop slices without rebuilding the rest of their archive filters each time.
  - The decision queue now promotes a `Next Decision` banner when nothing is selected, pointing the operator to the next actionable review, pilot, or rebalance item in the current lane before they scan the full queue.
  - The workbench header now has a persistent current-focus ribbon, so the selected chain’s posture, lane, and next move stay visible even before the operator dives into the timeline or action pane.
  - The focus ribbon now exposes action-readiness state and fast-path actions for pilot, rebalance, and synthesis cases, while still routing multi-path review acknowledgements into the full workbench.
  - The focus ribbon now also carries the matching `Active Chains` slice and steers queue focus plus board focus together, so the page header, queue, and active board no longer drift apart when the operator follows the ribbon’s recommendation.
  - Selecting a chain now auto-syncs the decision queue to that chain’s active lane via the focus ribbon, so queue context stops drifting away from the selected work item.
  - The focus ribbon now also has an explicit `Clear Focus` action, so the operator can intentionally leave a selected chain and return the workbench/timeline to a generic queue state without editing the URL by hand.
  - `Clear Focus` now resets the queue back to `All Lanes` and refreshes the queue fragment immediately, so selected-chain queue banners do not linger until the next timed HTMX refresh.
  - `Clear Focus` now also resets the active board slice back to `All`, so it behaves as a true return to the generic operator surface instead of leaving the board stranded in a narrow slice like `Operator` or `Stale`.
  - Archive, recent decisions, and Engine A portfolio-expression panels now open chains with explicit source context, so history clicks land in the neutral archive shell and regime/signal clicks land in rebalance context instead of inheriting stale queue or board state.
  - The remaining selected-chain and workbench reopen paths now also carry explicit queue-lane and board-slice arguments, so queue banners, ribbon recommendations, summary recommendations, `Next Up`, and `Refresh Chain` actions no longer depend on ambient remembered state to reopen the correct context.
  - Active-board slice navigation now preserves each visible card’s natural queue lane while keeping the current board slice, so `Open First Visible`, `Previous Visible`, and `Next Visible` stop reopening chains into stale queue context.
  - Hidden-selection recovery and non-URL session restore now also reapply the selected chain’s queue lane and board slice, so `Show In Matching Slice` and remembered-chain reloads no longer bounce back through generic reopen paths.
  - Changing the `Active Chains` board slice now refreshes the workbench immediately, so its board-slice state and queue-return actions no longer lag behind the operator’s current board filter until some later action or poll.
  - The focus ribbon now receives the current queue lane and board slice too, so its visible queue/board cards reflect the operator’s actual state instead of only the chain’s preferred lane/slice while still keeping the preferred focus metadata for auto-sync actions.
  - Manual queue-lane, board-slice, and combined queue+board changes now refresh the focus ribbon too, so the header no longer lags behind the rest of the shell after the operator changes lane or slice without opening a new chain.
  - Ribbon auto-sync suppression is now request-scoped on manual queue or board changes, so refreshing the header no longer silently snaps the operator back to the chain’s preferred lane/slice and the skip cannot be consumed by an unrelated later ribbon swap.
  - Queue-lane and board-slice helpers now short-circuit on no-op clicks, so clicking the already-active lane or slice no longer triggers redundant ribbon/workbench refreshes and visible shell flash.
  - Focus-ribbon auto-sync now also short-circuits when the shell is already on the ribbon’s preferred lane/slice, so benign ribbon swaps no longer trigger redundant queue and workbench refreshes.
- **Discussion prompts:**
  - Should the decision queue merge decay reviews, pilot sign-offs, and rebalance approvals into one lane or keep them visually separated?
  - When an operator opens a chain, should the workbench auto-focus the matching action panel and preserve that selection across refreshes?
  - Which archive slices actually matter day to day: completed chains, syntheses, post-mortems, retirements, or a different grouping?
  - What should count as “system activity” for an operator: queue depth, freshest chain update, last successful engine run, or all of the above?
  - Should review-trigger chains and rebalance chains stay in a shared workbench lane, or split into dedicated tabs once queue volume increases?
- **Draft acceptance criteria:**
  - Above the fold shows `intake -> active chains -> decision queue` before any legacy tooling.
  - Opening a chain makes its stage progression and next operator move obvious without reading raw JSON.
  - Operator actions can be completed from one workbench without hunting across cards, including review acknowledgements and rebalance execution/dismissal.
  - Archive search feels like a closed-loop history surface, not a generic list dump.
  - Legacy labs and diagnostics remain available, but are clearly secondary.

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
1. Provision PostgreSQL                    [DONE — schema initialized, 2026-03-10]
2. Seed market data via yfinance           [DONE — 89 instruments, 111,956 bars, 2026-03-10]
3. Wire backtester to experiment service   [DONE — ResearchBacktestAdapter already wired in runtime.py]
4. Run Engine A on historical data         [DONE — DB-backed validation ok, 3 artifacts, 2026-03-10]
5. Run Engine B full cycle                 [DONE — live validation ok, scored/PARK, 4 artifacts, 2026-03-10]
6. Pilot sign-off endpoints                [DONE — tranche 30, 2026-03-09]
7. INT-3 E2E tests                         [DONE — 12 tests, 7 scenarios, 2026-03-10]
8. Missing action endpoints                [DONE — tranche 31, 2026-03-09]
9. Minimal-stake live trade on IG          [DONE — user confirmed no demo account is available; live IG smoke open/close and live dispatcher smoke both passed at 0.01 stake on US 500, the Engine A research execute->dispatch->same-session close path passed live at 0.01 stake on NQ->QQQ, and the bounded six-symbol Engine A live batch (CL/GC/HG/NG/QQQ/IWM) passed after stop-policy + dispatcher/session reuse fixes, with account flat afterward, 2026-03-10]
10. Remaining UX polish                    [DONE — research workflow shell now defaults to current-state-first chain viewing, compressed workbench action results, archive history lenses, and queue-level next-decision guidance, 2026-03-10]
```

Items 1-10 complete.

## Post-Completion Operational State

- Latest live execution hardening landed in:
  - `97af3d2` — disable implicit IG protective stops by default
  - `92504e4` — detect live Engine A position mismatches after dispatch
  - `cd82bf6` — reuse connected broker sessions during multi-intent dispatch and fail the CLI when any queued intent is left retrying/partial
  - `1fa15aa` — map fresh-session IG positions back to configured tickers
  - `0deba3f` — persist IG deal mappings across reconnects via local open-position state
  - `1b205a5` — add a first-class broker-to-ledger sync CLI for post-trade reconciliation
  - `256fa10` — allow Engine A live execution flows to sync the ledger inline via `--sync-ledger`
- Current validated live path on 2026-03-10:
  - single-symbol live `CL` open/hold/close passed
  - single-symbol live `GC` open/hold/close passed
  - full six-symbol live Engine A batch opened cleanly, survived the previous failure window with all 6 positions present at IG, and then flattened cleanly
  - one intentional six-symbol held live Engine A batch was opened, inspected, synced into the ledger, and then flattened cleanly
  - a later one-symbol live `NQ -> QQQ` smoke-close also passed with inline `--sync-ledger`, leaving broker + ledger flat in one run
- Current operational state:
  - live IG account flat after the held-batch round trip and later inline-sync smoke validation
  - local ledger matches the flat broker state; latest inline-sync validation also returned `ledger_position_count: 0`
  - the held-batch round trip realized approximately `-0.68` versus the pre-hold cash balance
  - full-batch live validation is complete; any further run should be treated as intentional exposure, not infrastructure proving
  - the preferred low-friction live validation command is now `python scripts/execute_engine_a_rebalance.py --mode live --symbols NQ --size-mode min --commit --dispatch --allow-live --smoke-close --sync-ledger`
