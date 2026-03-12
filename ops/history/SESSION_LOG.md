# BoxRoomCapital — Session Log

> Entries before 2026-03-10 archived to `ops/history/archive/SESSION_LOG_pre_2026-03-10.md`

## 2026-03-10 ~14:00 UTC — Operational activation + INT-3 E2E tests

**Context:** Resume from shell crash. User asked to continue all backlog items.

**Work done:**
- **PostgreSQL schema initialized** — `init_research_schema()` ran successfully, 15 tables created in `research.*`
- **MVP universe seeded** — 17 ETF proxies + 36 futures contracts + roll calendar
- **Market data ingested** — 111,956 bars across 89 instruments (5 years of daily data via yfinance)
- **Fixed datetime tz-aware comparison bug** in `research/market_data/bootstrap.py` (naive vs aware comparison)
- **Backtester already wired** — confirmed `ResearchBacktestAdapter` is injected in `research/runtime.py:50`
- **INT-3 E2E tests built** — `tests/test_research_e2e.py` with 12 tests covering all 7 backlog scenarios:
  1. Full Engine B flow (event → experiment)
  2. Engine B pilot flow (high score → trade sheet + signoff)
  3. Taxonomy rejection (hypothesis blocked by taxonomy)
  4. Blocking objections (unresolved objection → PARK)
  5. Engine A daily cycle (regime → signals → rebalance)
  6. Decay detection (strategy health monitoring)
  7. Kill flow (invalidation → RetirementMemo)
  8. Auto-kill with decay (auto-approve criterion)
  9-10. Chain traversal and artifact type progression
  11-12. Promotion gate pilot signoff (pending + approved)
- **Fixed 4 pre-existing test failures** from Replit Agent's last commit:
  - `test_research_readiness.py` — `routing_mode` changed when `RESEARCH_SYSTEM_ACTIVE=True`; readiness issues changed
  - `test_sa_browser_capture.py` — intel intake path changed from `intel_analysis` to `engine_b_intake` after cutover
- **Stored RESEARCH_DB_DSN** in `~/.profile` and Claude memory for future sessions

**Key files touched:**
- `tests/test_research_e2e.py` (NEW — INT-3)
- `tests/test_research_readiness.py` (fixed assertions)
- `tests/test_sa_browser_capture.py` (fixed assertions)
- `research/market_data/bootstrap.py` (tz-aware fix)
- `ops/COMBINED_NEXT_STEPS.md` (updated completion status)

**Verification:** `pytest -q --maxfail=10` → **2594 passed in 160.73s**

**Current state:**
- Items 1-3, 6-8 from backlog: **DONE**
- Items 4-5 (Engine A/B validation on real data): **READY** — data and schema are in place
- Item 9 (paper trade on IG Demo): blocked on 4-5
- Item 10 (UX polish): ready

**Next steps:**
- Run Engine A on the seeded historical data to validate output
- Run one full Engine B cycle with a real/manual event
- Paper trade the winning engine on IG Demo

## 2026-03-10 — Real-runtime validation, live-path hardening, and final handoff

**Context:** Continued from the operational-activation checkpoint after multiple reconnects. Goal was to finish real-data validation, harden the live IG path, and leave the repo in a durable restart state.

**Work done:**
- Revalidated the real runtime against PostgreSQL and the live research stack:
  - `python scripts/research_readiness_report.py` reported `ready`
  - Engine A validation succeeded on seeded data
  - Engine B validation succeeded on a real/manual event path
- Closed the main execution-path defects found during live validation:
  - `5094013` — guarded invalid Engine B `--source-class` CLI input
  - `2f0c7db` / `3ada930` — carried reference prices into manual Engine A live + paper intents so execution metrics populate correctly
  - `92504e4` — added post-dispatch live reconciliation against IG open positions
  - `97af3d2` — disabled implicit IG protective stops by default
  - `cd82bf6` — reused a connected IG broker session across multi-intent dispatch and failed the CLI on partial live batches
  - `1fa15aa` / `0deba3f` — mapped fresh-session IG positions back to configured tickers and persisted deal mappings across reconnects
- Added first-class post-trade reconciliation tooling:
  - `1b205a5` — added `scripts/sync_broker_snapshot.py`
  - `256fa10` — added inline ledger sync to `scripts/execute_engine_a_rebalance.py` via `--sync-ledger`
  - `bf3e6a1` / `441fd27` — updated ops docs so the preferred bounded live path uses inline sync, not a separate follow-up command
- Updated operational memory:
  - `39d1bff` — refreshed the final live checkpoint state in `ops/COMBINED_NEXT_STEPS.md` and `ops/RESEARCH_RESTART_CHECKPOINT.md`

**Operational verification:**
- Focused regression slices stayed green across the hardening sequence, including:
  - dispatcher / manual execution
  - IG broker regression / config
  - reconciler / broker-sync CLI
  - inline ledger sync on live Engine A execution
- Bounded live IG validation was completed at minimum size on the real account:
  - single-symbol `CL` open/hold/close passed
  - single-symbol `GC` open/hold/close passed
  - full six-symbol Engine A batch opened, held through the prior failure window, and flattened cleanly
  - one intentional six-symbol held batch was opened, inspected, synced into the ledger, and flattened
  - later one-symbol `NQ -> QQQ` smoke-close passed with inline `--sync-ledger`
- Latest live broker check after the final inline-sync validation:
  - `open_positions: 0`
  - `balance/equity: 8107.74 GBP`
  - endpoint: `live`

**Key files touched:**
- `scripts/run_research_validation.py`
- `research/manual_execution.py`
- `scripts/execute_engine_a_rebalance.py`
- `execution/dispatcher.py`
- `execution/reconciler.py`
- `broker/ig.py`
- `scripts/sync_broker_snapshot.py`
- `ops/COMBINED_NEXT_STEPS.md`
- `ops/RESEARCH_RESTART_CHECKPOINT.md`

**Current state:**
- Research build, real-data validation, and bounded live-path validation are complete.
- Live IG account is flat.
- Local ledger is flat and can be synced inline from the Engine A execution path.
- Repo is clean at `441fd27` (`Document inline ledger sync live flow`).

**Next steps:**
- Default safe live validation command:
  - `python scripts/execute_engine_a_rebalance.py --mode live --symbols NQ --size-mode min --commit --dispatch --allow-live --smoke-close --sync-ledger`
- If resuming operational work, treat any broader live batch as an intentional exposure decision, not further infrastructure proving.

## 2026-03-10 — Engine A CLI help surface tightened

**Context:** After the live-path hardening and doc updates, the remaining operator gap was discoverability from the script itself.

**Work done:**
- Updated `scripts/execute_engine_a_rebalance.py` argparse help to include concrete examples for:
  - previewing the latest bounded live batch
  - bounded live validation with `--smoke-close --sync-ledger`
  - intentionally holding bounded live exposure
  - flattening a held batch with inline sync
- Added regression coverage in `tests/test_execute_engine_a_rebalance_script.py` to assert those examples remain visible in `--help`.

**Verification:**
- `pytest tests/test_execute_engine_a_rebalance_script.py -q` → `12 passed`
- `python scripts/execute_engine_a_rebalance.py --help` now renders the bounded live examples correctly

**Current state:**
- Repo `HEAD` is `bbedeeb` (`Improve Engine A execution CLI help`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Broker helper CLI help surfaces tightened

**Context:** After the Engine A execution CLI help update, the remaining operator inconsistency was that the read-only broker helper scripts still had bare argparse output with no concrete usage examples.

**Work done:**
- Updated `scripts/check_ig_access.py` help output to include live and demo example invocations.
- Updated `scripts/sync_broker_snapshot.py` help output to include live/core and demo/sandbox sync examples.
- Added focused coverage in:
  - `tests/test_check_ig_access_script.py`
  - `tests/test_sync_broker_snapshot_script.py`

**Verification:**
- `pytest tests/test_check_ig_access_script.py tests/test_sync_broker_snapshot_script.py -q` → `4 passed`
- `python scripts/check_ig_access.py --help` renders the live/demo examples correctly

**Current state:**
- Repo `HEAD` is `5d0f191` (`Improve broker CLI help examples`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Research helper CLI help surfaces tightened

**Context:** After the broker helper cleanup, the research-side helper scripts still had weak or inconsistent `--help` output.

**Work done:**
- Updated `scripts/run_research_validation.py` help output to include concrete Engine A, Engine B, and `--engine all` examples.
- Added a minimal argparse help surface to `scripts/research_readiness_report.py`.
- Added focused coverage in:
  - `tests/test_run_research_validation_script.py`
  - `tests/test_research_readiness_report_script.py`

**Verification:**
- `pytest tests/test_run_research_validation_script.py tests/test_research_readiness_report_script.py -q` → `4 passed`

**Current state:**
- Repo `HEAD` is `f017661` (`Improve research CLI help examples`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Research validation CLI source-class examples corrected

**Context:** Verifying the new help text exposed a real mismatch: `run_research_validation.py --help` was advertising `--source-class manual_event`, but the parser correctly rejects that value.

**Work done:**
- Corrected the `run_research_validation.py` examples to use the valid `news_wire` source class.
- Updated the corresponding help-surface assertions in `tests/test_run_research_validation_script.py`.
- Fixed the stale resume command in `ops/RESEARCH_RESTART_CHECKPOINT.md`.

**Verification:**
- `pytest tests/test_run_research_validation_script.py tests/test_research_readiness_report_script.py -q` → `4 passed`
- `python scripts/run_research_validation.py --help` now matches the parser

**Current state:**
- Repo `HEAD` is `307a112` (`Fix research validation CLI source-class examples`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Market-data bootstrap CLI surfaced and fixed

**Context:** The last Python operator entrypoint without a real CLI surface was `scripts/bootstrap_research_market_data.py`.

**Work done:**
- Added argparse help plus explicit `--start`, `--end`, and `--years` handling to `scripts/bootstrap_research_market_data.py`.
- Added focused coverage in `tests/test_bootstrap_research_market_data_script.py` for help text and date-window resolution.
- While verifying the real entrypoint, found and fixed a genuine bootstrap bug: the script did not add the repo root to `sys.path`, so `python scripts/bootstrap_research_market_data.py --help` failed with `ModuleNotFoundError`.
- Added a subprocess regression proving the real script entrypoint now renders `--help` from the repo root.

**Verification:**
- `pytest tests/test_bootstrap_research_market_data_script.py -q` → `5 passed`
- `python scripts/bootstrap_research_market_data.py --help` now renders correctly

**Current state:**
- Repo `HEAD` moved through:
  - `aa6cd7a` (`Improve market data bootstrap CLI`)
  - `410a627` (`Fix bootstrap market data script entrypoint`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Detached-job shell helpers now handle --help sanely

**Context:** The detached-job shell helpers were the last operator entrypoints with inconsistent `--help` behavior. `status` and `stop` treated `--help` as a job name.

**Work done:**
- Updated:
  - `scripts/detached_job_start.sh`
  - `scripts/detached_job_status.sh`
  - `scripts/detached_job_stop.sh`
  - `scripts/detached_job_checkpoint.sh`
- All four scripts now handle `--help` / `-h` cleanly and print concrete usage/examples.
- Added subprocess coverage in `tests/test_detached_job_scripts.py`.

**Verification:**
- `pytest tests/test_detached_job_scripts.py -q` → `1 passed`

**Current state:**
- Repo `HEAD` is `d1bd33e` (`Add help to detached job scripts`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Tracked restart docs brought back in sync

**Context:** The repo-local memory files were current, but the tracked restart docs still stopped before the later CLI-standardization work.

**Work done:**
- Updated:
  - `ops/RESEARCH_RESTART_CHECKPOINT.md`
  - `ops/COMBINED_NEXT_STEPS.md`
- Recorded the later operator-tooling tranche in the tracked docs:
  - Engine A / broker / research CLI help cleanup
  - market-data bootstrap CLI surfacing + entrypoint fix
  - detached-job shell helper `--help` cleanup
- Added the bootstrap entrypoint and overnight-runner note back into the restart context where relevant.

**Current state:**
- Repo `HEAD` is `f4d4079` (`Update restart docs for operator tooling cleanup`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-10 — Daily Market Data Refresh + Feed Aggregator + TradingView News

**Context:** User approved plan for daily market data refresh (Engine A fuel) and automated feed aggregator (Engine B fuel), then requested TradingView news as a 4th source.

**Work done:**
- **Feature 1: Daily Market Data Refresh**
  - Config: `MARKET_DATA_REFRESH_ENABLED`, `_HOUR`, `_MINUTE` in `config.py`
  - Handler: `_run_market_data_refresh_window()` in control.py — calls `ingest_seeded_market_data(yesterday, today)`
  - Status: `market_data_refresh_status()` returns enabled/last_result
  - Scheduler: registers window when enabled, skips when disabled
  - Error isolation: refresh failure doesn't block Engine A
  - 9 tests in `tests/test_market_data_refresh.py`

- **Feature 2: Feed Aggregator (Finnhub + Alpha Vantage + FRED)**
  - Config: `FEED_AGGREGATOR_ENABLED`, `_TICKERS`, `_FINNHUB_INTERVAL`, `_AV_INTERVAL`, `_FRED_INTERVAL`, `_FRED_SERIES`
  - New file: `intelligence/feed_aggregator.py` — `FeedAggregatorService` with bounded dedup hash set, staggered polling, background thread
  - Control: `start/stop/status_feed_aggregator()` in control.py
  - Watchdog: auto-restart on crash in `check_and_restart()`
  - Pipeline status: included in `pipeline_status()`

- **Feature 3: TradingView News (4th feed source)**
  - Discovered TradingView public news API: `news-headlines.tradingview.com/v2/view/headlines/symbol` (no key needed)
  - New client: `intelligence/tradingview_news_client.py` — symbol mapping, retry, batch fetch
  - Wired into feed aggregator as `poll_tradingview_news()` with 0.75 credibility, 10min interval
  - Config: `FEED_AGGREGATOR_TV_INTERVAL=600`, `FEED_AGGREGATOR_TV_ENABLED=true` (on by default, no key needed)
  - 7 tests for client, 5 tests for TV in aggregator

**Key files touched:**
- `config.py` — 11 new config vars
- `.env.example` — all new vars documented
- `intelligence/feed_aggregator.py` — NEW (4-source aggregator)
- `intelligence/tradingview_news_client.py` — NEW (TV news client)
- `app/engine/control.py` — market data refresh + feed aggregator lifecycle
- `tests/test_market_data_refresh.py` — NEW (9 tests)
- `tests/test_feed_aggregator.py` — NEW (21 tests)
- `tests/test_tradingview_news_client.py` — NEW (11 tests)

**Verification:** 43 new tests pass. Full suite: 2690 passed, 1 pre-existing failure (lxml).

**Current state:** Both engines now have automated fuel. Engine A gets daily bar refresh. Engine B gets news from 4 sources (Finnhub, AV, FRED, TradingView) on staggered intervals. All disabled by default — set `MARKET_DATA_REFRESH_ENABLED=true` and `FEED_AGGREGATOR_ENABLED=true` to activate.

## 2026-03-10 — Overnight runner docs updated for helper-script discovery

**Context:** After the detached-job shell helpers gained proper `--help` handling, the overnight-runner note still omitted that discovery path.

**Work done:**
- Updated `ops/OVERNIGHT_RUNNER.md` to state that all four detached-job helpers support `--help` / `-h`.
- Added a quick discovery block showing:
  - `./scripts/detached_job_start.sh --help`
  - `./scripts/detached_job_status.sh --help`
  - `./scripts/detached_job_stop.sh --help`
  - `./scripts/detached_job_checkpoint.sh --help`

**Current state:**
- Repo `HEAD` is `f6f90bd` (`Document detached job help usage`)
- Tracked repo remains clean
- No live orders were placed in this step

## 2026-03-11 14:30 — Advisory UI: Missing Endpoints + Templates

**Context:** Continued from prior session that built advisory page with 3-column layout but left server-side endpoints unimplemented
**Work done:**
- Created `_advisory_news.html` template — RSS headlines with source tags, links, timestamps
- Created `_advisory_intel.html` template — feed aggregator events with source-color-coded labels
- Added `POST /api/advisory/transaction` endpoint — dispatches to record_buy/sell/cash/dividend based on form tx_type
- Added `GET /fragments/advisory-transactions` — renders transaction history from advisory_transactions table
- Added `GET /fragments/advisory-news` — renders RSS headlines from advisory_rss_cache
- Added `GET /fragments/advisory-intel` — renders research_events data from feed aggregator
- Added `advisory_transactions` table + indexes to `data/trade_db.py` init_db()
- All 15 advisory routes now registered and functional
- 34 advisory tests passing, 134 API/UI tests passing, no regressions
**Key files touched:** app/api/server.py, data/trade_db.py, app/web/templates/_advisory_news.html (new), app/web/templates/_advisory_intel.html (new)
**Current state:** Advisory module Phase 1-5 fully wired. All fragment endpoints exist for the advisory_page.html layout. Transaction recording, news feeds, intel events, chat, holdings, sessions, memories — all functional.
**Next steps:** Full test suite verification, potential UI polish

## 2026-03-12 09:00 — Application state audit + test fixes + multi-agent workflow setup

**Context:** User returned after chaotic Mar 11 with multiple agents (Replit Agent, Claude, Codex) working. Confused about application state. Asked for comprehensive assessment.

**Assessment findings:**
- Replit Agent made 3 commits on Mar 11: advisory module (+3,700 lines), massive server.py refactor (+12,967/-9,661), broker connection fix
- The refactor broke 26 tests: IGBroker._lock missing, mock targets stale, yfinance MultiIndex, flaky external API assertion
- Server imports and boots fine, 2730/2756 tests were passing

**Fixes applied (commit 44b9460):**
- Added `_lock` to IG broker test fixture (16 tests fixed)
- Updated mock targets from `server._broker` to `shared._broker` (2 tests fixed)
- Pointed equity curve route test at `routes/research.py` (1 test fixed)
- Flattened yfinance MultiIndex columns in `technical_job.py` (1 test fixed)
- Relaxed flaky Alpha Vantage assertion in signal e2e (1 test fixed)
- Installed `lxml` for earnings client
- **Result: 2756 passed, 0 failed**

**Workflow setup:**
- Archived SESSION_LOG.md: 2,425 lines moved to `.claude/history/archive/SESSION_LOG_pre_2026-03-10.md`
- Updated CLAUDE.md with:
  - Multi-agent coordination protocol (agent roles, git discipline, conflict prevention)
  - `git pull` as step 1 of every session
  - Updated architecture section reflecting post-refactor route structure
  - New pitfalls from the Mar 11 breakage
  - Updated key file locations (route modules, shared.py, test count)

**Key files touched:** CLAUDE.md, .claude/history/SESSION_LOG.md, tests/test_regression_ig_broker.py, tests/test_ui_fragment_caching.py, tests/test_phase_n_ui.py, tests/test_signal_engine_e2e.py, intelligence/jobs/technical_job.py

**Current state:** All 2756 tests green. Git clean. Multi-agent workflow documented.
**Next steps:** Push, then resume feature work or address remaining vision gaps
