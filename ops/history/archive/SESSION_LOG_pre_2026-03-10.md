# BoxRoomCapital — Session Log

## 2026-03-04 — Initial familiarisation and connectivity audit

**Context:** User asked Claude to familiarise with the project and plan for real connectivity
**Work done:**
- Scanned full repo architecture
- Identified P0/P1 integration gaps: dispatcher not wired, webhook endpoint missing, tier-1 ingestion limited
- Provided phased execution plan (7 steps)
- Discovered secrets are not exposed to the Replit process
**Current state:** Project had 15 phases (A-O) complete but nothing connected to outside world
**Next steps:** Make it a connected app

## 2026-03-05 — Vision gap analysis + 10-point execution plan

**Context:** User asked for progress review against their vision, then said "do it" to the 10-point plan
**Work done:**
- Created `VISION.md` with user's full ambition statement
- Deep connectivity audit via subagent — found most APIs are REAL, not mocks
- Gap analysis: 90% done vs vision, main gap is operational (turning it on)
- Executed all 10 plan items:

| # | Item | Status |
|---|------|--------|
| 1 | TradingView webhook | Already wired (verified) |
| 2 | Auto-start scheduler/dispatcher/intraday on boot | Done — lifespan handler |
| 3 | Process supervision watchdog | Done — 60s heartbeat restarts |
| 4 | Preflight checks + `/api/preflight` endpoint | Done — checks 8 services |
| 5 | Promote 4 strategies to staged_live | Done — IBS Long/Short, GTAA, Dual Momentum |
| 6 | Paper test (full DAG) | Done — all 5 nodes pass in 18.8s |
| 7 | Kraken crypto broker adapter | Done — `broker/kraken.py` |
| 8 | Intraday event loop | Done — `app/engine/intraday.py` |
| 9 | Promote to live | Blocked on missing secrets + soak period |
| 10 | Monitoring | Infrastructure already in place |

**Bugs fixed during execution:**
- `execution.intent_dispatcher` → `execution.dispatcher` (wrong module name)
- `build_composite_scores` → `run_signal_shadow_cycle` (wrong function name)
- `dispatch_orchestration()` missing `window_name` arg
- 5 pre-existing test assertion mismatches (Phase N template text changes)

**Key files touched:**
- `app/api/server.py` — preflight checks, auto-start, `/api/preflight` endpoint
- `app/engine/control.py` — scheduler/dispatcher/intraday lifecycle, supervision watchdog
- `app/engine/trading_dag.py` — fixed import and arg bugs
- `app/engine/intraday.py` — NEW: intraday polling loop
- `broker/kraken.py` — NEW: Kraken crypto adapter
- `config.py` — crypto markets, intraday config, Kraken keys
- `.env.example` — all missing env vars documented
- `VISION.md` — NEW: user's vision statement
- `fund/promotion_gate.py` — seeded 4 strategies into staged_live
- 5 test files — fixed pre-existing assertion mismatches

**Current state:** 2187 tests passing. Full DAG runs successfully in shadow mode. System ready to go live once secrets are added.
**Secrets present:** IG only (username, password, API key, acc type, acc number)
**Secrets missing:** Telegram, Anthropic, OpenAI, Google AI, XAI, FRED, Finnhub, Alpha Vantage, SA RapidAPI, Kraken

**Next steps:** User needs to add secrets in Replit, set ORCHESTRATOR_ENABLED=true, watch soak period, then go live.

## 2026-03-05 — CLAUDE.md creation and session protocol

**Context:** User frustrated with context loss on Replit shell restarts. Wants persistent memory.
**Work done:**
- Created `CLAUDE.md` with session history protocol and learning system
- Created `.claude/history/SESSION_LOG.md` with full timeline
- Documented learnings about user working style and technical preferences
**Current state:** Memory system in place for future sessions
**Next steps:** User will likely add secrets and want to see the system running live

## 2026-03-05 — API key validation + SA_RAPIDAPI dropped

**Context:** User added most secrets, shell session died mid-way. Came back to verify what's set.
**Findings so far:**
- All major keys present: IG (5), Telegram (2), AI panel (4), data feeds (3: FRED, Finnhub, Alpha Vantage)
- Still missing: SA_RAPIDAPI_KEY (dropped — user will webscrape instead), KRAKEN keys, TRADINGVIEW_WEBHOOK_TOKEN
- Running parallel API validation of all set keys...
**Work done:**
- Verified all 10 APIs pass (IG, Telegram, FRED, Finnhub, Alpha Vantage, Anthropic, OpenAI, Google AI, xAI)
- IG live account confirmed: spread bet PUQ8X, balance £5,091.18
- Telegram bot @BoxRCapital_Bot delivering messages
- Dropped SA_RAPIDAPI_KEY from requirements — user will webscrape instead
- Confirmed codebase already uses grok-3 (no grok-2 refs)
- Added "session discipline" learning to CLAUDE.md
- Built hybrid SA scraper (Playwright stealth login + internal JSON API):
  - `intelligence/scrapers/sa_scraper.py` — hybrid scraper with cookie caching
  - `intelligence/scrapers/sa_adapter.py` — drop-in SAQuantClient replacement
  - Updated `intelligence/jobs/sa_quant_job.py` — auto-selects scraper when no RapidAPI key
  - Updated `.env.example` — SA_EMAIL/SA_PASSWORD replaces SA_RAPIDAPI_KEY
  - Installed playwright + playwright-stealth + Chromium
- Website access audit: SA uses PerimeterX (hybrid approach handles it), ShareScope accessible, Koyfin accessible
- SA scraper uses: login → cookie extraction → internal API calls to `/api/v3/ticker_metric_grades`, `/api/v3/symbols/{slug}/rating/summary`, `/api/v3/news`, `/api/v3/symbols/{slug}/rating/sell_side_ratings`
**Key files touched:**
- `intelligence/scrapers/sa_scraper.py` (NEW)
- `intelligence/scrapers/sa_adapter.py` (NEW)
- `intelligence/jobs/sa_quant_job.py` (modified — auto-select scraper)
- `.env.example` (SA_EMAIL/SA_PASSWORD added)
- `CLAUDE.md` (new learnings)
**Current state:** All API keys validated. SA scraper built but needs live test with SA_EMAIL/SA_PASSWORD secrets.
**Secrets user is adding:** SA_EMAIL, SA_PASSWORD, SHARESCOPE_EMAIL/PASSWORD, KOYFIN_EMAIL/PASSWORD, KRAKEN keys, TRADINGVIEW_WEBHOOK_TOKEN
**Next steps:** User restarting session to load new secrets. Then live-test SA scraper. Fix ShareScope login URL (404 on /login). Enable orchestrator.

## 2026-03-05 — SA scraper v2 rewrite (recovered from crash)

**Context:** Session crashed mid-work while live-testing SA scraper. Changes recovered from git diff.
**Work done:**
- Major rewrite of `intelligence/scrapers/sa_scraper.py` — switched from full Playwright page-scraping to hybrid approach:
  - Playwright + stealth used ONLY for login to obtain session cookies
  - All data fetching now uses `requests` library against SA's internal JSON API (`/api/v3/`)
  - Cookie caching with 25-min expiry (auto-refresh)
  - Human-like random delays between API calls
  - Chromium auto-discovery for Replit nix store paths
- New API endpoints used:
  - `ticker_metric_grades` — factor grades (value/growth/momentum/profitability/revisions)
  - `symbols/{slug}/rating/summary` — quant/author/wall-st ratings
  - `news` — news articles (JSON:API format)
  - `symbols/{slug}/rating/sell_side_ratings` — analyst recommendations
- Added `_NUMERIC_TO_GRADE` mapping (SA uses 1-13 scale internally)
- Added `_derive_rating_from_grades()` fallback when ratings endpoint fails
- Cleaned up `sa_adapter.py` — removed unused imports, formatting
- Supports both `SA_EMAIL`/`SEEKING_ALPHA_EMAIL` env var names
**Key files touched:**
- `intelligence/scrapers/sa_scraper.py` (major rewrite)
- `intelligence/scrapers/sa_adapter.py` (cleanup)
**Current state:** Code rewritten but uncommitted. Live testing status unknown (session crashed before results).
**Next steps:** Live-test the rewritten scraper, commit changes, continue with orchestrator enablement.

## 2026-03-05 — SA scraper live test: PerimeterX blocks all approaches

**Context:** Live-testing SA scraper — all approaches fail
**Findings:**
- Raw `requests` POST to SA login/API: 403 PerimeterX captcha
- `cloudscraper` library: also 403
- Playwright + stealth: "prove you are not a robot" challenge page
- SA's PerimeterX (appId: PXxgCxM9By) blocks ALL server-side access
- Even public API endpoints (no auth needed) are blocked
**Alternative data sources tested:**
- Yahoo Finance: works — analyst recs, targets, recommendation keys
- Finnhub (we have API key): works — buy/hold/sell/strongBuy/strongSell breakdown
- Tipranks: also 403
**Decision:** Pivot to Yahoo Finance + Finnhub as primary; SA scraper becomes optional/manual
**Work done:**
- Built `YFinnhubAdapter` in `intelligence/scrapers/sa_adapter.py` — drop-in for SAQuantClient
  - `fetch_snapshot()`: Yahoo recommendationKey + recommendationMean → rating + score
  - `fetch_layer_score()`: combines Yahoo + Finnhub consensus → L8 LayerScore
  - `fetch_news()`: yfinance news API
  - `fetch_analyst_recs()`: Finnhub consensus with buy/hold/sell breakdown
- Updated `sa_quant_job.py` to use `YFinnhubAdapter` when no RapidAPI key
- Live test results:
  - AAPL: score=75.62, conf=0.95, rating=buy (41 analysts)
  - MSFT: score=90.89, conf=0.95, rating=strong buy
  - TSLA: score=71.4, conf=0.95, rating=buy
  - SPY: no rating (ETF, expected)
- Full job runner pipeline: 2/2 success with YFinnhubAdapter
**Current state:** Adapter working, test suite 2185 passed + fixed 4 AI panel tests.

## 2026-03-05 — Full external connectivity audit

**Broker connectivity results:**
| Broker | Connect | Account | Keys | Status |
|--------|---------|---------|------|--------|
| IG (LIVE) | OK | £5,091.18 GBP, 0 positions | All 5 present | READY |
| Kraken | OK | $0.00 USD (empty) | Both keys present | READY (unfunded) |
| IBKR | No config in config.py | N/A | None | NOT WIRED — needs TWS/Gateway |
| CityIndex | References config vars that don't exist | N/A | None | NOT WIRED |
| Paper | In-memory mock | N/A | None | Always works |

**Strategy slot → broker mapping:**
- gtaa_isa (IBKR) — NOT READY, no IBKR config
- dual_momentum_isa (IBKR) — NOT READY
- ibs_long_sb (IG) — READY
- ibs_short_sb (IG) — READY

**Data feed audit:**
| Feed | Status |
|------|--------|
| Yahoo Finance | Working (ratings, news, targets) |
| Finnhub | Working (consensus, recommendations) |
| FRED | Key present, validated |
| Alpha Vantage | Key present, validated |
| Anthropic | Key present, validated |
| OpenAI | Key present, validated |
| Google AI | Key present, validated |
| xAI/Grok | Key present, validated |
| SA Quant | Blocked by PerimeterX — replaced by YFinnhub |
| Telegram | Working — @BoxRCapital_Bot (env var is TELEGRAM_TOKEN, not TELEGRAM_BOT_TOKEN) |

**Current state:** IG spread bet strategies ready to trade. IBKR/CityIndex need account setup.

## 2026-03-05 — Codex memory sync and protocol adoption

**Context:** User wants Codex and Claude to share the same working memory and operating rules while switching between models in Replit.
**Work done:**
- Read `CLAUDE.md`, `VISION.md`, `.claude/history/SESSION_LOG.md`, and `../.claude/projects/-home-runner-workspace/memory/MEMORY.md`
- Reviewed current branch state and uncommitted worktree changes
- Aligned on the local protocol: read history first, update session memory incrementally, and use the same shared handoff files Claude uses
- Captured latest recorded state for handoff: SA is blocked by PerimeterX, `YFinnhubAdapter` is the current fallback path, IG is ready, Kraken is connected but unfunded, IBKR/CityIndex are not wired, and Telegram bot token is currently missing
**Key files touched:** `.claude/history/SESSION_LOG.md`, `../.claude/projects/-home-runner-workspace/memory/MEMORY.md`
**Current state:** Codex is now using the same repo-local memory workflow as Claude for future sessions and task handoffs.
**Next steps:** Continue from the current uncommitted SA/Yahoo+Finnhub changes or move to runtime/orchestrator validation, depending on the next task.

## 2026-03-05 — TradingView webhook banner diagnosis

**Context:** User reported red top-strip text in the UI: `TradingView webhook rejected`.
**Work done:**
- Traced the banner source to `get_incidents(limit=1)` rendered by the top strip fragment
- Confirmed webhook rejections are logged in `app/api/server.py` when payload parsing or token validation fails
- Queried the live incident row from the DB
- Verified the latest rejection detail is `reason=missing webhook token` with `client_ip=testclient`
- Cross-checked tests and confirmed FastAPI `TestClient` webhook tests hit the real endpoint path and can pollute the shared `bot_events` table
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** The banner is currently showing a test-generated rejection, not evidence of a live TradingView integration failure.
**Next steps:** If desired, isolate test DB writes or filter test-origin incidents from the ops UI.

## 2026-03-05 — TradingView banner fix: filter test artifacts and stop test DB pollution

**Context:** User asked whether the fix was to "make this connect to TradingView". Investigation showed webhook connectivity was already present; the actual bug was test-origin incidents appearing in the live UI.
**Work done:**
- Confirmed `TRADINGVIEW_WEBHOOK_TOKEN` is set in the runtime config and webhook endpoint is already wired
- Added operator-facing incident filtering in `app/api/server.py` so FastAPI `testclient` artifacts are excluded from:
  - `/api/incidents`
  - top-strip latest incident banner
  - incidents fragment
  - legacy dashboard incident panel
- Patched webhook tests to stop writing to the shared live DB:
  - stubbed `create_order_intent_envelope` in `tests/test_api_webhook_intake.py`
  - stubbed `log_event` and `create_order_intent_envelope` in `tests/test_phase_o.py`
- Added regression coverage in `tests/test_api_status.py` for both incident filtering and top-strip behavior
- Verified the top-strip fragment no longer contains `TradingView webhook rejected`
- Ran focused test suite: `pytest -q tests/test_api_status.py tests/test_api_webhook_intake.py tests/test_phase_o.py`
**Key files touched:** `app/api/server.py`, `tests/test_api_status.py`, `tests/test_api_webhook_intake.py`, `tests/test_phase_o.py`, `.claude/history/SESSION_LOG.md`, `../.claude/projects/-home-runner-workspace/memory/MEMORY.md`
**Current state:** The TradingView banner issue is fixed as an ops/UI bug. The webhook remains connected; test artifacts are no longer surfaced as live incidents and future webhook tests no longer pollute the shared DB path.
**Next steps:** If user wants actual TradingView live alerting validated end-to-end, send a real signed webhook through `/api/webhooks/tradingview` and watch intent creation.

## 2026-03-05 — TradingView role planning against project vision

**Context:** User asked how TradingView alerts should fit the BoxRoomCapital vision and requested planning only, not implementation.
**Work done:**
- Reviewed current TradingView webhook intake, technical overlay, strategy-slot configuration, and vision statement
- Confirmed the repo is ready to receive TradingView alerts but does not include TradingView alert creation assets or Pine scripts
- Identified current architecture mismatch: webhook intake is wired, but current default routing is not aligned with the live sleeve/broker model
- Recommended role: TradingView as a chart-based trigger source for simple technical sleeves, while BoxRoomCapital remains the decision, risk, capital-allocation, and execution brain
- Recommended first-wave use cases: IBS long/short style sleeves before any broader rollout
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Planning direction is clear, but no code changes were made for TradingView strategy buildout in this step.
**Next steps:** Decide which first-wave strategies should originate from TradingView, whether TV supplies only the signal or also sizing hints, and whether rollout starts shadow-only or staged-live.

## 2026-03-05 — Intel pipeline: SA bookmarklet + X/Telegram forwarding + LLM council

**Context:** User wants SA subscription data into the app (server scraping blocked by PerimeterX) and X/Twitter content forwarded for LLM analysis.
**Work done:**
- Built `intelligence/intel_pipeline.py` — shared LLM analysis engine
  - Queries all available models (Claude, ChatGPT, Grok, Gemini) with intel-specific prompt
  - Extracts tickers, trade ideas (direction, conviction, entry triggers, invalidation), risk factors
  - Deduplicates across models, persists to research event store, sends Telegram notification
- Added 3 webhook endpoints to `app/api/server.py`:
  - `POST /api/webhooks/sa_intel` — receives SA page data from browser bookmarklet
  - `POST /api/webhooks/x_intel` — receives X/Twitter content
  - `POST /api/webhooks/telegram` — Telegram bot webhook (auto-analyzes forwarded X links, `/analyze` command, any text)
- Added `GET /api/intel/history` — lists recent intel analysis results
- Added `GET /intel/bookmarklet` — install page for SA bookmarklet (drag to bookmarks bar)
- Created `app/web/static/sa_bookmarklet.js` — scrapes SA page (title, content, tickers, quant grades, rating, author) and POSTs to BoxRoomCapital
- Telegram bot now handles: X link forwarding, `/analyze` command, `/status`, `/help`, free-text analysis
- Added 15 tests in `tests/test_intel_pipeline.py` — all passing
- Full test suite: 2202 passed (2 pre-existing failures unrelated)
**Key files created/modified:**
- `intelligence/intel_pipeline.py` (NEW)
- `app/web/static/sa_bookmarklet.js` (NEW)
- `tests/test_intel_pipeline.py` (NEW)
- `app/api/server.py` (3 webhooks + bookmarklet page + intel history)
- `.env.example` (Telegram webhook setup instructions)
**Current state:** All endpoints functional, tests passing. Ready for live use.
**To activate:**
1. SA: Visit `/intel/bookmarklet`, drag link to bookmarks bar, browse SA, click bookmark
2. Telegram: Run `POST https://api.telegram.org/bot<TOKEN>/setWebhook?url=<SERVER_URL>/api/webhooks/telegram`
3. X: Forward tweets/links to @BoxRCapital_Bot on Telegram, or POST to `/api/webhooks/x_intel`

## 2026-03-05 — Claude token window settings updated

**Context:** User asked to set higher Claude context and auto-compact thresholds in this Replit environment.
**Work done:**
- Added `model_context_window = 1000000`
- Added `model_auto_compact_token_limit = 900000`
- Applied the values in both global Claude settings and project-local Claude settings
- Validated both JSON files parse and contain the requested values
**Key files touched:** `/home/runner/.claude/settings.json`, `.claude/settings.local.json`, `.claude/history/SESSION_LOG.md`
**Current state:** Requested Claude settings are present in both config locations.
**Next steps:** Restart/reload Claude session if required for the client to pick up the new settings.

## 2026-03-05 — TradingView activation implementation started

**Context:** User approved implementation of the TradingView activation plan: governed alert intake, Pine assets, audit trail, lane-aware execution, and frequent memory checkpoints.
**Work done:**
- Re-read TradingView webhook, strategy slot, router, promotion-gate, and event-store code paths
- Locked implementation decisions from the plan:
  - first wave = `ibs_spreadbet_long` + `ibs_spreadbet_short` on `SPY`/`QQQ`
  - TradingView role = execution trigger + audit trail
  - sizing = bot-controlled
  - `shadow` and `staged_live` lanes = audit-only, no dispatchable intent creation
- Inspected current dirty diffs in `app/api/server.py` and webhook tests to avoid clobbering unrelated work
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Ready to implement TradingView registry, normalized payload handling, audit persistence, operator inbox, Pine assets, and tests.
**Next steps:** Patch server/config/webhook helpers first, then add tests and TradingView assets, then run focused and full test suites.

## 2026-03-05 — TradingView activation checkpoint: core webhook flow patched

**Context:** Implementing governed TradingView alert intake with audit trail and lane-aware execution.
**Work done:**
- Extended `intelligence/webhook_server.py` with:
  - TradingView strategy registry built from `config.STRATEGY_SLOTS`
  - normalized `tv.v1` alert payload validation
  - per-strategy ticker/action/timeframe enforcement
  - signal age validation and correlation/source refs
- Extended research-event plumbing:
  - `data/trade_db.py` now supports lookup of a single research event by ID
  - `intelligence/event_store.py` now exposes deterministic event ID computation and event lookup
- Patched `app/api/server.py` TradingView flow:
  - `GET /api/tradingview/alerts`
  - dedupe before intent creation
  - promotion-lane lookup (`live` vs `staged_live` vs `shadow`)
  - audit-only handling for non-live lanes
  - route/capability/risk/promotion checks before live intent creation
  - intent metadata/correlation IDs now come from the TradingView alert instead of hardcoded defaults
- Added new config/env vars:
  - `TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS`
  - `TRADINGVIEW_ENABLED_STRATEGIES`
**Key files touched:** `intelligence/webhook_server.py`, `intelligence/event_store.py`, `data/trade_db.py`, `app/api/server.py`, `config.py`, `.env.example`, `.claude/history/SESSION_LOG.md`
**Current state:** Core TradingView flow is implemented but unverified. Tests and TradingView Pine assets are next.
**Next steps:** Patch webhook tests to the new contract, add Pine scripts + alert template docs, run focused tests, fix failures, then run the full suite.

## 2026-03-05 — TradingView activation checkpoint: tests and assets added

**Context:** Continuing the TradingView activation implementation after the core webhook patch.
**Work done:**
- Reworked TradingView tests to the new governed contract:
  - `tests/test_webhook_server.py`
  - `tests/test_api_webhook_intake.py`
  - `tests/test_phase_o.py`
- Added repo-owned TradingView assets:
  - `tradingview/ibs_spreadbet_long_v1.pine`
  - `tradingview/ibs_spreadbet_short_v1.pine`
  - `tradingview/ALERTS.md`
- Verified targeted TradingView/status surfaces: `129 passed`
- Full suite initially failed in 2 unrelated notification tests because this runtime has live Telegram env vars set; patched `tests/test_e2e_pipeline.py` to disable notifications explicitly inside `TestOperatorAlerts`
- Verified the isolated notification subset now passes: `6 passed`
**Key files touched:** `tests/test_webhook_server.py`, `tests/test_api_webhook_intake.py`, `tests/test_phase_o.py`, `tests/test_e2e_pipeline.py`, `tradingview/ibs_spreadbet_long_v1.pine`, `tradingview/ibs_spreadbet_short_v1.pine`, `tradingview/ALERTS.md`, `.claude/history/SESSION_LOG.md`
**Current state:** TradingView implementation appears correct on focused tests. One final full-suite rerun remains.
**Next steps:** Rerun full `pytest -q`, capture final status, then write the closing session log summary.

## 2026-03-05 — TradingView activation checkpoint: final suite blocked by backtest fragment null formatting

**Context:** TradingView webhook implementation and targeted tests are green, but the first full-suite rerun exposed a separate dashboard rendering regression.
**Work done:**
- Reproduced the remaining failures in `tests/test_phase_o.py`
- Confirmed `_backtest.html` crashes when `job.result_parsed` contains `None` for numeric fields such as `sharpe_ratio`
- Scoped the fix to defensive template formatting so the TradingView implementation remains unchanged
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Only the backtest fragment null-handling regression remains before the final full-suite verification.
**Next steps:** Patch `app/web/templates/_backtest.html`, rerun the targeted fragment tests, then rerun full `pytest -q` and write the closing session summary.

## 2026-03-05 — TradingView activation checkpoint: backtest fragment regression fixed

**Context:** Final suite verification for the TradingView activation work was blocked by unrelated null formatting in the backtest dashboard fragment.
**Work done:**
- Patched `app/web/templates/_backtest.html` to treat nullable `sharpe_ratio`, `max_drawdown_pct`, `total_trades`, and `win_rate` safely during Jinja formatting
- Re-ran the previously failing `tests/test_phase_o.py` fragment tests and confirmed they now pass (`2 passed`)
**Key files touched:** `app/web/templates/_backtest.html`, `.claude/history/SESSION_LOG.md`
**Current state:** Targeted failures are resolved. Final full-suite verification is in progress.
**Next steps:** Run `pytest -q`, capture the final status, and write the closing session summary.

## 2026-03-05 — TradingView activation implementation complete

**Context:** Completed the governed TradingView alert-ingestion rollout requested by the user, including route-aware execution, audit persistence, repo-owned TradingView assets, tests, and final full-suite verification.
**Work done:**
- Implemented normalized `tv.v1` TradingView alert validation and strategy registry enforcement
- Routed accepted live alerts through slot-derived sleeve/account/broker metadata, dedupe, control checks, risk checks, and promotion-gate checks before intent creation
- Added audit-only behavior and inbox visibility for `shadow` / `staged_live` alerts
- Added repo-owned Pine scripts and alert payload templates for the first-wave IBS strategies
- Hardened the backtest dashboard fragment against nullable result metrics uncovered during final verification
- Ran the full repository test suite successfully: `2211 passed`
**Key files touched:** `app/api/server.py`, `intelligence/webhook_server.py`, `intelligence/event_store.py`, `data/trade_db.py`, `config.py`, `.env.example`, `app/web/templates/_backtest.html`, `tests/test_webhook_server.py`, `tests/test_api_webhook_intake.py`, `tests/test_phase_o.py`, `tests/test_e2e_pipeline.py`, `tradingview/ibs_spreadbet_long_v1.pine`, `tradingview/ibs_spreadbet_short_v1.pine`, `tradingview/ALERTS.md`, `.claude/history/SESSION_LOG.md`
**Current state:** TradingView activation code is implemented and the repo is green.
**Next steps:** Configure TradingView alerts with the documented `tv.v1` payloads and set the relevant strategy lanes to `live` when you are ready to execute real intents.

## 2026-03-06 06:25 — Replit agent disconnect research checkpoint

**Context:** User reported that both Claude Code and Codex stop running inside the Replit bash shell after a couple of hours while the shell itself stays alive, and asked whether others have reported similar issues.
**Work done:**
- Checked public Anthropic sources and issue trackers for reports matching silent agent stoppage, hangs, freezes, and disconnects
- Found public Claude Code issues describing similar symptoms in terminal/containerized environments:
  - `#619` intermittent hang with process still running and no output
  - `#4837` frequent disconnects during normal usage
  - `#12184` session freeze/start-resume failures in a containerized Linux/web environment
- Confirmed Anthropic troubleshooting docs include a `Command hangs or freezes` path and recommend restart, `/doctor`, and `/bug`
- Confirmed Anthropic status page has recent Claude Code incidents on 2026-03-02 and 2026-03-03
- Inferred that the shared failure across Claude Code and Codex increases the likelihood of a common Replit/web-terminal/PTY/network/session cause rather than a single vendor-specific bug, but this is not yet proven
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Research is saved. Best current hypothesis is a shared Replit or long-lived interactive CLI session failure mode; no root cause has been isolated yet.
**Next steps:** If this recurs, capture exit code, timestamps, memory pressure, and whether the agent survives under `tmux`/`nohup` or a watchdog wrapper so the failure can be narrowed down.

## 2026-03-06 07:51 — SA retry: browser-assisted capture path restored real SA data

**Context:** User wants Seeking Alpha data back in the system after the prior server-side scraper failed under PerimeterX, because SA remains strategically important.
**Work done:**
- Kept the conclusion that server-side SA login/API scraping is not the viable path in this environment
- Implemented a browser-assisted SA capture path using the existing bookmarklet and a new webhook:
  - `POST /api/webhooks/sa_quant_capture`
  - parses ticker, quant rating, quant score, author/wall-st ratings, and factor grades from a user-authenticated browser page
  - stores the raw capture as a research event
  - writes an L8 `signal_layer` event when quant rating/score is present
  - stores normalized factor grades in the feature store DB
- Added `SABrowserCaptureAdapter` so SA quant jobs now prefer recent browser-captured SA data before falling back to Yahoo Finance + Finnhub
- Updated the SA bookmarklet to send:
  - article pages to `sa_intel`
  - stock pages with quant data to `sa_quant_capture`
- Added config/env support for staleness control: `SA_BROWSER_CAPTURE_MAX_AGE_SECONDS`
- Fixed SA factor-grade persistence in `sa_quant_job.py` to use the repo DB instead of an in-memory feature store
- Verified focused SA tests: `14 passed`
**Key files touched:** `intelligence/scrapers/sa_adapter.py`, `intelligence/jobs/sa_quant_job.py`, `app/api/server.py`, `app/web/static/sa_bookmarklet.js`, `config.py`, `.env.example`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** Real SA data can now enter the system through the user's logged-in browser session, and the batch SA quant job can reuse that captured data. Fully automated server-side SA scraping is still blocked by PerimeterX.
**Next steps:** Live-test the bookmarklet against a real Seeking Alpha stock page and article page, confirm captures appear in the app/database, then decide whether to layer a more automated browser-runner on top later.

## 2026-03-06 08:21 — Bookmarklet install page bug fixed

**Context:** User reported that dragging the generated bookmarklet from `/intel/bookmarklet` appeared to do nothing.
**Work done:**
- Diagnosed the generated bookmarklet href as corrupted
- Root cause: the install page minifier stripped `//...` after flattening the script, which truncated embedded `https://...` endpoint strings to `https:`
- Also hardened the HTML generation by escaping the bookmarklet href instead of injecting raw JS into the anchor attribute
- Added a regression test to verify the builder preserves `https://` URLs
- Verified focused tests: `5 passed`
**Key files touched:** `app/api/server.py`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** Newly generated bookmarklets from `/intel/bookmarklet` should now be valid. Any bookmarklet previously dragged from the broken page remains invalid and must be replaced.
**Next steps:** Reload `/intel/bookmarklet`, remove the old bookmark, drag the new one, then test on one Seeking Alpha stock page.

## 2026-03-06 08:24 — SA bookmarklet CORS fix

**Context:** User retried the regenerated bookmarklet and got a browser `Load failed` error.
**Work done:**
- Diagnosed the failure as missing CORS/preflight handling for cross-origin `fetch()` from `https://seekingalpha.com`
- Added FastAPI `CORSMiddleware` allowing `POST/OPTIONS` requests from Seeking Alpha origins
- Verified the preflight path directly with ASGI transport:
  - status `200`
  - `access-control-allow-origin: https://seekingalpha.com`
  - `access-control-allow-methods: GET, POST, OPTIONS`
- Added a regression test for the preflight behavior
**Key files touched:** `app/api/server.py`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** The app should now accept the SA bookmarklet's browser-originated requests. The app process may need a restart/reload before the browser sees the fix.
**Next steps:** Restart/reload the app if needed, then test the bookmarklet again on a real SA page and inspect stored events if it still fails.

## 2026-03-06 08:30 — Session memory checkpoint saved

**Context:** User is concerned about losing context due to Replit disconnects and asked to save this session to memory.
**Work done:**
- Confirmed this session's checkpoints are already preserved in `SESSION_LOG.md`
- Created a repo-local conversation memory file with the key decisions, file changes, verification results, limitations, and exact restart steps:
  - `.claude/history/CONVERSATION_2026-03-06.md`
**Key files touched:** `.claude/history/CONVERSATION_2026-03-06.md`, `.claude/history/SESSION_LOG.md`
**Current state:** Repo-local memory for this session now exists in both quick-scan and longer-form form.
**Next steps:** On the next restart, read `CLAUDE.md`, then `.claude/history/SESSION_LOG.md`, then `.claude/history/CONVERSATION_2026-03-06.md` before resuming the SA bookmarklet live test.

## 2026-03-06 11:52 — First live SA bookmarklet test: partial success only

**Context:** User tested the SA bookmarklet after the generator and CORS fixes and asked whether it worked because there was no browser error.
**Work done:**
- Queried `trades.db` for recent SA bookmarklet captures, jobs, and bot events
- Confirmed a new `sa_browser_capture` research event was written at `2026-03-06T11:50:59Z`
- Confirmed a matching bot event: `SA capture received: VET`
- Inspected the stored payload and found the captured URL was `https://seekingalpha.com/author/bang-for-the-buck`
- Confirmed the capture contained:
  - ticker `VET`
  - `page_type=article`
  - two factor grades (`value=A`, `growth=D`)
  - no quant rating
  - no quant score
- Confirmed no new SA intel job or SA browser-capture `signal_layer` event was created from this test
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** The bookmarklet request path works end-to-end, but the tested page was not a full article body or symbol page with enough structured data for a complete SA quant/intel result.
**Next steps:** Re-test on a real SA stock/symbol page or a full article page, then re-check whether an `intel_analysis` job and/or `signal_layer` event is created.

## 2026-03-06 11:55 — Second live SA bookmarklet test: article ingestion succeeded

**Context:** User re-tested the bookmarklet on a real Seeking Alpha article and asked whether it worked.
**Work done:**
- Queried `trades.db` for the latest SA bookmarklet captures, jobs, bot events, and factor-grade feature records
- Confirmed a new `sa_browser_capture` event at `2026-03-06T11:54:32Z`
- Confirmed the captured page was a real article:
  - URL: `https://seekingalpha.com/article/4879407-as-losses-endure-babcock-and-wilcox-has-dilution-cash-flow-problem-downgrade`
  - title captured correctly
  - 5 factor grades captured and normalized into `feature_records`
- Confirmed a new `intel_analysis` job was created and completed successfully for that article
- Confirmed matching bot events for both:
  - `SA capture received: BWEI.BWS`
  - `SA intel received: As Losses Endure, Babcock & Wilcox Has A Dilution And Cash F...`
- Confirmed there was still no SA quant `signal_layer` event because this was an article page with no quant rating / quant score extracted
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** The SA article ingestion path is working end-to-end: bookmarklet -> webhook -> capture persistence -> factor-grade storage -> intel analysis job completion. The remaining gap is extracting full quant-rating data from a symbol/stock page.
**Next steps:** Test the bookmarklet on a Seeking Alpha symbol page (for example `/symbol/AAPL`) to verify creation of an SA quant `signal_layer` event with rating/score.

## 2026-03-06 12:01 — Symbol-page tab traversal added to SA bookmarklet

**Context:** User pointed out that Seeking Alpha symbol pages are complex and multi-tabbed and asked the bookmarklet to navigate those information-rich routes automatically.
**Work done:**
- Rewrote `app/web/static/sa_bookmarklet.js` from a one-shot DOM scrape into a small in-browser crawler for symbol pages
- New symbol-page behavior:
  - scrapes the current DOM first
  - walks visible tab/button controls that look like ratings/grades/analysis tabs
  - fetches same-origin symbol subroutes exposed by visible links without leaving the page
  - merges the richest fields found across the scanned tabs/routes before posting
- Restricted LLM intel submission to article pages so symbol-page tab crawling does not create noisy intel jobs
- Verified JS syntax with `node --check`
- Re-ran bookmarklet regression tests successfully: `6 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `.claude/history/SESSION_LOG.md`
**Current state:** The bookmarklet can now actively explore symbol-page tabs/routes instead of only scraping the currently visible pane. User must regenerate the bookmarklet from `/intel/bookmarklet` to get this version.
**Next steps:** Reload `/intel/bookmarklet`, replace the old bookmark, click it on a real SA symbol page, then verify whether a browser-captured SA quant score/rating is now persisted.

## 2026-03-06 12:21 — Symbol-page crawler broadened with scan_debug

**Context:** User observed that the upgraded bookmarklet appeared to visit the ratings tab and then stop, which was not enough to capture the SA quant score/rating on a symbol page.
**Work done:**
- Broadened symbol-page exploration in `app/web/static/sa_bookmarklet.js`:
  - scans current DOM plus raw HTML
  - expands likely overflow/menu controls before tab collection
  - fetches hidden same-origin symbol subroutes, not just visible links
  - adds guessed symbol subroutes such as `ratings`, `ratings/quant-ratings`, `ratings/factor-grades`, `revisions`, `earnings`, `valuation`, `analysis`
  - uses broader regex extraction for quant rating, quant score, and factor grades from both visible text and HTML
- Added `scan_debug` payload data so the next bookmarklet click records which menus/tabs/routes were visited and what each returned
- Preserved `scan_debug` and `tickers` in the server-side normalized capture payload (`intelligence/scrapers/sa_adapter.py`)
- Added a regression assertion that `scan_debug` survives normalization
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `6 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `intelligence/scrapers/sa_adapter.py`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** The next symbol-page test should leave much better evidence in `trades.db` even if quant score extraction still misses.
**Next steps:** Reload `/intel/bookmarklet`, replace the bookmark again, click it on a real symbol page, then inspect the latest `sa_browser_capture` payload and its `raw_fields.scan_debug`.

## 2026-03-06 12:24 — Symbol-page MU test produced first live SA browser signal-layer event

**Context:** User regenerated the latest bookmarklet, clicked it on a real Seeking Alpha symbol page, and asked what it scraped.
**Work done:**
- Queried `trades.db` for the newest SA bookmarklet capture, `scan_debug`, bot events, factor-grade feature records, and downstream `sa-browser-capture` signal-layer events
- Confirmed a new `sa_browser_capture` event for `MU` at `2026-03-06T12:23:10Z`
- Confirmed the capture URL was `https://seekingalpha.com/symbol/MU/ratings/quant-ratings`
- Confirmed the bookmarklet now visited many symbol-page tabs and routes via `scan_debug`, including:
  - button tabs: quant rating, SA analysts' rating, wall-st analysts' rating, valuation, growth, profitability, momentum, dividends, earnings, charting, financials, options, peers
  - fetched routes: `ratings/quant-ratings`, `ratings/author-ratings`, `ratings/sell-side-ratings`, `valuation/metrics`, plus several guessed routes that 404ed
- Confirmed the run produced the first live downstream `signal_layer` event from source `sa-browser-capture`:
  - ticker `MU`
  - score `80.0`
  - rating string extracted as `quant ratingquant rating`
  - raw quant score extracted as `8.0`
- Confirmed factor grades were stored for `MU` at the same timestamp
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Symbol-page crawling now reaches enough of the SA experience to create a live browser-derived SA signal-layer event. Extraction quality still needs cleanup: duplicated rating text, noisy ticker harvesting, and likely-imprecise quant-score parsing.
**Next steps:** Tighten selector/regex logic specifically for SA quant rating/score blocks and constrain ticker extraction on symbol pages now that `scan_debug` gives concrete evidence of where the data lives.

## 2026-03-06 12:32 — Quant extractor tightened from MU evidence

**Context:** User confirmed the true MU quant rating is about `4.99/5`, so the prior browser capture (`8.0` and `quant ratingquant rating`) was clearly misparsed.
**Work done:**
- Tightened `app/web/static/sa_bookmarklet.js` extraction logic based on the live MU `scan_debug` evidence:
  - symbol pages now keep the primary `/symbol/<TICKER>` ticker only instead of harvesting dozens of peer/noise tokens
  - quant rating extraction now normalizes against an explicit allowed rating list and rejects heading text like `Quant RatingQuant Rating`
  - quant score extraction now only accepts quant-like values on a 1-5 scale and only from quant contexts
  - merge logic now prefers field values from the most relevant context (quant vs author vs sell-side vs grades) instead of keeping the first non-null value
  - reduced guessed routes to the ones that have actually shown signal on SA symbol pages
  - prevented the crawler from opening unrelated menus like the user menu
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `6 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `.claude/history/SESSION_LOG.md`
**Current state:** The next regenerated bookmarklet should produce a much cleaner MU/AAPL symbol-page capture. User must replace the bookmark again because the bookmarklet code changed.
**Next steps:** Reload `/intel/bookmarklet`, replace the bookmark, click the same symbol page again, then inspect the latest `sa_browser_capture` payload for the corrected quant rating/score.

## 2026-03-06 12:51 — Bookmarklet install page now shows version and disables caching

**Context:** User retried the bookmarklet, but the DB showed no new capture row and no `bookmarklet_version`, indicating the browser was still running an older bookmark source.
**Work done:**
- Added `_extract_bookmarklet_version()` to parse the inline JS version stamp
- Updated `/intel/bookmarklet` to visibly display the current bookmarklet version on the install page
- Added `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` plus `Pragma: no-cache` and `Expires: 0` headers to the bookmarklet install page
- Added a regression test for version extraction
- Verified:
  - `python -m py_compile app/api/server.py intelligence/scrapers/sa_adapter.py`
  - `pytest -q tests/test_sa_browser_capture.py` -> `7 passed`
**Key files touched:** `app/api/server.py`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** User can now confirm the bookmarklet version directly on `/intel/bookmarklet` before dragging it. This should eliminate ambiguity about stale/cached bookmark sources.
**Next steps:** Hard refresh `/intel/bookmarklet`, verify the page shows the latest version string, replace the bookmark, then click it once on the same MU symbol page and inspect the new capture row for that version.

## 2026-03-06 ~09:00 — Project status review + Telegram webhook setup

**Context:** New Claude Code session after disconnect. User asked for project status, then wanted to set up Telegram forwarding for X posts.
**Work done:**
- Full status review: branch state, last Claude/Codex work, uncommitted changes
- Ran full test suite: 2211 passed in 3m26s — all green
- Started server, found public Replit URL
- Discovered TELEGRAM_TOKEN env var was stale in shell (worked when hardcoded)
- Registered Telegram webhook (must use POST body, not query params — query param returns 404)
- User sent test X post to @BoxRCapital_Bot — pipeline worked but only got URL, not tweet content
- X blocks server-side content fetching (requires login)
- Discussed alternatives: X Bookmarks API ($100+/mo), bookmarklet (free), bookmarks page scanner
- User wants to use X bookmarks as source of interesting ideas
- User asked to save session to persistent memory

**Key files touched:** `.claude/history/SESSION_LOG.md`, persistent memory files created

**Current state:**
- Telegram bot webhook is registered and working
- LLM council pipeline processes messages but X links only pass URL (no content)
- Pending decision: bookmarklet vs bookmarks page scanner vs both for X content ingestion
- 2211 tests passing, ~1,500 lines uncommitted

**Next steps:**
1. User chooses X content ingestion approach (bookmarklet / bookmarks scanner / both)
2. Build chosen approach
3. Commit the large uncommitted diff
4. Consider merging to main

## 2026-03-06 13:06 — Non-SA self-capture blocked in bookmarklet

**Context:** User said they tested the latest bookmarklet. DB inspection showed the newest run was not a Seeking Alpha symbol/article page at all: it captured `/intel/bookmarklet` itself, created a bogus `sa_browser_capture` for ticker `SBRC`, and even queued an `intel_analysis` job on the install page.
**Work done:**
- Queried `trades.db` directly with Python because `sqlite3` is not installed in this runtime
- Confirmed the latest research events were:
  - `2026-03-06T12:52:20Z` `sa_browser_capture` for `https://.../intel/bookmarklet`
  - `2026-03-06T12:53:26Z` `intel_analysis` for the same install page
- Patched `app/web/static/sa_bookmarklet.js` to hard-refuse execution unless `window.location.hostname` is `seekingalpha.com` or a subdomain
- Bumped bookmarklet version to `2026-03-06T13:03Z`
- Added regression coverage in `tests/test_sa_browser_capture.py` to assert the shipped JS contains the Seeking Alpha host guard
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `8 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** The bookmarklet will no longer poison the research/event store when clicked on the install page or any other non-Seeking-Alpha page. User must refresh `/intel/bookmarklet` and re-drag the bookmark because the version changed again.
**Next steps:** Replace the bookmark with version `2026-03-06T13:03Z`, run it on a real `seekingalpha.com/symbol/...` page, then inspect whether the newest MU capture contains a plausible quant score close to the on-page `4.99/5`.

## 2026-03-06 13:21 — Retest still used stale bookmarklet, no new MU capture

**Context:** User said they re-ran the bookmarklet after the non-Seeking-Alpha host guard was added and version bumped to `2026-03-06T13:03Z`.
**Work done:**
- Queried `trades.db` again for the latest `research_events`, `feature_records`, and SA-related `bot_events`
- Confirmed there is still no new `sa_browser_capture` event for `MU` after the old valid run at `2026-03-06T12:23:10Z`
- Confirmed the newest SA-related events are again bogus install-page captures:
  - `2026-03-06T13:19:00Z` bot events for `SBRC`
  - `2026-03-06T13:19:14Z` `intel_analysis` on `/intel/bookmarklet`
- Confirmed the newest bogus install-page `sa_browser_capture` still uses bookmarklet version `2026-03-06T12:38Z`, not the patched `2026-03-06T13:03Z`
- Reconfirmed the last valid MU capture remains the older bad parse (`rating='quant ratingquant rating'`, `quant_score=8.0`)
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** The user/browser is still running a stale bookmarklet. The new host guard code is not what executed, so there is no fresh evidence yet for the improved extractor.
**Next steps:** User must hard-refresh `/intel/bookmarklet`, verify the page itself shows version `2026-03-06T13:03Z`, delete the bookmark, re-drag it, then run it only on a real `seekingalpha.com/symbol/...` page. After that, inspect `trades.db` again for a new MU capture carrying `bookmarklet_version='2026-03-06T13:03Z'`.

## 2026-03-06 13:55 — Added bookmarklet debug beacon because valid-version clicks still produced no backend write

**Context:** User reported they re-dragged the correct bookmarklet, but `trades.db` still showed no new `MU` capture and no events carrying the latest version. The last valid MU capture remains the old bad parse from `2026-03-06T12:23:10Z`.
**Work done:**
- Reconfirmed there were no new SA capture rows, no new MU rows, and no new bot events after the old bogus install-page capture at `2026-03-06T13:19Z`
- Searched for alternate DB files and verified the app code writes to `/home/runner/workspace/trades.db`
- Added a lightweight debug endpoint `GET /api/webhooks/sa_debug_ping` in `app/api/server.py`
- Updated `app/web/static/sa_bookmarklet.js` to send image-beacon debug pings at key stages:
  - `start`
  - `blocked_non_sa`
  - `no_payload`
  - `pre_post`
  - `post_ok`
  - `post_fail`
  - `exception`
- Bumped bookmarklet version to `2026-03-06T13:52Z`
- Added regression coverage for the debug ping route and for the shipped bookmarklet containing the debug function
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `9 passed`
**Key files touched:** `app/api/server.py`, `app/web/static/sa_bookmarklet.js`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** We still do not have a fresh valid MU capture after the extractor fixes. However, the next test will now reveal whether the click is executing at all, whether it passes the Seeking Alpha host guard, and whether it reaches the backend before/after the JSON POST.
**Next steps:** User must replace the bookmark again from `/intel/bookmarklet` version `2026-03-06T13:52Z`, click it on a real `seekingalpha.com/symbol/MU` page, then inspect recent `bot_events` for `strategy='sa_debug_ping'` plus any new `sa_quant_capture` rows.

## 2026-03-06 14:04 — Manual debug endpoint hit also produced no event; backend process is stale or different instance

**Context:** User said they definitely used the latest bookmarklet code and also manually hit the debug endpoint before the latest check.
**Work done:**
- Re-queried `bot_events` for `strategy in ('sa_debug_ping','sa_quant_capture','sa_intel')`
- Re-queried `research_events` for latest SA/intel rows
- Confirmed there are still no `sa_debug_ping` events at all and no new SA rows after the old `2026-03-06T13:19Z` bogus install-page capture
- Inspected Replit workflow config in `.replit`
- Confirmed the app is started by the `Start application` workflow with:
  - `BOT_UI_HOST=0.0.0.0 BOT_UI_PORT=5000 python3 run_console.py`
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** This is now a backend/runtime issue, not a bookmarklet-source issue. The public app serving the user’s URL is almost certainly not running the latest `server.py` routes yet, even if `/intel/bookmarklet` shows the latest JS version from disk.
**Next steps:** Restart the Replit application process via the configured workflow/command, then manually hit `/api/webhooks/sa_debug_ping?...` once before testing the bookmarklet again. Only after a `sa_debug_ping` event appears should the bookmarklet be retested.

## 2026-03-06 14:08 — Manual debug ping succeeded after app restart

**Context:** User restarted the Replit app and manually opened the new debug endpoint.
**Work done:**
- Re-queried `bot_events` for `strategy in ('sa_debug_ping','sa_quant_capture','sa_intel')`
- Confirmed a new debug event appeared:
  - `2026-03-06T13:52:39.851121`
  - `strategy='sa_debug_ping'`
  - `headline='SA bookmarklet ping: manual'`
  - `detail='v=probe, host=seekingalpha.com, page_type=symbol, url=https://seekingalpha.com/symbol/MU'`
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** The live Replit backend is now confirmed to be running the updated `server.py` routes and writing to the same `trades.db` being inspected locally. The blocker has shifted back to the bookmarklet execution path itself.
**Next steps:** User should run the bookmarklet again on a real `seekingalpha.com/symbol/MU` page using version `2026-03-06T13:52Z`, then inspect `sa_debug_ping` stages plus any resulting `sa_browser_capture` / `signal_layer` rows.

## 2026-03-06 14:18 — Bookmarklet reached `start` only; switched symbol crawl to safer route-only behavior

**Context:** After the app restart, a manual `sa_debug_ping` succeeded. User then ran the bookmarklet on `https://seekingalpha.com/symbol/MU` using version `2026-03-06T13:52Z`.
**Work done:**
- Queried `bot_events` and `research_events` immediately after the click
- Confirmed the bookmarklet now starts and reaches the backend: a new debug event was logged
  - `headline='SA bookmarklet ping: start'`
  - `detail='v=2026-03-06T13:52Z, host=seekingalpha.com, url=https://seekingalpha.com/symbol/MU'`
- Confirmed it still never reached `pre_post`, `post_ok`, `post_fail`, or `exception`
- Inferred the bookmarklet was stalling/dying during the live symbol-page crawl, most likely because clicking one of the discovered tabs triggered a real page navigation and killed the script before the webhook POST
- Patched `app/web/static/sa_bookmarklet.js` to make symbol crawling safer:
  - bumped version to `2026-03-06T14:15Z`
  - added `fetchWithTimeout()` with a 6-second timeout for same-origin route fetches
  - added `isSafeInPageTab()` and now skips risky navigational tabs instead of clicking them
  - added more debug beacon stages around symbol crawl: `symbol_enrich_start`, `symbol_routes_collected`, `symbol_enrich_done`
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `9 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** The bookmarklet is definitely executing on SA and talking to the backend. The remaining blocker is inside symbol-page traversal, and the crawler is now changed to avoid live-page navigation side effects.
**Next steps:** Hard refresh `/intel/bookmarklet`, replace the bookmark with version `2026-03-06T14:15Z`, run it once on `seekingalpha.com/symbol/MU`, then inspect `sa_debug_ping` stages and any new `sa_browser_capture` row.

## 2026-03-06 14:33 — Route-only crawler completed end-to-end; quant extraction still wrong, parser tightened again

**Context:** User reported the new bookmarklet run behaved oddly: it ran quickly, showed a green status box, but did not visibly render the tabs. This matched the intended route-fetch approach.
**Work done:**
- Queried `bot_events` and `research_events` immediately after the MU run
- Confirmed the `2026-03-06T14:15Z` bookmarklet now completed end-to-end:
  - `start`
  - `symbol_routes_collected`
  - `symbol_enrich_start`
  - `symbol_enrich_done`
  - `pre_post`
  - `SA capture received: MU`
  - `post_ok`
- Confirmed the odd UI behavior is expected because the crawler now fetches symbol subroutes in the background instead of visibly navigating the live page
- Confirmed extraction is still wrong even though transport/crawl are working:
  - MU capture row at source_ref `https://seekingalpha.com/symbol/MU` was upserted with `updated_at=2026-03-06T13:58:31.095748`
  - parsed `rating='sell'`
  - parsed `quant_score=1.0`
  - downstream `signal_layer` score became `47.5`
- Identified two concrete parser issues from the stored payload:
  - base/root symbol page quant parsing is noisy and should not be trusted unless the route is explicitly quant-related
  - `mergeData()` was dropping `bookmarklet_version`, which is why the stored MU capture had an empty version even though `scan_debug` showed `2026-03-06T14:15Z`
- Patched `app/web/static/sa_bookmarklet.js` again:
  - bumped version to `2026-03-06T14:30Z`
  - base symbol pages no longer count as `quant` context unless the URL/route itself is quant-related
  - quant rating regex narrowed to explicit `Quant Rating/Recommendation` patterns
  - quant score regex narrowed to decimal or `/5` style values to avoid table-count noise like isolated `1`
  - derive rating from a valid 1-5 quant score when explicit text rating is absent
  - preserve `bookmarklet_version` across merges so stored captures record the actual bookmark version used
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `9 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** Bookmarklet transport and crawl are working on live SA symbol pages. Remaining issue is extraction quality only. The next version should reduce root-page noise and stop accepting isolated integer matches like `1` as the quant score.
**Next steps:** Replace the bookmark with version `2026-03-06T14:30Z`, run it once more on `seekingalpha.com/symbol/MU`, then inspect whether the stored MU capture now has a plausible `quant_score` and correctly records the bookmarklet version.

## 2026-03-06 14:47 — `14:30Z` run still missed quant; switched route scraping to hidden iframe render

**Context:** User ran the `2026-03-06T14:30Z` bookmarklet on `MU`.
**Work done:**
- Queried `bot_events` and `research_events` immediately after the run
- Confirmed the run completed end-to-end:
  - `start`
  - `symbol_routes_collected`
  - `symbol_enrich_start`
  - `symbol_enrich_done`
  - `pre_post`
  - `SA capture received: MU`
  - `post_ok`
- Confirmed the new MU capture row was upserted correctly with `bookmarklet_version='2026-03-06T14:30Z'`
- Confirmed the parser improvement removed the bogus `sell / 1.0` quant reading, but it still failed to extract any quant value:
  - `rating=''`
  - `quant_score=None`
  - bot event showed `has_quant=False`
  - no new `signal_layer` event was created from the `14:30Z` run
- Confirmed the route-fetch summaries still showed empty quant data on `/ratings/quant-ratings`, implying raw fetched HTML alone is insufficient for quant extraction
- Patched `app/web/static/sa_bookmarklet.js` again:
  - bumped version to `2026-03-06T14:44Z`
  - route scraping now tries a hidden same-origin iframe first, waits for client-side render, then scrapes the iframe DOM
  - falls back to raw HTML fetch only if iframe loading fails
  - stores per-route method/fallback info in `scan_debug.route_tabs`
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `9 passed`
**Key files touched:** `app/web/static/sa_bookmarklet.js`, `tests/test_sa_browser_capture.py`, `.claude/history/SESSION_LOG.md`
**Current state:** Transport is solid and the bogus root-page quant misread is gone. Remaining issue is that background raw HTML fetches do not expose the quant block, so the new approach now relies on hidden iframe render to capture post-hydration DOM.
**Next steps:** Replace the bookmark with version `2026-03-06T14:44Z`, run it once more on `seekingalpha.com/symbol/MU`, then inspect whether the quant route now yields a plausible `quant_score` near the live page value.

## 2026-03-06 14:25 — `14:44Z` run recovered live quant rating via hidden iframe, but numeric score still missing

**Context:** User ran the `2026-03-06T14:44Z` bookmarklet on `MU` after the route scraper was changed to use hidden same-origin iframes.
**Work done:**
- Queried `bot_events` and `research_events` immediately after the run
- Confirmed the run completed end-to-end:
  - `start`
  - `symbol_enrich_start`
  - `symbol_routes_collected`
  - `symbol_enrich_done`
  - `pre_post`
  - `SA capture received: MU`
  - `post_ok`
- Confirmed the updated MU capture row now has:
  - `bookmarklet_version='2026-03-06T14:44Z'`
  - `rating='strong buy'`
  - `quant_score=None`
  - factor grades present
- Confirmed `scan_debug.route_tabs` now shows `method='iframe'` for the key symbol routes, including `/ratings/quant-ratings`
- Confirmed the hidden-iframe scrape recovered a plausible live quant rating from the quant route:
  - route summary for `/ratings/quant-ratings` reported `rating='strong buy'`
- Confirmed a new downstream `signal_layer` event was created:
  - `score=90.0`
  - `rating='strong buy'`
  - `quant_score_raw=None`
- Remaining gap: the numeric quant score near the live `4.99/5` still is not being extracted, even though the qualitative rating is now credible
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Browser-assisted SA symbol ingestion now works reliably enough to produce a credible live quant rating and downstream signal from the MU page. The remaining defect is numeric-score extraction only.
**Next steps:** Tighten selectors/regex specifically against the iframe-rendered quant route DOM so the bookmarklet captures the displayed numeric quant score in addition to the rating.

## 2026-03-06 15:03 — X/Twitter -> Telegram -> LLM council pipeline fully working

**Context:** X integration had been built in a prior session (lost to crash) but the live server wasn't fetching tweet content — council only received bare URLs, producing useless 5-7% confidence analyses. User restarted Claude to debug.
**Work done:**
- Confirmed the full X integration code already existed in `server.py`: `_fetch_tweet_from_url()`, `_fetch_single_tweet()`, `_fetch_thread()`, `_get_x_oauth()` with lazy `.env` reload
- Confirmed all 5 X API credentials (consumer key/secret, access token/secret, bearer) are in `.env` and loaded by `config.py`
- Confirmed the API works from Python shell (200 OK, full tweet + thread returned)
- Identified the live server was returning 401 Unauthorized — Replit agent had a credential loading issue
- Added diagnostic endpoint `/api/debug/tweet_fetch` to isolate the problem (showed oauth available but API 401)
- User got Replit agent to fix the credential loading in the live process
- After fix + restart: forwarded the same X link via Telegram
- **Result:** LLM council received full tweet content + thread (8 replies) from `@JuneGoh_Sparta` about Asian refinery feedstock shortages
- Council produced: **46.7% confidence, 3 models, 2 trade ideas** (Long CL=F crude oil, medium conviction)
- Cleaned up: removed debug endpoint from `server.py`
- Added exception logging around tweet fetch in Telegram handler
**Key files touched:** `app/api/server.py`, `.claude/history/SESSION_LOG.md`
**Current state:** Full pipeline working: Telegram X link -> extract tweet ID -> X API v2 fetch (tweet + thread + media) -> LLM council (Claude + ChatGPT + Grok) -> trade ideas + risk factors -> Telegram notification + DB persistence
**Next steps:** Test with more X links to validate robustness, consider adding ticker extraction from tweet content

## 2026-03-06 15:30 — LLM Council UI page built

**Context:** User wants to see what the LLM council discussed and decided, challenge their ideas, and have a pipeline from idea → backtest → paper → live.
**Work done:**
- Created new `/intel` page with sidebar nav item (between Research and Incidents)
- Built `_intel_council.html` fragment — main council feed showing:
  - Source badges (X/SA/TG), timestamp, confidence bar with color coding
  - Per-model summaries (Claude/ChatGPT/Grok/Gemini) with color-coded model names
  - Trade idea cards: ticker, direction badge, conviction, timeframe, thesis, entry trigger, invalidation
  - Pipeline stage tracker per idea: idea → review → backtest → paper → live
  - Collapsible risk factors section
  - Challenge button — opens inline form to question/dispute the analysis
  - Source link to original content
- Built `_intel_pipeline_summary.html` — right sidebar with:
  - Pipeline stage counts (idea/review/backtest/paper/live)
  - Top ideas table sorted by confidence
  - Stats: total analyses, total ideas, avg confidence, unique tickers
- Added 5 new endpoints:
  - `GET /intel` — council page
  - `GET /fragments/intel-council` — council feed fragment (HTMX, auto-refresh 30s)
  - `GET /fragments/intel-pipeline-summary` — pipeline sidebar fragment
  - `POST /api/intel/challenge` — challenge an analysis, re-runs through council with user's question
  - `POST /api/intel/submit` — submit content directly from UI (handles X link fetching)
- Quick Submit box on the page — paste text or X links for instant council analysis
- All endpoints tested via TestClient: 200 OK, content verified
- 104 focused tests passing
**Key files created:** `app/web/templates/intel_council_page.html`, `app/web/templates/_intel_council.html`, `app/web/templates/_intel_pipeline_summary.html`
**Key files modified:** `app/api/server.py`, `app/web/templates/base.html`, `.claude/history/SESSION_LOG.md`
**Current state:** Intel Council page is functional. Shows all council analyses with full detail, per-model breakdowns, trade ideas with pipeline tracking, and challenge capability. Pipeline stages are currently all "idea" — workflow progression (review→backtest→paper→live) is the next step.
**Next steps:** Restart app, test the /intel page live. Then build the workflow progression: promote ideas to backtest, trigger backtests, paper trade results.

## 2026-03-06 15:45 — LLM council upgraded: thinking models, fund context, debate round, linked content

**Context:** User pointed out the council was using old models, had a generic prompt, weren't debating, and weren't fetching linked articles from tweets.
**Work done:**
- Upgraded models:
  - Claude: `claude-sonnet-4` → `claude-opus-4` with extended thinking (10k token budget)
  - ChatGPT: `gpt-4o` → `o3` with high reasoning effort
  - Grok: `grok-3` (already best available)
  - Gemini: `gemini-2.0-flash` → `gemini-2.5-pro` with thinking budget
- Added rich fund context system prompt (FUND_CONTEXT): BoxRoomCapital profile, strategies, assets, execution approach, risk parameters, signal layers — so models know what fund they're analyzing for
- Enriched Round 1 prompt: now asks for specific instruments, UK spread bet availability, time-sensitivity assessment
- Added Round 2 debate: each model sees other models' Round 1 verdicts and provides revised assessment with agreements, disagreements, and revised confidence
- Final confidence is 70% weighted to Round 2 (post-debate) and 30% to Round 1
- Added `_fetch_linked_content()`: automatically fetches article content from URLs in tweets (up to 3 links, strips HTML)
- Content limit raised from 12k to 16k chars
- Updated UI template `_intel_council.html` to show debate section with per-model color-coded challenge cards
- Added `debate_summary` and `debate_parts` parsing to the fragment endpoint
- All 120 tests passing
**Key files modified:** `intelligence/intel_pipeline.py` (full rewrite), `app/api/server.py`, `app/web/templates/_intel_council.html`, `.claude/history/SESSION_LOG.md`
**Current state:** Council is significantly upgraded. Next test will use thinking models and include a debate round. API keys: Anthropic ✓, OpenAI ✓, xAI ✓, Google ✗.
**Next steps:** Restart app, forward an X link via Telegram, verify the council produces a debate round with higher-quality analysis.

## 2026-03-06 15:22 — Real SA ratings-history payload identified; switched to network-capture path

**Context:** User surfaced the actual Seeking Alpha ratings-history JSON response for `MU`. The payload includes `attributes.ratings.quantRating=4.9968...`, `authorsRating`, `sellSideRating`, and numeric factor-grade fields (`growthGrade`, `valueGrade`, etc.). This is the stable source we needed and is materially better than DOM/tab scraping.
**Work done:**
- Updated `intelligence/scrapers/sa_adapter.py` to accept the real SA ratings-history shape directly:
  - added numeric grade mapping `1..13 -> F..A+`
  - added score-to-rating derivation for numeric ratings
  - added support for `sa_history` / `sa_rating_history_entry` payloads
  - maps nested `attributes.ratings.*` into quant score, quant rating, author rating, wall-st rating, and factor grades
- Added regression coverage in `tests/test_sa_browser_capture.py` for the real ratings-history payload shape
- Added a minimal unpacked Chrome extension at `browser_extensions/sa_network_capture/`:
  - `content.js` injects a page hook at `document_start`
  - `page_hook.js` intercepts page `fetch` and `XMLHttpRequest` responses
  - detects the SA ratings-history JSON by shape, not brittle DOM selectors
  - normalizes and forwards it to `/api/webhooks/sa_quant_capture`
  - uses an extension options page to store the BRC endpoint
- Added install/use docs in `browser_extensions/sa_network_capture/README.md`
- Re-verified:
  - `node --check app/web/static/sa_bookmarklet.js`
  - `node --check browser_extensions/sa_network_capture/content.js`
  - `node --check browser_extensions/sa_network_capture/page_hook.js`
  - `node --check browser_extensions/sa_network_capture/options.js`
  - `pytest -q tests/test_sa_browser_capture.py` -> `10 passed`
**Key files touched:** `intelligence/scrapers/sa_adapter.py`, `tests/test_sa_browser_capture.py`, `browser_extensions/sa_network_capture/*`, `.claude/history/SESSION_LOG.md`
**Current state:** We now have a better collection path than the bookmarklet/DOM crawler: capture the page's own authenticated ratings JSON from Chrome and feed it directly into the existing SA webhook. The backend parser accepts this shape.
**Next steps:** Load the unpacked extension in Chrome, set the Replit endpoint in extension options, open a SA symbol page, and confirm the extension posts a new `sa_browser_capture` row containing a numeric quant score near `4.99/5`.

## 2026-03-06 15:44 — Full SA symbol snapshot path added via Chrome extension aggregator

**Context:** The direct Seeking Alpha ratings-history JSON response proved to be the stable source of truth. User wanted the whole symbol page and tab set, not just quant. The bookmarklet/DOM path was no longer the right abstraction.
**Work done:**
- Refactored `app/api/server.py` so SA browser capture storage/signaling now goes through a shared helper instead of duplicating logic in the quant webhook
- Added `POST /api/webhooks/sa_symbol_capture`:
  - stores the raw aggregated symbol snapshot as `sa_symbol_capture`
  - accepts `summary`, `sections`, and `raw_responses`
  - derives the existing `sa_browser_capture` + `signal_layer` events from the normalized summary when quant fields are present
- Updated source handling so network-extension captures are stored with source `sa-network-extension` instead of being hardcoded to the bookmarklet source
- Extended the Chrome extension in `browser_extensions/sa_network_capture/`:
  - manifest bumped to `0.2.0`
  - content script now runs in all frames
  - `page_hook.js` now coordinates full symbol collection from the top frame
  - opens hidden same-origin symbol routes (ratings, valuation, earnings, dividends, financials, peers)
  - collects JSON responses from top frame + subframes, merges them into one symbol snapshot, and posts once to `/api/webhooks/sa_symbol_capture`
- Updated extension docs in `browser_extensions/sa_network_capture/README.md`
- Added regression test covering the new symbol snapshot webhook path in `tests/test_sa_browser_capture.py`
**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/options.js`
- `python -m py_compile app/api/server.py intelligence/scrapers/sa_adapter.py tests/test_sa_browser_capture.py`
- `pytest -q tests/test_sa_browser_capture.py` -> `11 passed`
**Key files touched:** `app/api/server.py`, `tests/test_sa_browser_capture.py`, `browser_extensions/sa_network_capture/manifest.json`, `browser_extensions/sa_network_capture/page_hook.js`, `browser_extensions/sa_network_capture/README.md`, `.claude/history/SESSION_LOG.md`
**Current state:** The preferred SA collection path is now the Chrome extension aggregator, not the bookmarklet. The backend can store a raw multi-section symbol snapshot and still feed the existing SA quant signal path from the normalized summary.
**Next steps:** Reload the unpacked extension in Chrome, open a real SA symbol page, and confirm that a new `sa_symbol_capture` row lands with multiple sections and raw responses, plus the derived `sa_browser_capture` / `signal_layer` rows.

## 2026-03-08 12:00 — Research System Architecture & Tech Spec

**Context:** User provided ChatGPT Pro follow-up research (solo operator evidence + strategy feasibility map), asked Claude to save it, assess impact on plans, update the research system plan, then produce full architecture + detailed tech spec.
**Work done:**
- Saved follow-up as `ops/RESEARCH_FOLLOWUP_SOLO_OPS_AND_STRATEGY_MAP.md`
- Assessed impact: validates existing plan, adds strategy expansion roadmap, LLM budget guidance, crypto lane refinement, decay-triggered review emphasis
- Updated `ops/RESEARCH_SYSTEM_PLAN_FINAL.md`:
  - Added Strategy Expansion Roadmap (MVP through Phase 4+ with "never" list)
  - Added LLM Budget Allocation Guidance table
  - Added Decay-Triggered Review as deterministic service
  - Added 2 new consensus principles
  - Narrowed crypto exclusion to token speculation only (CME basis/carry allowed Phase 3)
  - Updated source documents table
- Created `ops/RESEARCH_SYSTEM_ARCHITECTURE.md` — full system architecture:
  - Twin-engine design (Engine A: futures trend/carry, Engine B: equity event/NLP)
  - PostgreSQL storage (JSONB for artifacts + relational for ops)
  - Model Router with configurable provider per service
  - Shared promotion gate, kill monitor, decay review
  - Integration plan with existing L1-L8 signal system
  - Module structure (~30 new files)
- Created `ops/RESEARCH_SYSTEM_TECH_SPEC.md` — detailed technical specification:
  - Phase 1: All artifact Pydantic schemas + PostgreSQL artifact store
  - Phase 2: Model router + full challenge pipeline (replace council)
  - Phase 3: Edge taxonomy enforcement
  - Phase 4: Deterministic regime classifier + LLM journal
  - Phase 5: Cost model (IG/IBKR/futures) + experiment service
  - Phase 6: Kill monitor + retirement formalization
  - Phase 7: Decay-triggered review → promotion gate wiring
  - Engine A: Trend/carry/value/momentum signals + vol-target portfolio construction
  - Engine B: Expression service + synthesis + post-mortem
  - DB migration plan (PostgreSQL alongside SQLite)
  - Testing strategy (~285 new tests)
- Also: checked Codex progress on architecture plan, set up auto-memory files
**Key files created:**
- `ops/RESEARCH_FOLLOWUP_SOLO_OPS_AND_STRATEGY_MAP.md`
- `ops/RESEARCH_SYSTEM_ARCHITECTURE.md`
- `ops/RESEARCH_SYSTEM_TECH_SPEC.md`
- `/home/runner/.claude/projects/-home-runner-workspace/memory/MEMORY.md`
- `/home/runner/.claude/projects/-home-runner-workspace/memory/architecture.md`
- `/home/runner/.claude/projects/-home-runner-workspace/memory/pitfalls.md`
**Key files modified:**
- `ops/RESEARCH_SYSTEM_PLAN_FINAL.md` (strategy roadmap, LLM budget, decay review, crypto scope)
**Current state:** Architecture and tech spec ready for review. All 7 phases specified in detail with artifact schemas, service interfaces, prompt templates, test strategy, and acceptance criteria. Codex is separately working on P0-P6 architecture cleanup (server.py/trade_db.py decomposition — ~50-60% done, currently testing).
**Next steps:** Review architecture + tech spec. Decide on PostgreSQL provisioning. Begin Phase 1 build once Codex completes P4 (intel pipeline refactor).

## 2026-03-06 17:25 — Live extension transport confirmed; switched collector from hidden iframes to visible tab traversal

**Context:** First live Chrome extension run successfully wrote `sa_symbol_capture` with source `sa-network-extension`, proving end-to-end transport worked. However, the hidden-iframe collector only captured generic API responses (`symbol_data`, `market_open`, etc.) and missed the rich ratings-history payload.
**Work done:**
- Confirmed the endpoint handoff bug: the content script could dispatch the endpoint event before `page_hook.js` had registered its listener, which is why manual console injection was needed
- Fixed `browser_extensions/sa_network_capture/content.js` so it re-dispatches the saved endpoint after the page hook loads
- Reworked `browser_extensions/sa_network_capture/page_hook.js`:
  - filters out low-value SA noise endpoints (`ab_tests`, `breaking_news`, `market_open`, `visitor_tag_for_url_params`, px collector)
  - allows re-posting the symbol snapshot when richer records arrive later instead of posting only once
  - replaced hidden iframe route loading with sequential visible-tab traversal on the real symbol page, because that is the execution path where Seeking Alpha previously emitted the good ratings payloads
- Bumped extension manifest version to `0.2.1`
- Repacked the extension archive as `browser_extensions/sa_network_capture_v0.2.1.zip`
**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/options.js`
- `pytest -q tests/test_sa_browser_capture.py` -> `11 passed`
**Current state:** We now know the extension can post into the backend. The collector strategy has been updated to follow the visible SA tab flow rather than the hidden-frame flow that only produced generic API traffic.
**Next steps:** User should reload the unpacked Chrome extension from the new `0.2.1` build, reload the MU page, and watch for a new `sa_symbol_capture` containing ratings/quant sections rather than only generic `symbol` records.

## 2026-03-06 ~09:30 — Idea Pipeline Progression (Full Build)
**Context:** User wanted trade ideas from LLM council to progress through lifecycle stages (idea -> review -> backtest -> paper -> live) instead of being static
**Work done:**
- Designed full architecture with 3 parallel research agents + 1 plan agent
- Saved implementation plan to `ops/collab/IDEA_PIPELINE_PLAN.md` for Codex handoff
- Step 1: Added `trade_ideas` + `idea_transitions` tables to `data/trade_db.py` with full CRUD (7 functions)
- Step 2: Added config vars to `config.py` (soak hours, stake, backtest thresholds)
- Step 3: Built `intelligence/idea_pipeline.py` — IdeaPipelineManager class with:
  - Stage transition validation with gate criteria per stage
  - Backtest integration (mapped strategies via Backtester + generic momentum proxy)
  - Paper trade integration via PaperBroker
  - Backfill from existing analyses
- Step 4: Modified `intelligence/intel_pipeline.py` to auto-seed trade_ideas DB on council analysis
- Step 5: Added 15+ API endpoints to `app/api/server.py` (REST + HTMX fragments)
- Step 6: Built 3 new templates (`_idea_actions.html`, `_idea_detail.html`, `_idea_pipeline_board.html`)
  - Updated `_intel_council.html` with interactive pipeline actions
  - Updated `intel_council_page.html` with kanban board section
  - Updated pipeline summary fragment to use real DB counts
- Step 7: Wrote 30 tests in `tests/test_idea_pipeline.py` — all passing
- Full test suite: 2252 passed, 0 failures
**Key files touched:** `data/trade_db.py`, `config.py`, `intelligence/idea_pipeline.py` (new), `intelligence/intel_pipeline.py`, `app/api/server.py`, `_idea_actions.html` (new), `_idea_detail.html` (new), `_idea_pipeline_board.html` (new), `_intel_council.html`, `intel_council_page.html`, `tests/test_idea_pipeline.py` (new), `ops/collab/IDEA_PIPELINE_PLAN.md` (new)
**Current state:** All code complete and tested. Needs app restart + backfill of existing analyses to see ideas in the pipeline board. UI has interactive promote/reject/backtest/paper buttons per stage.
**Next steps:** Restart app, hit `/api/ideas/backfill` to seed existing ideas, test the pipeline UI on `/intel`, send a new X link to verify new ideas auto-seed

## 2026-03-06 17:33 — Added explicit extension-side debug beacons for SA network capture

**Context:** User confirmed the 0.2.1 extension visibly moved tabs, which indicates the client-side collector path executed. However, there was still no new backend write after that run, so the failure had to be between endpoint handoff, pre-post assembly, and POST execution.
**Work done:**
- Bumped the Chrome extension to `0.2.2`
- Added explicit console logs in `browser_extensions/sa_network_capture/page_hook.js`:
  - `[BRC SA] hook active ...`
  - `[BRC SA] endpoint set/missing ...`
  - `[BRC SA] tab scan ...`
  - `[BRC SA] posting snapshot ...`
  - existing success/failure logs remain
- Added lightweight debug beacons to `/api/webhooks/sa_debug_ping` for stages:
  - `endpoint_set`
  - `tab_scan`
  - `pre_post`
  - `post_ok`
  - `post_fail`
- Repacked the extension as `browser_extensions/sa_network_capture_v0.2.2.zip`
**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
**Current state:** Next live run should tell us exactly where the client dies instead of relying on absence of DB writes.

## 2026-03-06 18:24 — Moved SA extension network delivery into the extension background worker

**Context:** Live `0.2.2` console logs proved the page script reached `posting snapshot`, but no new backend rows or debug pings landed. That strongly indicated the cross-origin requests were being blocked or dropped in the Seeking Alpha page context.
**Work done:**
- Bumped the unpacked Chrome extension to `0.2.3`
- Added `browser_extensions/sa_network_capture/background.js` as a Manifest V3 service worker
- Moved all network delivery into the extension context:
  - `page_hook.js` now emits `brc-sa-request` events instead of calling `fetch` directly
  - `content.js` bridges those requests to `chrome.runtime.sendMessage`
  - `background.js` performs the actual POST to `/api/webhooks/sa_symbol_capture` and GET to `/api/webhooks/sa_debug_ping`
- Updated manifest host permissions to allow extension-side delivery to the configured endpoint
- This should bypass Seeking Alpha page-level CSP / connect restrictions that were likely preventing the browser from reaching the Replit endpoint from page JS
**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/background.js`
- `node --check browser_extensions/sa_network_capture/options.js`
- Repacked extension archive: `browser_extensions/sa_network_capture_v0.2.3.zip`
**Current state:** The page collector still assembles the symbol snapshot, but the POST path now runs from the extension service worker rather than the Seeking Alpha page environment.
**Next steps:** User should load `0.2.3`, open `MU`, and report the new `BRC SA` console lines. The backend should now either receive the snapshot or return a concrete HTTP error back to the service worker.

## 2026-03-06 21:35 — Hardened SA symbol capture with API-first collection and server-side normalization

**Context:** `0.2.3` finally delivered live symbol snapshots from the Chrome extension, but the collector was still behaving like a UI scraper: it over-captured unrelated account/marketing traffic, attributed many responses to a generic `symbol` bucket, and relied on visible tab movement for discovery. The user asked for a maximum-effort pass that would make Seeking Alpha capture materially more reliable.

**Work done:**
- Refactored `browser_extensions/sa_network_capture/page_hook.js` to prefer direct authenticated same-origin API fetches for stable symbol endpoints instead of depending on visible tab traversal:
  - ratings history
  - relative rankings
  - primary price
  - valuation metrics
  - capital structure
  - 5Y valuation averages
  - ticker metric grades
  - sector metrics
  - earnings estimates (when `tickerId` is available)
- Tightened client-side filtering so unrelated SA APIs like account, marketing, tooltip, and inbox traffic are no longer treated as meaningful symbol payloads.
- Reduced duplicate raw captures by deduping on `section + response_url + history_id` rather than route context.
- Kept visible-tab traversal only as a fallback path when the direct API plan does not populate the expected core sections.
- Canonicalized the symbol URL in the collector so captures stay anchored to `/symbol/<TICKER>` even if the page is currently on a deeper subroute.
- Added server-side normalization in `intelligence/scrapers/sa_adapter.py` via `normalize_sa_symbol_snapshot(...)` so `sa_symbol_capture` rows now derive structured sections from raw API payloads, including:
  - `ratings_history`
  - `relative_rankings`
  - `price`
  - `price_history`
  - `valuation_metrics`
  - `valuation_averages_5y`
  - `capital_structure`
  - `metric_grades`
  - `sector_metrics`
  - `earnings_estimates`
  - `ownership`
- Updated `/api/webhooks/sa_symbol_capture` in `app/api/server.py` to:
  - normalize raw symbol snapshots before storage
  - persist `normalized_sections` on the stored event payload
  - enrich the derived summary with ranks, price, and normalized section names before creating the downstream `sa_browser_capture`
- Bumped the unpacked extension to `0.3.0` and repacked it as `browser_extensions/sa_network_capture_v0.3.0.zip`.
- Updated extension docs in `browser_extensions/sa_network_capture/README.md` to reflect the API-first collector design.

**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/background.js`
- `python -m py_compile app/api/server.py intelligence/scrapers/sa_adapter.py tests/test_sa_browser_capture.py`
- `pytest -q tests/test_sa_browser_capture.py` -> `12 passed`
- Re-ran `normalize_sa_symbol_snapshot(...)` against the latest live MU capture already in `trades.db`; it successfully derived:
  - `quant_score = 4.996835443037975`
  - `rating = strong buy`
  - `sector_rank = 2`
  - `industry_rank = 1`
  - `primary_price = 397.05`
  - normalized sections for valuation, rankings, grades, estimates, price history, and ownership

**Current state:**
- The extension/server path is now API-first rather than DOM-first.
- Raw symbol snapshots are still preserved, but the backend now stores a structured normalized view as well.
- The next live user test should be done with `sa_network_capture_v0.3.0.zip` loaded into Chrome.

## 2026-03-06 22:36 — Live `0.3.0` SA symbol capture succeeded with normalized sections

**Context:** After the API-first refactor and server-side normalization work, the user loaded `sa_network_capture_v0.3.0.zip` into Chrome and opened the MU symbol page. Console logs showed `hook active`, `endpoint set`, an 8-route API fetch plan, `posting snapshot MU 9 14`, and `symbol snapshot captured MU 9`.

**Live result in `trades.db`:**
- New `sa_symbol_capture` stored at `2026-03-06T22:35:19Z`
- Source/version: `sa-network-extension`, `sa-network-extension-0.3.0`
- Raw section count: `9`
- Normalized section count: `9`
- Raw response count: `9`
- Derived `sa_browser_capture` stored at the same timestamp with:
  - `rating = strong buy`
  - `quant_score = 4.996835443037975`
- Derived `signal_layer` stored at the same timestamp with:
  - `score = 92.98`
  - `rating = strong buy`

**Normalized sections confirmed on the live payload:**
- `ratings_history`
- `relative_rankings`
- `valuation_metrics`
- `capital_structure`
- `valuation_averages_5y`
- `metric_grades`
- `sector_metrics`
- `earnings_estimates`
- `price`

**Notable extracted fields:**
- `sector_rank = 2`
- `industry_rank = 1`
- `primary_price = 397.05`
- structured valuation multiples and sector comparison metrics
- structured per-metric grades for `main_quant` and `dividends`
- structured annual EPS estimate series and counts

**Current state:** The SA extension path is now working live as an API-first collector with structured normalized symbol snapshots, not just a bookmarklet/DOM scrape fallback.

## 2026-03-06 23:00 — Surfaced SA symbol snapshots in-app and propagated normalized context downstream

**Context:** After the live `0.3.0` extension run succeeded, the user asked to proceed with the next steps: make the new SA data visible in the app and useful to downstream strategy/research logic instead of leaving it only in raw event storage.

**Work done:**
- Propagated `normalized_sections` into the derived `sa_browser_capture` payload path:
  - `parse_sa_browser_payload(...)` now preserves `normalized_sections` and `normalized_section_names` in `snapshot.raw_fields`
  - `SABrowserCapture.to_payload()` now emits top-level `normalized_sections` when available
- Enriched L8 SA signal payload details in `intelligence/sa_quant_client.py` with compact derived context from browser-captured SA snapshots:
  - `primary_price`
  - `overall_rank`
  - `sector_name`
  - `industry_name`
  - `section_names`
- Updated `/api/webhooks/sa_symbol_capture` so normalized sections are injected into the derived summary before creating the downstream `sa_browser_capture`
- Added a reusable in-app snapshot surface:
  - `GET /api/sa/snapshots`
  - `GET /fragments/sa-symbol-captures`
  - template `app/web/templates/_sa_symbol_captures.html`
- Added the new SA snapshot panel to both:
  - `app/web/templates/intel_council_page.html`
  - `app/web/templates/research_page.html`
- The panel shows latest SA captures with:
  - quant score and rating
  - sector/industry ranks
  - price
  - valuation metrics
  - capital structure
  - main quant metric grades
  - forward EPS consensus
  - full structured normalized JSON

**Verification:**
- `python -m py_compile app/api/server.py intelligence/scrapers/sa_adapter.py intelligence/sa_quant_client.py tests/test_sa_browser_capture.py`
- `pytest -q tests/test_sa_browser_capture.py` -> `15 passed`
- Added direct tests for:
  - preserving normalized sections through `parse_sa_browser_payload(...)`
  - `/api/sa/snapshots`
  - `/fragments/sa-symbol-captures`
  - downstream `signal_layer.details` carrying `primary_price` and `section_names`

**Current state:**
- The SA symbol capture path is working live.
- The app now has a dedicated panel for inspecting normalized SA snapshots.
- The derived browser capture and signal-layer payloads now carry compact structured context, so downstream consumers do not have to reopen the parent `sa_symbol_capture` event to get basic SA rankings/price context.

## 2026-03-06 23:12 — Fixed SA extension message bridge for non-MU symbol pages

**Context:** The user tested `AAPL` with extension `0.3.0`. Page console showed:
- `hook active`
- `endpoint set`
- `api fetch plan`
- `posting snapshot AAPL 9 14`
But no `symbol snapshot captured`, no backend rows, and no `sa_debug_ping` writes landed. That proved the failure was not scraping or backend parsing; it was the page-to-extension transport bridge.

**Work done:**
- Updated the extension to `0.3.1`
- Reworked the bridge between the injected page script and the content/background scripts:
  - `page_hook.js` now sends outbound requests over both DOM custom events and `window.postMessage`
  - `page_hook.js` now listens for inbound responses and endpoint updates over both channels
  - `content.js` now forwards requests from both DOM custom events and `window.postMessage`
  - `content.js` now emits responses and endpoint updates over both channels
- Added timeout-aware network delivery in `background.js` via `AbortController`
- Added background-worker console logs:
  - `[BRC SA BG] message ...`
  - `[BRC SA BG] post_snapshot start ...`
  - `[BRC SA BG] post_snapshot ok ...`
  - `[BRC SA BG] post_snapshot transport/http error ...`
- Repacked extension archive: `browser_extensions/sa_network_capture_v0.3.1.zip`

**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/background.js`

**Current state:** `0.3.1` is the build to test next for `AAPL` and other non-MU pages. The background service worker now has explicit logging and fetch timeouts, and the page/content bridge no longer depends on one brittle custom-event path.

## 2026-03-06 23:18 — Confirmed AAPL live capture and tightened duplicate bridge handling in `0.3.2`

**Context:** After loading `0.3.1`, the user retried `AAPL`. Page console showed a complete flow ending with `symbol snapshot captured AAPL 9`. DB inspection confirmed the first non-MU live capture worked. However, the dual bridge introduced in `0.3.1` caused duplicate endpoint/pre-post/post-ok pings and duplicate `SA capture received` bot events because both the DOM-event path and `window.postMessage` path were being accepted.

**Live AAPL result (`0.3.1`):**
- New `sa_symbol_capture` at `2026-03-06T23:11:28Z`
- New derived `sa_browser_capture` at the same timestamp
- New derived `signal_layer` at the same timestamp
- Extracted summary:
  - `rating = hold`
  - `quant_score = 3.481157469717362`
  - `primary_price = 260.29`
  - `sector_rank = 85`
  - `industry_rank = 7`
- Normalized sections present:
  - `capital_structure`
  - `earnings_estimates`
  - `metric_grades`
  - `price`
  - `ratings_history`
  - `relative_rankings`
  - `sector_metrics`
  - `valuation_averages_5y`
  - `valuation_metrics`
- Derived signal payload now includes `price=260.29` and the normalized section list.

**Work done after that:**
- Bumped the extension to `0.3.2`
- Added request-id dedupe in `browser_extensions/sa_network_capture/content.js` so the same page request is forwarded only once even when both bridge channels fire
- Added endpoint dedupe in `browser_extensions/sa_network_capture/page_hook.js` so duplicate endpoint updates no longer trigger duplicate debug pings or re-start capture
- Repacked archive: `browser_extensions/sa_network_capture_v0.3.2.zip`

**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `node --check browser_extensions/sa_network_capture/background.js`

**Current state:**
- Cross-symbol live capture is now proven (`MU` and `AAPL`)
- `0.3.2` is the current stable build target because it keeps the reliable dual bridge but removes duplicate forwarding side effects.

## 2026-03-06 23:28 — Confirmed `ECO` on `0.3.2` and tightened section field whitelists

**Context:** User tested a weaker-name symbol page, `ECO`, using extension `0.3.2`. Page console showed a clean single-shot flow ending with `symbol snapshot captured ECO 9`, and the backend stored the new events successfully. The live payload also revealed that some normalized metric sections were still overinclusive because multiple SA metric responses were being merged too permissively.

**Live ECO result (`0.3.2`):**
- New `sa_symbol_capture` at `2026-03-06T23:23:30Z`
- New derived `sa_browser_capture` at the same timestamp
- New derived `signal_layer` at the same timestamp
- Extracted summary:
  - `rating = strong buy`
  - `quant_score = 4.924050632911392`
  - `primary_price = 49.206596`
  - `sector_rank = 6`
  - `industry_rank = 2`
- Normalized sections present:
  - `ratings_history`
  - `relative_rankings`
  - `valuation_metrics`
  - `capital_structure`
  - `valuation_averages_5y`
  - `metric_grades`
  - `sector_metrics`
  - `earnings_estimates`
  - `price`
- `0.3.2` also eliminated the duplicate bridge side effects seen in `0.3.1`; only one `SA capture received` event and one `post_ok` ping were written.

**Work done after inspection:**
- Tightened server-side normalization in `intelligence/scrapers/sa_adapter.py` so section payloads are filtered by explicit field whitelist:
  - `valuation_metrics` now keeps only core valuation fields
  - `valuation_averages_5y` now keeps only `_avg_5y` valuation fields
  - `capital_structure` now keeps only market-cap / TEV / cash / debt fields
  - `sector_metrics` now keeps only core valuation comparison fields
  - `price` keeps only `primary_price`
- This removes unrelated metric spillover from normalized sections.
- Added a regression assertion in `tests/test_sa_browser_capture.py` to ensure unrelated fields like `price_high_52w` do not leak into `valuation_metrics`.

**Verification:**
- `pytest -q tests/test_sa_browser_capture.py` -> `15 passed`
- Re-ran `normalize_sa_symbol_snapshot(...)` locally against the stored live ECO payload; verified:
  - `valuation_metrics` no longer contains unrelated fields like `price_high_52w`
  - `capital_structure` now contains only `marketcap`, `tev`, `total_cash`, `total_debt`

**Current state:**
- Cross-symbol live capture is now proven for `MU`, `AAPL`, and `ECO`
- `0.3.2` is the current stable extension build
- The normalization cleanup is implemented in code, but existing stored rows would need a new post-fix capture to persist the cleaner normalized sections in DB/UI.

## 2026-03-06 23:45 — Universal Seeking Alpha capture path added (`0.4.0`)

**Context:** User asked how to handle Seeking Alpha analysis and news pages as well as symbol pages, and wanted a universal SA scraper instead of maintaining separate brittle flows.

**Work done:**
- Added a universal backend route: `POST /api/webhooks/sa_page_capture`
  - dispatches symbol-style payloads into the existing `sa_symbol_capture` processing path
  - dispatches article/news payloads into the intel pipeline via a shared helper
- Refactored `app/api/server.py`:
  - added `_decode_json_request(...)`
  - added `_queue_sa_intel_payload(...)`
  - added `_handle_sa_symbol_capture_payload(...)`
  - kept legacy `sa_intel` and `sa_symbol_capture` routes working, but now both share the new helpers
- Added raw `sa_page_capture` event storage for article/news captures before queueing LLM analysis
- Extended the Chrome extension to support non-symbol pages:
  - `browser_extensions/sa_network_capture/page_hook.js` now detects `symbol`, `article`, and `news`
  - symbol pages still use the API-first collector
  - article/news pages now use a DOM + metadata extractor (title, author, canonical URL, tickers, timestamps, summary, cleaned content)
  - article/news captures are posted through the extension bridge using a new `post_page_capture` message
- Extended `browser_extensions/sa_network_capture/background.js` to forward `post_page_capture` to `/api/webhooks/sa_page_capture`
- Updated extension docs in `browser_extensions/sa_network_capture/README.md`
- Bumped extension version to `0.4.0`
- Packaged archive: `browser_extensions/sa_network_capture_v0.4.0.zip`

**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/background.js`
- `node --check browser_extensions/sa_network_capture/content.js`
- `python -m py_compile app/api/server.py tests/test_sa_browser_capture.py`
- `pytest -q tests/test_sa_browser_capture.py` -> `17 passed`

**Current state:**
- Seeking Alpha symbol capture remains stable on the extension path
- The extension architecture is now universal: one collector surface, page-type-specific extractors
- Symbol pages are API-first; article/news pages are content-first and feed the intel pipeline through the same extension
- New extension build to load in Chrome: `browser_extensions/sa_network_capture_v0.4.0.zip`

## 2026-03-07 10:58 — SA extension validation sweep

**Context:** User asked what the last SA capture work was, then requested an explicit test of the current extension code to confirm what is working and what still needs live validation.

**Work done:**
- Re-read the current universal SA capture implementation in `app/api/server.py` and the extension capture flow in `browser_extensions/sa_network_capture/`
- Confirmed the universal route split is in place:
  - symbol payloads route to `sa_symbol_capture` handling
  - article/news payloads route to `sa_page_capture` and then into the intel pipeline
- Ran JS syntax validation:
  - `node --check browser_extensions/sa_network_capture/page_hook.js`
  - `node --check browser_extensions/sa_network_capture/background.js`
  - `node --check browser_extensions/sa_network_capture/content.js`
- Ran automated backend capture tests:
  - `pytest -q tests/test_sa_browser_capture.py` -> `17 passed`
- Verified test coverage includes:
  - symbol snapshot normalization/storage
  - `sa_page_capture` article webhook queueing into intel analysis
  - `sa_page_capture` dispatching symbol-style payloads back into symbol capture handling

**Key files touched:** `.claude/history/SESSION_LOG.md`

**Current state:**
- Automated checks are green for the universal SA capture path
- Symbol-page capture remains the only path previously proven live in a real browser session
- Article/news routing is implemented and test-covered at the webhook/backend layer, but there is still no recorded live browser proof that the DOM extractor in `page_hook.js` succeeds end-to-end on real Seeking Alpha article/news pages

**Next steps:**
- Load extension build `0.4.0` in Chrome
- Test one real `seekingalpha.com/article/...` page and one real `seekingalpha.com/news/...` page
- If either fails, inspect Chrome console logs from the page and background worker, then inspect stored `sa_page_capture` events in the app

## 2026-03-07 11:03 — SA article capture timeout fix (`0.4.1`)

**Context:** User live-tested `0.4.0` on a real Seeking Alpha article page. The page console showed:
- extractor startup succeeded (`[BRC SA] hook active`)
- page type + ticker extraction succeeded (`posting page capture article TSLY`)
- final failure was `page capture failed extension transport timeout`

**Work done:**
- Confirmed the failure did not reach the backend: no new `sa_page_capture` rows were written to `trades.db`
- Traced the bug to a timeout race in the extension bridge:
  - `page_hook.js` waited only 15s for a response from the extension bridge
  - `background.js` could also spend that full 15s waiting on the POST to `/api/webhooks/sa_page_capture`
- Patched the extension:
  - `page_hook.js` bridge timeout increased to 45s
  - `background.js` POST timeout increased to 30s
  - debug ping timeout set explicitly to 10s
  - manifest version bumped to `0.4.1`
- Packaged new build: `browser_extensions/sa_network_capture_v0.4.1.zip`

**Verification:**
- `node --check browser_extensions/sa_network_capture/page_hook.js`
- `node --check browser_extensions/sa_network_capture/background.js`
- `node --check browser_extensions/sa_network_capture/content.js`

**Current state:**
- Article/news extraction logic is still believed to be correct
- The immediate blocker found in live testing was the extension transport timeout, not page-type detection or article parsing
- New build ready for retest: `0.4.1`

**Next steps:**
- Reload the extension with `0.4.1`
- Re-test the same SA article page
- If it still fails, capture the extension service worker console output (`[BRC SA BG] ...`) because the next likely fault would be endpoint/network latency rather than DOM extraction

## 2026-03-07 11:10 — SA article retest on `0.4.1`: network path still failing before backend

**Context:** User re-tested `0.4.1` on the same Seeking Alpha article and provided the page console output.

**Findings:**
- Page extractor still initializes correctly:
  - `[BRC SA] hook active sa-network-extension-0.4.1 ...`
  - `[BRC SA] endpoint set sa-network-extension-0.4.1`
  - `[BRC SA] posting page capture article TSLY sa-network-extension-0.4.1`
- New failure changed from a generic transport timeout to:
  - `[BRC SA] page capture failed signal is aborted without reason`
- Checked `trades.db` after the retest:
  - no new `sa_page_capture` events
  - no new `sa_debug_ping` rows for `0.4.1`
  - latest SA debug rows are still from `0.3.2`

**Conclusion:**
- Article extraction and request construction are working
- The failure is now isolated to the extension/background network path to the BoxRoomCapital endpoint
- Because even `sa_debug_ping` did not arrive, this is not a backend route bug inside `app/api/server.py`; the configured endpoint is either unreachable, sleeping, wrong, or stalling before the app handles the request

**Next steps:**
- Verify the exact extension endpoint by opening `<endpoint>/api/health` and `<endpoint>/api/preflight` in a normal browser tab
- Open the extension service worker console and capture only `[BRC SA BG] ...` lines for the failing article test

2026-03-07 11:46 — Control-plane hang root cause + mitigation
- Root cause: HTMX over-polling of hidden dashboard panels plus synchronous broker-backed fragments (especially /fragments/market-browser calling IG for every configured market) caused worker starvation, making even /api/health and /api/preflight hang.
- Mitigation shipped: added TTL/stale caching for status, broker snapshot, broker health, market browser, research, and ledger fragment payloads; added short UI-specific IG timeouts; removed reconcile_report state-file writes on read-only polling paths.
- Validation: py_compile passed; pytest -q tests/test_regression_ig_broker.py tests/test_ui_fragment_caching.py -> 38 passed; pytest -q tests/test_api_analytics.py passed; tests/test_api_execution_quality.py still hangs in this environment and was not used as a validation signal.

2026-03-07 11:47 — Stability follow-up: overview/trading hidden HTMX tab polling now aborted client-side; risk/intelligence/portfolio fragments cached; legacy SSE now exits on disconnect and sends keep-alives. Evidence: repeated CLOSE_WAIT/FIN_WAIT2 buildup on port 5000 and restart-only recovery pattern.

## 2026-03-07 14:00 — Codebase cleanup & light mode theme conversion (continued)

**Context:** Continuing cleanup backlog and fixing remaining test failures after dark→light theme conversion
**Work done:**
- Fixed 6 remaining theme-related test failures in `tests/test_phase_n_ui.py`:
  - `bg-slate-900` → `bg-white` in page token assertions (overview.html, trading.html)
  - `bg-slate-950` → `bg-gray-50` in DESIGN_TOKENS.md assertion
  - `bg-slate-900` → `bg-white` in DESIGN_TOKENS.md card assertion
  - `text-emerald-400` → `text-emerald-600` in profit color assertion
  - `text-red-500` → `text-red-600` in loss color assertion
- Full test suite: 2300 passed, 10 pre-existing e2e failures, 1 flaky (passes individually)
- Updated persistent memory: theme reference, test count
**Key files touched:** `tests/test_phase_n_ui.py`, `MEMORY.md`
**Current state:** Light mode theme conversion fully complete — all templates, DESIGN_TOKENS.md, app.js, and tests aligned
**Next steps:** Phase 2 structural refactors (server.py helper extraction, template macros, intel_pipeline decomposition)

2026-03-07 14:04 UTC — Instability incident marker: user reports the app still degrades until a full machine restart is required. Symptom pattern remains: brief period of normal behavior after restart, then severe hanging/blue-bar browser loads and service instability. User requested a dedicated log-based investigation after current codebase cleanup work completes.

## 2026-03-07 15:00 — Simplify review + structural refactors

**Context:** Ran /simplify with 3 review agents (reuse, quality, efficiency) on 100KB diff, then fixed findings
**Work done:**
- **Cache eviction**: Added expired-entry cleanup to `_FRAGMENT_CACHE` when size > 50 (server.py)
- **Control action helper**: Extracted `_execute_control_action()` — eliminated 9 duplicate create-job/try/except/update-job blocks
- **Jinja2 macros**: Created `_macros.html` with `badge()`, `section_header()`, `kv_row()`, `panel()` macros; adopted in 5 templates (_intelligence_feed, _incidents, _broker_health, _jobs, _status)
- **Shared `utc_now_iso()`**: Added to `utils/datetime_utils.py`, replaced 17 duplicate definitions across job files, server, earnings_client, signal_shadow
- **UTC timestamps**: Fixed `datetime.now()` → `datetime.now(timezone.utc)` in data/trade_db.py (4 instances)
- **Dead code**: Removed unused `COUNCIL_MODEL_TIMEOUT` variable in intel_pipeline.py
- **Top-level import**: Moved `concurrent.futures` from inside function body to module level in intel_pipeline.py
- **DRY webhooks**: Replaced inline JSON parsing in `sa_quant_capture_webhook` and `x_intel_webhook` with `_decode_json_request` helper
- **Strategy key**: Replaced remaining hardcoded `"ibs_credit_spreads"` with `config.DEFAULT_STRATEGY_KEY` in promotion_gate.py and options_recovery.py
**Key files touched:** server.py, intel_pipeline.py, trade_db.py, promotion_gate.py, options_recovery.py, datetime_utils.py, _macros.html, 5 templates, 16 job files
**Current state:** 2302 passed, 10 pre-existing e2e failures
**Remaining from review agents (lower priority):**
- Dedupe check runs after event write in `_queue_sa_intel_payload` (efficiency #5)
- `_find_existing_sa_intel_result` uses LIKE on unindexed column (efficiency #6)
- Remaining inline `_FakeEventStore` in tests (reuse #4)
- N+1 broker API calls in market browser (efficiency #2) — mitigated by cache TTL
- ThreadPoolExecutor gather pattern duplication in intel_pipeline rounds (reuse #3)

## 2026-03-07 16:00 — Architecture review and improvement plan

**Context:** User requested comprehensive Architect-level review and improvement plan
**Work done:**
- Surveyed full codebase: 91K lines Python, 300+ files, 112 test files
- Identified 6 priority areas (P0-P6)
- Created `ops/ARCHITECTURE_PLAN.md` with full diagnosis and execution plan
- Key finding: server.py (5K lines) is the #1 problem, data layer fragmentation is #2
- Confirmed legacy/ is dead code (not imported anywhere)
- Mapped 5 data stores with inconsistent connection management
**Key files touched:** `ops/ARCHITECTURE_PLAN.md`
**Current state:** Plan written, ready for user review and Codex input
**Next steps:** User to review plan, point Codex at it for independent assessment, then execute

## 2026-03-08 14:00 — Data Sources Report Synthesis

**Context:** User provided ChatGPT Pro deep research report on solo operator data sources (Carver, Alvarez, Davey, Darwinex/Wim) — their numeric vs textual data, market snapshots, time series needs, charting tools, homegrown vs commercial services. Asked to synthesize into current research system specs.
**Work done:**
- Saved report to `ops/RESEARCH_FOLLOWUP_DATA_SOURCES.md`
- Added Phase 0 (Market Data Infrastructure) to tech spec — ~250 lines of Pydantic models covering InstrumentMaster, RawBar, CanonicalBar, CorporateAction, UniverseMembership, FuturesContract, RollCalendar, MultiplePrices, ContinuousSeries, LiquidityCostEntry, Snapshot, VendorAdapter, plus time series checklist and buy-vs-build table
- Added 5-layer data architecture diagram + PostgreSQL DDL (instruments, raw_bars, canonical_bars, universe_membership, corporate_actions, futures_contracts, roll_calendar, snapshots) to architecture doc
- Added `research/market_data/` module (9 files) to architecture module structure
- Added Section 12 (Charting Layer) to UX spec — 5 chart types (symbol, trade replay, scanner, regime, futures strip), Lightweight Charts recommendation, 5 new chart endpoints, scanner view spec
- Added 5 chart template files to UX template list
- Updated build summary: ~40 files, ~335 tests, 8,500-11,000 lines (was ~30/285/6-8K)
- Added 2 new consensus principles to plan: "build numeric-data-first" + "bar definitions and vendor semantics matter"
- Updated plan build sequence to start with Phase 0 before Phase 1
- Added source document references across all docs
**Key files touched:** `ops/RESEARCH_FOLLOWUP_DATA_SOURCES.md` (new), `ops/RESEARCH_SYSTEM_ARCHITECTURE.md`, `ops/RESEARCH_SYSTEM_TECH_SPEC.md`, `ops/RESEARCH_SYSTEM_UX_SPEC.md`, `ops/RESEARCH_SYSTEM_PLAN_FINAL.md`, `.claude/history/SESSION_LOG.md`
**Current state:** All four research system spec docs now incorporate the data sources report. Build plan resequenced to Phase 0-7 (numeric data first). Charting requirements specified.
**Next steps:** User review of updated specs. Codex continuing architecture plan P0-P6 work.

## 2026-03-08 15:30 — Codex Review Assessment + Full Implementation Backlog

**Context:** Codex finished Architecture Plan v2 (P0-P6), then reviewed Claude's research system specs. Made 4 edits: fixed stale sequencing, softened PostgreSQL causality claim, relaxed challenge independence rule, made UX a phase-2 destination. User asked Claude to assess Codex's views, then create full implementation backlog.
**Work done:**
- Assessed all 4 Codex review points — agreed with all, noted caveat on challenge independence
- Answered Codex's 3 open questions (Engine B subsumes idea pipeline, minimum dataset = OHLCV + corp actions + SP500 list, evolve /research not new route)
- Codex wrote answers into docs
- Created comprehensive implementation backlog: `ops/RESEARCH_SYSTEM_BACKLOG.md`
  - 39 tasks across 13 tracks (Infrastructure, Phase 0-7, Engine A, Engine B, UX, Integration)
  - ~58 new files, ~380 tests, ~10,500 lines estimated
  - Full dependency graph with parallelization opportunities
  - Explicit Codex build instructions (12 rules)
  - Every task has: file, class/function specs, acceptance criteria, test count, spec reference
**Key files:** `ops/RESEARCH_SYSTEM_BACKLOG.md` (new), `.claude/history/SESSION_LOG.md`
**Current state:** Backlog ready for overnight Codex build session. All specs approved (with Codex's edits). Architecture Plan v2 complete.
**Next steps:** Point Codex at RESEARCH_SYSTEM_BACKLOG.md for overnight build.

## 2026-03-08 21:16 UTC — Autonomous overnight setup (pre-build)

**Context:** User requested setup for non-stop autonomous execution workflow and asked to await explicit go-instruction before starting backlog build.
**Work done:**
- Started setup pass to add permanent autonomous-mode instructions and detached background runner scripts.
- Confirmed no build execution should start yet.
**Key files touched:** `.claude/history/SESSION_LOG.md`
**Current state:** Setup in progress.
**Next steps:** Update `CLAUDE.md`, add detached job scripts, provide exact go/start command.

## 2026-03-08 21:18 UTC — Autonomous mode + detached runner setup complete

**Context:** User asked to apply the autonomous execution setup now and wait for explicit go-instruction before starting backlog implementation.
**Work done:**
- Added permanent autonomous execution block to `CLAUDE.md` under Learnings/session discipline.
- Added detached background job tooling:
  - `scripts/detached_job_start.sh`
  - `scripts/detached_job_status.sh`
  - `scripts/detached_job_stop.sh`
  - `scripts/detached_job_checkpoint.sh`
- Added usage/runbook doc: `ops/OVERNIGHT_RUNNER.md`.
- Made scripts executable and validated shell syntax with `bash -n`.
**Key files touched:** `CLAUDE.md`, `scripts/detached_job_start.sh`, `scripts/detached_job_status.sh`, `scripts/detached_job_stop.sh`, `scripts/detached_job_checkpoint.sh`, `ops/OVERNIGHT_RUNNER.md`, `.claude/history/SESSION_LOG.md`
**Current state:** Setup complete. No backlog build job started yet.
**Next steps:** Await user `go` instruction, then launch detached backlog job and checkpoint continuously.

## 2026-03-08 20:15 — Research System Backlog Audit & Fix
**Context:** Audit of backlog against 7 source spec documents found 5 structural dependency errors, ~15 missing tasks, stale thresholds, and 6 missing spec files on main.
**Work done:**
- Restored 7 spec files from git history (8f6a9cd / f74caad) to `ops/`
- Fixed circular dependency: moved P3-1 (taxonomy) before P2, added explicit dependency chain P3-1→P2-4
- Added 6 missing Pydantic body models to P1-2 (EngineASignalSet, SignalValue, RebalanceSheet, ExecutionReport, FillDetail, PostMortemNote)
- Added EA dependency on P4-1 (regime classifier) to EA-2 and EA-4
- Fixed scoring thresholds to 5-tier: <60 reject, 60-69 park/revise, 70-79 test, 80-89 experiment, 90+ live pilot
- Added INT-2 dependency on P2-7 + P2-8
- Fixed P4-1 sizing factor to enforce ge=0.5 in Pydantic model
- Fixed acceptance criteria for P2-1 (prompt hash audit), P2-7 (regime check in pipeline), P5-2 (correlation), P6-1 (data health)
- Added 8 new tasks: P0-9 (universe seeding), P2-1b (prompt hash registry), EA-1b (feature cache), EA-5 (Engine A control), EA-6 (Engine B control), SCHED-1 (scheduler), INT-4 (council cutover)
- Replaced UX-1..UX-5 with expanded UX-1..UX-11 covering all 6 tabs + charts + actions + artifact viewer + intel intake + top bar KPIs
- Updated summary table: 39→~55 tasks, ~58→~82 files, ~380→~460 tests, ~10,500→~13,600 lines
- Rewrote dependency graph with correct ordering and per-UX-task dependency mapping
- Expanded build instructions from 14 to 19 items with all new constraints
- Updated header to reflect P0-P7 and list existing built UX surfaces
- All 15 integrity checks passed verification
**Key files touched:** `ops/RESEARCH_SYSTEM_BACKLOG.md`, 6 restored spec files, `.claude/history/SESSION_LOG.md`
**Current state:** Backlog is audit-clean and ready for Codex overnight build session
**Next steps:** Launch Codex build session with updated backlog

## 2026-03-08 21:45 UTC — Research build tranche 1 complete (Phase 0 foundation)

**Context:** User gave autonomous `GO` instruction for the research-system backlog build.
**Work done:**
- Added PostgreSQL research config + dependency wiring:
  - `config.py` `_env_str(...)` helper and `RESEARCH_DB_DSN`
  - `.env.example` `RESEARCH_DB_DSN`
  - `requirements.txt` `psycopg2-binary`
- Installed `psycopg2-binary` into the project-local Python path so the research PostgreSQL layer can import/run in this runtime.
- Added `data/pg_connection.py` with:
  - thread-safe lazy `ThreadedConnectionPool`
  - `get_pg_connection()`, `release_pg_connection()`, `reset_pg_pool()`, `init_research_schema()`
  - idempotent DDL for research schema tables: instruments, raw_bars, canonical_bars, universe_membership, corporate_actions, futures_contracts, roll_calendar, liquidity_costs, snapshots, artifacts, model_calls, artifact_links, pipeline_state
- Created research package scaffold:
  - `research/`, `research/market_data/`, `research/engine_a/`, `research/engine_b/`, `research/shared/`, `research/prompts/`
- Implemented Phase 0 market-data modules:
  - `research/market_data/instruments.py`
  - `research/market_data/raw_bars.py`
  - `research/market_data/corporate_actions.py`
  - `research/market_data/universe.py`
  - `research/market_data/canonical_bars.py`
  - `research/market_data/futures.py`
  - `research/market_data/liquidity.py`
  - `research/market_data/snapshots.py`
  - `research/market_data/ingestion.py`
- Added shared row-mapping helper: `research/shared/sql.py`
- Added focused mock-driven unit tests for the new PostgreSQL + Phase 0 surface.

**Key files touched:** `config.py`, `.env.example`, `requirements.txt`, `data/pg_connection.py`, `research/market_data/*`, `research/shared/sql.py`, `tests/test_pg_connection.py`, `tests/test_instruments.py`, `tests/test_raw_bars.py`, `tests/test_corporate_actions.py`, `tests/test_universe.py`, `tests/test_canonical_bars.py`, `tests/test_futures_data.py`, `tests/test_liquidity.py`, `tests/test_snapshots.py`, `tests/test_ingestion.py`, `tests/research_test_utils.py`, `tests/__init__.py`

**Verification:**
- `python3 -m py_compile ...` on all new research modules: passed
- `pytest -q tests/test_pg_connection.py tests/test_instruments.py tests/test_raw_bars.py tests/test_corporate_actions.py tests/test_universe.py tests/test_canonical_bars.py tests/test_futures_data.py tests/test_liquidity.py tests/test_snapshots.py tests/test_ingestion.py` -> `36 passed`

**Current state:** Research system now has a real package scaffold, PostgreSQL schema bootstrap, and a tested Phase 0 numeric-first market-data foundation.
**Next steps:** Build Phase 1 artifact models/store and extend promotion gate for the new artifact-driven decision flow.

## 2026-03-08 22:05 UTC — Research build tranche 2 complete (artifacts + routing + early Engine B pipeline)

**Context:** Continued autonomous backlog execution after Phase 0 foundation passed.
**Work done:**
- Added typed artifact spine:
  - `research/artifacts.py` — enums, `ArtifactEnvelope`, body schemas for EventCard, HypothesisCard, FalsificationMemo, TestSpec, ExperimentReport, TradeSheet, RetirementMemo, RegimeSnapshot, RegimeJournal, EngineASignalSet, RebalanceSheet, ExecutionReport, PostMortemNote, ReviewTrigger, ScoringResult
  - `validate_artifact_body(...)` and artifact-type → schema mapping
- Added immutable PostgreSQL artifact store:
  - `research/artifact_store.py`
  - supports `save`, `get`, `get_chain`, `get_latest`, `query`, `get_linked`, `count`
  - version chains + parent supersede behavior
  - link extraction from `*_ref` / `*_refs` fields into `research.artifact_links`
- Extended promotion gate for artifact-aware outcomes:
  - `fund/promotion_gate.py` now includes `outcome`, `artifact_refs`, `blocking_objections`
  - backward-compat `allowed` behavior preserved
  - added `evaluate_with_artifacts(...)`
- Added taxonomy enforcement:
  - `research/taxonomy.py`
- Added research model-routing layer:
  - `research/model_router.py`
  - normalized `ModelConfig`, `ModelResponse`
  - provider routing, fallback handling, prompt hashing, `research.model_calls` logging
  - default `RESEARCH_MODEL_CONFIG` added to `config.py`
- Added initial prompt templates:
  - `research/prompts/v1_signal_extraction.py`
  - `research/prompts/v1_hypothesis.py`
  - `research/prompts/v1_challenge.py`
- Added early Engine B artifact pipeline services:
  - `research/engine_b/signal_extraction.py`
  - `research/engine_b/hypothesis.py`
  - `research/engine_b/challenge.py`
- Fixed `SignalExtractionService` default timestamp behavior to use a UTC ISO timestamp when the model does not provide one.

**Key files touched:** `research/artifacts.py`, `research/artifact_store.py`, `fund/promotion_gate.py`, `research/taxonomy.py`, `research/model_router.py`, `research/prompts/v1_signal_extraction.py`, `research/prompts/v1_hypothesis.py`, `research/prompts/v1_challenge.py`, `research/engine_b/signal_extraction.py`, `research/engine_b/hypothesis.py`, `research/engine_b/challenge.py`, `config.py`, `.claude/history/SESSION_LOG.md`

**Verification:**
- `pytest -q tests/test_artifacts.py tests/test_artifact_store.py tests/test_promotion_gate_v2.py tests/test_promotion_enforcement.py` -> `32 passed`
- `pytest -q tests/test_taxonomy.py tests/test_model_router.py tests/test_artifacts.py tests/test_artifact_store.py tests/test_promotion_gate_v2.py tests/test_pg_connection.py tests/test_instruments.py tests/test_raw_bars.py tests/test_corporate_actions.py tests/test_universe.py tests/test_canonical_bars.py tests/test_futures_data.py tests/test_liquidity.py tests/test_snapshots.py tests/test_ingestion.py tests/test_promotion_enforcement.py` -> `77 passed`
- `pytest -q tests/test_signal_extraction.py tests/test_hypothesis.py tests/test_challenge.py` -> `4 passed`
- Combined focused research slice: `81 passed`

**Current state:** Research system now has a tested numeric market-data foundation, typed artifact system, immutable artifact store, artifact-aware promotion gating, taxonomy enforcement, model routing, and the first three Engine B pipeline services.
**Next steps:** Continue with prompt hash registry, hypothesis/challenge scoring pipeline, and then wire the new artifact flow into existing intel/research surfaces.

## 2026-03-08 23:35 UTC — Research build tranche 3 complete (pipeline hardening + intake + regime classifier)

**Work done:**
- Hardened Engine B lineage and pipeline behavior:
  - propagated `chain_id` through hypothesis, challenge, and scoring artifacts
  - updated `research/engine_b/pipeline.py` to keep a stable chain across stages
  - pipeline now halts cleanly on taxonomy rejection and records `taxonomy_rejected` in `research.pipeline_state`
  - challenge service now enforces self-challenge independence when the router supports it, while remaining compatible with lightweight test doubles
- Upgraded research model routing:
  - `research/model_router.py` now implements retry + exponential backoff before fallback
  - preserved cost/prompt-hash logging into `research.model_calls`
- Added Engine B intake layer:
  - `research/engine_b/source_scoring.py` — deterministic tiered source credibility scoring with corroboration bonus
  - `research/engine_b/intake.py` — raw-content normalization, dedup by recent `raw_content_hash`, source scoring, instrument hint extraction
- Added Engine A deterministic regime classifier:
  - `research/engine_a/regime.py`
  - classifies `vol_regime`, `trend_regime`, `carry_regime`, `macro_regime`, derives `sizing_factor`, and sets risk overrides

**Tests added/expanded:**
- `tests/test_model_router.py`
  - retry/backoff behavior
  - fallback expectations updated for retried failures
- `tests/test_engine_b_pipeline.py`
  - taxonomy rejection halt path
- `tests/test_intake.py`
  - source-tier scoring
  - corroboration bonus
  - dedup detection
  - normalization + instrument hint extraction
- `tests/test_regime_classifier.py`
  - vol/trend/carry regime classification
  - macro state classification
  - sizing-factor floors and stress penalties

**Verification:**
- Focused research/runtime slice: `pytest -q tests/test_prompt_registry.py tests/test_scorer.py tests/test_engine_b_pipeline.py tests/test_model_router.py tests/test_challenge.py tests/test_signal_extraction.py tests/test_hypothesis.py tests/test_intake.py tests/test_regime_classifier.py` -> passed
- Expanded research slice: `pytest -q tests/test_pg_connection.py tests/test_instruments.py tests/test_raw_bars.py tests/test_corporate_actions.py tests/test_universe.py tests/test_canonical_bars.py tests/test_futures_data.py tests/test_liquidity.py tests/test_snapshots.py tests/test_ingestion.py tests/test_artifacts.py tests/test_artifact_store.py tests/test_promotion_gate_v2.py tests/test_taxonomy.py tests/test_model_router.py tests/test_signal_extraction.py tests/test_hypothesis.py tests/test_challenge.py tests/test_scorer.py tests/test_engine_b_pipeline.py tests/test_prompt_registry.py tests/test_intake.py tests/test_regime_classifier.py tests/test_promotion_enforcement.py` -> `104 passed, 3 warnings`

**Current state:**
- Engine B now has a sturdier artifact chain, retry-capable model routing, a deterministic intake layer, and pipeline-state handling for taxonomy rejects.
- Engine A now has the first shared deterministic regime context service ready for signal conditioning.

**Next steps:**
- Continue with `research/shared/regime_journal.py` + prompt template, then wire the prompt registry into the live model-router call path, and after that expand Engine B intake/pipeline wiring into existing intel surfaces.

## 2026-03-09 00:10 UTC — Research build tranche 4 complete (regime journal + cost model + experiments + kill monitor)

**Work done:**
- Added prompt-registry enforcement to the live model-router path:
  - `research/model_router.py` now bootstraps prompt hashes and blocks calls on `PROMPT_DRIFT`
  - added `regime_journal` service config to `RESEARCH_MODEL_CONFIG`
  - extended `research/prompt_registry.py` with `regime_journal` prompt hashing
- Added regime-journal support:
  - `research/prompts/v1_regime_journal.py`
  - `research/shared/regime_journal.py`
  - extended `RegimeJournal` artifact schema with `regime_snapshot_ref` for artifact linkage
- Added deterministic cost modeling:
  - `research/shared/cost_model.py`
  - supports IG and IBKR equity/futures templates, round-trip cost estimation, and backtest netting
- Added cost-aware Engine B experiment service:
  - `research/engine_b/experiment.py`
  - immutable `TestSpec` registration
  - cost-applied experiment execution
  - gross + net metrics, robustness checks, capacity estimate, and correlation callback support
- Added lifecycle retirement enforcement:
  - `research/shared/kill_monitor.py`
  - kill criteria registration, trigger evaluation, retirement memo generation, optional pipeline-state update + notify callbacks

**Tests added/expanded:**
- `tests/test_model_router.py` — prompt-drift blocking path
- `tests/test_regime_journal.py`
- `tests/test_cost_model.py`
- `tests/test_experiment.py`
- `tests/test_kill_monitor.py`

**Verification:**
- Expanded research slice including new shared services: `123 passed, 3 warnings`

**Current state:**
- Shared research platform now covers artifact lineage, prompt drift enforcement, intake, scoring, deterministic regime context, regime journaling, cost modeling, experiment execution, and retirement memo generation.
- Remaining major backlog areas are decay-triggered review integration, Engine A signal/portfolio/rebalancer pipeline, and app-surface wiring (BotControl/API/UI/intel migration).

**Next steps:**
- Build `research/shared/decay_review.py` and promotion-gate blocking on active review triggers.
- Then move into Engine A signal generators / portfolio construction.

## 2026-03-09 00:45 UTC — Research build tranche 5 complete (decay review + Engine A primitives)

**Work done:**
- Added decay-triggered review service:
  - `research/shared/decay_review.py`
  - creates `ReviewTrigger` artifacts from `analytics.decay_detector`
  - supports operator acknowledgement with 4-state `PromotionOutcome`
- Extended `ReviewTrigger` artifact schema in `research/artifacts.py` with acknowledgement fields
- Integrated pending decay reviews into promotion enforcement:
  - `fund/promotion_gate.py` now blocks with `DECAY_REVIEW_PENDING` before artifact-chain promotion if a strategy has an active unacknowledged review trigger
- Added Engine A deterministic signal generators:
  - `research/engine_a/signals.py`
  - `TrendSignal`, `CarrySignal`, `ValueSignal`, `MomentumSignal`
- Added Engine A portfolio construction:
  - `research/engine_a/portfolio.py`
  - vol-adjusted/risk-parity-style construction with leverage cap and regime sizing
- Added Engine A rebalance generation:
  - `research/engine_a/rebalancer.py`
  - delta computation, small-trade suppression, cost-aware approval blocking

**Tests added:**
- `tests/test_decay_review.py`
- `tests/test_engine_a_signals.py`
- `tests/test_engine_a_portfolio.py`
- `tests/test_engine_a_rebalancer.py`

**Verification:**
- Expanded research slice: `141 passed, 3 warnings`

**Current state:**
- Research system now includes decay-review enforcement and the first usable Engine A deterministic components (signals, portfolio targets, rebalance sheets).
- The next meaningful build target is `research/engine_a/pipeline.py` to connect regime → signals → portfolio → rebalance into a daily run artifact chain.

## 2026-03-09 01:20 UTC — Research build tranche 6 complete (Engine A orchestration + feature cache + control hooks)

**Work done:**
- Added full Engine A daily orchestration:
  - `research/engine_a/pipeline.py`
  - daily artifact chain: `RegimeSnapshot` -> `EngineASignalSet` -> `RebalanceSheet` -> optional `TradeSheet` -> optional `ExecutionReport`
  - promotion-gate aware execution path
- Added Engine A feature cache:
  - `research/engine_a/feature_cache.py`
  - new schema table `research.feature_cache` in `data/pg_connection.py`
  - integrated cache reads/writes into `EngineAPipeline` signal generation
- Extended control plane for Engine A:
  - `app/engine/control.py`
  - `start_engine_a()`, `stop_engine_a()`, `engine_a_status()`
  - included Engine A in `pipeline_status()` and supervisor restart logic
- Added config/env settings:
  - `ENGINE_A_ENABLED`
  - `ENGINE_A_INTERVAL_SECONDS`
  - documented in `.env.example`

**Tests added/expanded:**
- `tests/test_engine_a_pipeline.py`
- `tests/test_feature_cache.py`
- `tests/test_engine_a_control.py`
- expanded `tests/test_engine_a_pipeline.py` to verify cache-hit reuse

**Verification:**
- Expanded slice: `152 passed, 3 warnings`

**Current state:**
- Engine A now has deterministic signals, portfolio construction, rebalance generation, daily orchestration, cache support, and control-plane lifecycle hooks.
- Remaining work is primarily app/API/UI exposure and the residual research/ops surfaces around these new services.

## 2026-03-09 02:25 UTC — Research build tranche 7 complete (dashboard fragments + scheduler research hooks)

**Work done:**
- Added artifact-backed research dashboard query layer:
  - `research/dashboard.py`
  - pipeline funnel, active hypotheses, recent decisions, pending review alerts, retirement alerts
- Extended the existing `/research` surface with nested HTMX fragments:
  - `/fragments/research/pipeline-funnel`
  - `/fragments/research/active-hypotheses`
  - `/fragments/research/engine-status`
  - `/fragments/research/recent-decisions`
  - `/fragments/research/alerts`
- Added operator acknowledgement action for decay reviews:
  - `POST /api/actions/research/review-ack`
  - invalidates fragment caches after acknowledgement
- Added new templates:
  - `_research_pipeline_funnel.html`
  - `_research_active_hypotheses.html`
  - `_research_engine_status.html`
  - `_research_recent_decisions.html`
  - `_research_alerts.html`
- Extended scheduler core to support named window handlers:
  - `app/engine/scheduler.py`
  - `window_handlers` constructor arg
  - `set_window_handler()`
  - generic summary extraction for non-orchestration scheduled jobs
- Wired research scheduler hooks into control plane:
  - `app/engine/control.py`
  - configurable decay-review and kill-check factories
  - scheduler now registers research windows when factories are present
  - added status reporting for `decay_review` and `kill_check`
- Wired default app factories in server bootstrap:
  - `DecayReviewService(ArtifactStore())`
  - `KillMonitor(ArtifactStore())`

**Tests added/expanded:**
- `tests/test_research_dashboard.py`
- `tests/test_research_api_surface.py`
- `tests/test_scheduler_research.py`
- expanded `tests/test_engine_a_control.py` for decay review, kill check, and scheduler registration

**Verification:**
- `pytest tests/test_engine_a_control.py tests/test_research_dashboard.py tests/test_research_api_surface.py tests/test_engine_a_api_surface.py tests/test_scheduler_research.py tests/test_decay_review.py tests/test_phase_n_ui.py -q`
- Result: `116 passed in 1.70s`

**Current state:**
- The new research system is now visible in the app via artifact-aware dashboard fragments.
- Pending decay reviews can be acknowledged from the UI.
- The scheduler can run dedicated research windows rather than only the orchestration callback.
- Default runtime now wires decay-review and kill-check services into scheduler startup.

**Next likely build targets:**
- Engine B control-plane registration / manual trigger surface
- Default Engine A runtime data provider so `engine_a_factory` is live in the real app, not only in tests
- Further `/research` tab expansion (regime panel, Engine A tab, chain drilldown)

## 2026-03-09 02:45 UTC — Research build tranche 8 complete (manual Engine B runtime entry point)

**Work done:**
- Added live Engine B runtime factory:
  - `research/runtime.py`
  - `build_engine_b_pipeline()` wires `ArtifactStore`, `ModelRouter`, signal extraction, hypothesis, challenge, scoring, and latest Engine A regime context
- Added manual Engine B control-plane action:
  - `POST /api/actions/research/engine-b-run`
  - accepts raw content, source class, credibility, source IDs
  - runs Engine B in a background job and persists job result summary
  - invalidates research dashboard fragment caches on completion
- Extended `/research` page with operator intake form:
  - `Manual Engine B Intake`
  - lets operator paste raw text and submit directly into the new pipeline
- Kept runtime lazy:
  - no Engine B model/router bootstrap at app import time
  - pipeline is constructed only when a manual run is triggered

**Tests added/expanded:**
- `tests/test_research_runtime.py`
- expanded `tests/test_research_api_surface.py` for Engine B action path + UI form checks

**Verification:**
- `pytest tests/test_research_runtime.py tests/test_research_api_surface.py tests/test_research_dashboard.py tests/test_engine_a_control.py tests/test_scheduler_research.py tests/test_engine_a_api_surface.py tests/test_decay_review.py tests/test_phase_n_ui.py -q`
- Result: `119 passed in 1.75s`

**Current state:**
- `/research` now has a direct operator path into Engine B.
- The resulting chains feed the new research dashboard fragments and job log.
- Scheduler, decay review, kill review, and manual Engine B intake are now all exposed in the running app.

**Next likely build targets:**
- Default Engine A runtime data provider so Engine A can be started from the real app without test-only factories
- Engine B control/status registration inside `BotControlService`
- Deeper `/research` drilldowns (regime panel, chain detail, recent artifacts table)

## 2026-03-09 03:05 UTC — Research build tranche 9 complete (default Engine A runtime provider)

**Work done:**
- Added DB-backed Engine A runtime data provider:
  - `research/engine_a/runtime_data.py`
  - reads futures roots/contracts from research PostgreSQL layer
  - assembles `price_history`, `term_structure`, `value_history`, `current_value`, `vol_estimates`, `correlations`, `contract_sizes`, and heuristic `regime_inputs`
- Added runtime Engine A factory:
  - `research/runtime.py::build_engine_a_pipeline()`
  - wires `ArtifactStore`, `FeatureCache`, and `EngineARuntimeDataProvider`
- Wired real app control plane to Engine A factory:
  - `app/api/server.py` now configures `engine_a_factory=lambda: build_engine_a_pipeline()`
  - Engine A is now genuinely configured in the live app, subject to research DB data availability
- Added config/env for runtime capital sizing:
  - `ENGINE_A_CAPITAL_BASE`
  - documented in `.env.example`

**Tests added/expanded:**
- `tests/test_engine_a_runtime_data.py`
- expanded `tests/test_research_runtime.py` to cover Engine A factory wiring

**Verification:**
- `pytest tests/test_engine_a_runtime_data.py tests/test_research_runtime.py tests/test_research_api_surface.py tests/test_research_dashboard.py tests/test_engine_a_control.py tests/test_scheduler_research.py tests/test_engine_a_api_surface.py tests/test_decay_review.py tests/test_phase_n_ui.py -q`
- Result: `122 passed in 1.88s`

**Current state:**
- Engine A is no longer test-only from the app’s point of view.
- If the research PostgreSQL layer has futures contracts + canonical bars, the control plane can now build and run Engine A using real stored market data.
- Remaining gaps are more around richer research UX and optional Engine B lifecycle registration than core runtime wiring.

**Next likely build targets:**
- Research chain drilldown / recent artifact table in `/research`
- Engine B lifecycle/status registration in `BotControlService`
- More explicit operator surfaces for Engine A inputs/health diagnostics

## 2026-03-09 07:50 UTC — Research backlog resume checkpoint

**Context:** User asked how far the ops-driven research system backlog had progressed and requested that build work continue from the next incomplete tranche.

**Work done:**
- Reviewed `ops/RESEARCH_SYSTEM_BACKLOG.md` against the logged build tranches in this file
- Confirmed implemented coverage through research build tranche 9:
  - infrastructure + market-data substrate
  - artifact store + promotion gate v2
  - Engine B extraction/hypothesis/challenge/scoring/pipeline
  - regime, cost, experiment, kill, and decay-review services
  - Engine A signals/portfolio/rebalancer/pipeline/runtime provider
  - research dashboard fragments, manual Engine B intake, and scheduler research hooks
- Identified the clearest next backlog gap as Engine B lifecycle/status registration inside `BotControlService`, followed by richer `/research` drilldowns and Engine A operator diagnostics

**Current state:** Research backlog is materially advanced beyond the initial spec order. Core services are built; remaining work is mostly control-plane completion, deeper research UX, and end-to-end integration/cutover tasks.

**Next steps:** Inspect current control-plane wiring, implement Engine B managed-service registration and status reporting, then extend the next operator-facing research surface that depends on it.

## 2026-03-09 08:01 UTC — Research build tranche 10 complete (Engine B control-plane registration)

**Work done:**
- Added Engine B lifecycle management to `BotControlService`:
  - `start_engine_b()`, `stop_engine_b()`, `engine_b_status()`
  - queue-backed `submit_engine_b_event()` for managed or ad hoc background execution
  - `pipeline_status()` now reports Engine B state
  - supervisor restart logic now covers Engine B when enabled
- Extended research service wiring:
  - `app/api/server.py` now configures `engine_b_factory=lambda: build_engine_b_pipeline()`
- Added operator control/API exposure:
  - `POST /api/actions/engine-b-start`
  - `POST /api/actions/engine-b-stop`
  - manual Engine B intake action now routes through the control plane instead of spawning an inline pipeline bootstrap directly
- Extended ops/research UI status surfaces:
  - `app/web/templates/_pipeline_status.html` now shows Engine B controls, queue depth, and last-result state
  - research engine-status context now includes an Engine B status card
- Added config/env support:
  - `ENGINE_B_ENABLED` in `config.py`
  - documented in `.env.example`

**Tests added/expanded:**
- `tests/test_engine_b_control.py` (NEW)
- expanded `tests/test_api_control.py` for Engine B start/stop routes
- expanded `tests/test_engine_a_api_surface.py` to assert Engine B route + template presence

**Verification:**
- `pytest -q tests/test_engine_b_control.py tests/test_engine_a_control.py tests/test_engine_a_api_surface.py`
- Result: `11 passed in 1.14s`
- `python -m py_compile app/engine/control.py app/api/server.py tests/test_engine_b_control.py tests/test_api_control.py tests/test_engine_a_api_surface.py`
- Note: broader `TestClient(server.app)` route suites remain prone to hanging in this runtime, consistent with the earlier recorded test-client limitation

**Current state:**
- Engine B is now a first-class managed research service rather than only a one-off manual background task path.
- The control plane and pipeline status surfaces can report and operate both research engines.
- Remaining notable backlog gaps are research chain drilldown / artifact viewer, deeper Engine A diagnostics, Engine B extras (expression/synthesis/post-mortem), and the larger integration/cutover tasks.

**Next steps:**
- Build research chain drilldown / recent artifact view in `/research`
- Expand Engine A operator diagnostics (regime, targets, rebalance health)
- Wire Engine B into broader intel/event intake flow and eventual council cutover

## 2026-03-09 08:08 UTC — Research backlog continuation checkpoint

**Context:** User asked to continue the research-system build immediately after Engine B control-plane registration.

**Work done:**
- Selected the next bounded backlog target: research chain drilldown / artifact viewer (`UX-8`)
- Preparing to wire artifact-chain endpoints and an inline `/research` viewer so operators can inspect full research lineage from summary tables

**Current state:** Control-plane work is complete through Engine B lifecycle registration. The next missing operator capability is drilling from dashboard summary rows into the underlying artifact chain.

**Next steps:** Inspect the current research page/templates, add chain/detail endpoints, render the chain viewer template, and wire one or more `/research` tables to load the viewer inline.

## 2026-03-09 08:15 UTC — Research build tranche 11 complete (artifact chain viewer)

**Work done:**
- Added research artifact serialization + chain/detail helpers in `app/api/server.py`
- Added new research inspection endpoints:
  - `GET /api/research/artifact-chain/{chain_id}`
  - `GET /api/research/artifact/{artifact_id}`
  - `GET /fragments/research/artifact-chain`
  - `GET /fragments/research/artifact-chain/{chain_id}`
- Added artifact-chain viewer template:
  - `app/web/templates/_research_artifact_chain.html`
  - renders metadata, compact summary rows, and expandable raw JSON bodies for each artifact in the chain
- Wired persistent viewer placement into the main research page:
  - `app/web/templates/research_page.html`
  - viewer lives outside the auto-refreshing `#research-panel` so selections are not blown away every 15 seconds
- Added inline drilldown triggers from existing research fragments:
  - `app/web/templates/_research_active_hypotheses.html`
  - `app/web/templates/_research_recent_decisions.html`
  - both now load the selected chain into `#research-artifact-chain-viewer`

**Tests added/expanded:**
- `tests/test_research_artifact_viewer.py` (NEW)
- expanded `tests/test_research_api_surface.py` for route registration and template wiring

**Verification:**
- `pytest -q tests/test_research_artifact_viewer.py tests/test_research_api_surface.py tests/test_engine_b_control.py tests/test_engine_a_control.py tests/test_engine_a_api_surface.py`
- Result: `17 passed in 1.25s`
- `python -m py_compile app/api/server.py tests/test_research_artifact_viewer.py tests/test_research_api_surface.py tests/test_engine_b_control.py tests/test_engine_a_control.py tests/test_engine_a_api_surface.py`

**Current state:**
- `/research` now has an actual artifact-chain drilldown instead of summary-only cards.
- Operators can inspect full Engine B lineage from active hypotheses or recent decisions without leaving the page.
- Remaining higher-value backlog gaps are richer Engine A diagnostics (`UX-2`), Engine B extras (`EB-1` to `EB-3`), and broader intel/integration cutover tasks.

**Next steps:**
- Build Engine A diagnostics surface: regime panel, signal heatmap, portfolio targets, rebalance panel, regime journal
- Then wire more of the existing intel/event flow into Engine B and move toward council cutover

## 2026-03-09 08:20 UTC — Research build tranche 12 complete (Engine A diagnostics surface)

**Work done:**
- Added Engine A artifact-backed diagnostics helpers in `app/api/server.py`:
  - latest regime snapshot
  - signal heatmap aggregation from `ENGINE_A_SIGNAL_SET`
  - portfolio targets from latest `REBALANCE_SHEET`
  - rebalance summary panel with top moves
  - recent regime-journal entries
- Added new `/research` fragment endpoints:
  - `GET /fragments/research/regime-panel`
  - `GET /fragments/research/signal-heatmap`
  - `GET /fragments/research/portfolio-targets`
  - `GET /fragments/research/rebalance-panel`
  - `GET /fragments/research/regime-journal`
- Added new templates:
  - `app/web/templates/_research_regime_panel.html`
  - `app/web/templates/_research_signal_heatmap.html`
  - `app/web/templates/_research_portfolio_targets.html`
  - `app/web/templates/_research_rebalance_panel.html`
  - `app/web/templates/_research_regime_journal.html`
- Extended `app/web/templates/_research.html` to load the new Engine A diagnostics fragments on staggered HTMX intervals
- Linked the rebalance panel back into the new research chain viewer so operator diagnostics can jump straight into artifact lineage

**Tests added/expanded:**
- `tests/test_engine_a_dashboard_helpers.py` (NEW)
- expanded `tests/test_research_api_surface.py` for new fragment routes
- expanded `tests/test_engine_a_api_surface.py` for template wiring assertions

**Verification:**
- `pytest -q tests/test_engine_a_dashboard_helpers.py tests/test_research_artifact_viewer.py tests/test_research_api_surface.py tests/test_engine_b_control.py tests/test_engine_a_control.py tests/test_engine_a_api_surface.py`
- Result: `21 passed in 1.34s`
- `python -m py_compile app/api/server.py tests/test_engine_a_dashboard_helpers.py tests/test_research_artifact_viewer.py tests/test_research_api_surface.py tests/test_engine_b_control.py tests/test_engine_a_control.py tests/test_engine_a_api_surface.py`

**Current state:**
- `/research` now exposes both Engine B review/drilldown and the first real Engine A diagnostics surface.
- The remaining major backlog items are Engine B extras (`EB-1` to `EB-3`), research operator actions beyond review-ack, and the integration/cutover path that routes the broader intel/event flow into Engine B.

**Next steps:**
- Build Engine B extras next: expression service, synthesis summary, and post-mortem generation
- Then wire existing intel/event intake into Engine B and add the research-system activation/cutover path

## 2026-03-09 08:25 UTC — Research build tranche 13 complete (Engine B extras)

**Work done:**
- Added deterministic Engine B expression service:
  - `research/engine_b/expression.py`
  - converts `HypothesisCard` + `ExperimentReport` + regime context into a `TradeSheet`
  - includes broker/instrument heuristic selection, regime-scaled sizing, entry/exit rules, and kill criteria
- Added operator synthesis service:
  - `research/shared/synthesis.py`
  - `research/prompts/v1_synthesis.py`
  - generates a concise operator summary for a full research chain while keeping unresolved objections explicit
- Added post-mortem generation service:
  - `research/shared/post_mortem.py`
  - `research/prompts/v1_post_mortem.py`
  - produces `PostMortemNote` artifacts from a completed hypothesis chain
- Extended prompt registry coverage:
  - `research/prompt_registry.py` now registers/checks `research_synthesis` and `post_mortem` prompt hashes
- Extended model config:
  - `config.py` now includes `research_synthesis` service config

**Tests added/expanded:**
- `tests/test_expression.py` (NEW)
- `tests/test_synthesis.py` (NEW)
- `tests/test_post_mortem.py` (NEW)

**Verification:**
- `pytest -q tests/test_expression.py tests/test_synthesis.py tests/test_post_mortem.py tests/test_model_router.py tests/test_prompt_registry.py`
- Result: `14 passed in 3.35s`
- `python -m py_compile research/engine_b/expression.py research/shared/synthesis.py research/shared/post_mortem.py research/prompts/v1_synthesis.py research/prompts/v1_post_mortem.py research/prompt_registry.py config.py tests/test_expression.py tests/test_synthesis.py tests/test_post_mortem.py`

**Current state:**
- The research system now has the main Engine B post-score services needed to move from idea -> challenge -> experiment -> expression -> synthesis/post-mortem.
- The remaining big gaps are integration and cutover work: routing more real intake into Engine B, research-system activation flags, richer operator actions, and broader E2E coverage.

**Next steps:**
- Wire existing intel/event sources into Engine B (`INT-2`)
- Add research-system activation/cutover path (`INT-4`)
- Expand operator actions and archive/post-mortem surfaces where the new services can be used

## 2026-03-09 09:51 UTC — Research integration resume checkpoint

**Context:** User asked to continue immediately after Engine B extras landed.

**Work done:**
- Selected the next high-value backlog target: `INT-2` existing intel/event flow -> Engine B integration
- Preparing to trace webhook/manual idea entry points and route them through the managed Engine B control path

**Current state:** Research engines, diagnostics, artifact viewer, and post-score services are in place. The biggest remaining gap is that most real intake paths still do not feed the research pipeline automatically.

**Next steps:** Inspect current intel webhooks and idea submission handlers, wire Engine B enqueue calls into those paths, add gating for safe rollout, then verify with focused route/config tests.

## 2026-03-09 10:03 UTC — Research integration implementation checkpoint

**Context:** Continuing the research backlog with `INT-2` webhook/manual intake wiring and `INT-4` cutover support.

**Work done:**
- Confirmed backlog contract from `ops/RESEARCH_SYSTEM_BACKLOG.md`
- Traced current legacy intel entry points in `app/api/server.py`
- Chose rollout shape: dual-write Engine B mirror while council remains primary, then flag-gated cutover to Engine B-only routing
- Identified missing migration utility and `/intel` banner/context as part of the cutover tranche

**Current state:** No code edits in this tranche yet. Existing webhooks still route primarily to the council pipeline; manual Engine B exists separately.

**Next steps:** Patch server/config for dual-write + cutover behavior, add migration utility, then verify with focused route tests.

## 2026-03-09 10:27 UTC — Research build tranche 14 complete (INT-2 intake wiring + INT-4 cutover support)

**Context:** Continued the research backlog after Engine B extras to wire real intake into Engine B and add a safe council cutover path.

**Work done:**
- Added shared Engine B enqueue helpers in `app/api/server.py` so webhook/manual intake paths use one job + cache invalidation flow
- Wired existing intake paths into Engine B:
  - `/api/intel/submit` now mirrors into Engine B while council remains primary, and routes Engine B-only when cutover is active
  - `/api/webhooks/x_intel` now mirrors into Engine B while council remains primary, and routes Engine B-only when cutover is active
  - `/api/webhooks/telegram` now mirrors into Engine B while council remains primary, and routes Engine B-only when cutover is active
  - `/api/webhooks/sa_intel` and `/api/webhooks/sa_page_capture` now mirror into Engine B while council remains primary, and route Engine B-only when cutover is active
  - `/api/webhooks/sa_quant_capture` now triggers Engine B after existing capture storage/signal persistence
  - added `/api/webhooks/finnhub` for structured Finnhub transcript/news/filing intake into Engine B
- Added `RESEARCH_SYSTEM_ACTIVE = _env_bool("RESEARCH_SYSTEM_ACTIVE", False)` to `config.py` and documented it in `.env.example`
- Added `/intel` cutover banner/context so operators can see when new intake bypasses the council path
- Added one-time idempotent migration utility:
  - `research/migration/council_cutover.py`
  - exports existing `trade_ideas` into a stable cutover manifest under `.runtime/research_migration/` by default

**Key files touched:**
- `app/api/server.py`
- `app/web/templates/intel_council_page.html`
- `config.py`
- `.env.example`
- `research/migration/council_cutover.py`
- `tests/test_engine_b_integration.py`
- `tests/test_council_cutover.py`

**Verification:**
- `python -m py_compile app/api/server.py config.py research/migration/council_cutover.py tests/test_engine_b_integration.py tests/test_council_cutover.py`
- `pytest -q tests/test_engine_b_integration.py tests/test_council_cutover.py tests/test_research_api_surface.py tests/test_sa_browser_capture.py -k 'sa_quant_capture_webhook_stores_capture_and_signal or sa_page_capture_webhook_queues_article_intel or sa_page_capture_webhook_accepts_expanded_market_news_story or test_research_dashboard_routes_are_registered or test_research_page_mentions_manual_engine_b_form or test_research_template_loads_dashboard_fragments or test_research_fragment_templates_expose_chain_viewer_controls or test_x_intel_webhook_dual_writes_engine_b_when_cutover_inactive or test_x_intel_webhook_routes_only_engine_b_when_cutover_active or test_sa_quant_capture_webhook_triggers_engine_b or test_intel_submit_mirrors_engine_b_when_cutover_inactive or test_finnhub_webhook_enqueues_engine_b or test_intel_page_context_exposes_research_system_banner or test_council_cutover_migration_is_idempotent'`
- Result: `14 passed, 17 deselected in 1.70s`
- `pytest -q tests/test_sa_browser_capture.py -k 'test_sa_page_capture_webhook_queues_article_intel or test_sa_page_capture_webhook_accepts_expanded_market_news_story or test_sa_page_capture_webhook_skips_duplicate_canonical_url or test_sa_page_capture_webhook_ignores_market_news_hub or test_sa_quant_capture_webhook_stores_capture_and_signal'`
- Result: `5 passed, 15 deselected in 1.59s`
- Compatibility attempt: `pytest -q tests/test_intel_pipeline.py` began running but stalled in the known `TestClient(server.app)` path in this runtime, so it was not counted as verified

**Current state:**
- Existing intel intake now feeds Engine B in production code without removing the legacy council path.
- Setting `RESEARCH_SYSTEM_ACTIVE=true` flips the main intel intake routes over to Engine B-first routing while leaving historical council review UI available.
- The cutover migration utility exists and is idempotent.

**Next steps:**
- Expose operator surfaces for synthesis/post-mortem generation and archive browsing
- Feed Engine B outputs into L9 research scoring / composite scorer integration
- Add broader E2E coverage once the full app-client test path is stable in this runtime

## 2026-03-09 10:38 UTC — Research signal-layer integration checkpoint

**Context:** Continuing from intake/cutover into `INT-1` so Engine B findings feed the composite scorer.

**Work done:**
- Confirmed `INT-1` scope from backlog: add `L9_RESEARCH`
- Traced layer registry, composite contracts, event-store ingestion jobs, and shadow-cycle collection path
- Identified the main integration hazard: adding L9 to canonical layer order must not silently make research a required layer in tier-1 shadow jobs
- Chosen implementation shape: latest Engine B scoring artifact -> deterministic L9 LayerScore -> persisted `signal_layer` event -> composite/shadow consumption

**Current state:** No code edits for L9 yet. Existing composite logic is still L1-L8 only.

**Next steps:** Add L9 layer contracts + research signal job runner, keep required-layer defaults non-disruptive, then verify with focused L9/composite tests.

## 2026-03-09 10:52 UTC — Research build tranche 15 complete (INT-1 L9 research signal integration)

**Context:** Continued the research backlog after intake/cutover support so Engine B findings flow into the existing composite scorer.

**Work done:**
- Added canonical signal layer `L9_RESEARCH`:
  - `app/signal/types.py`
  - extended `LayerId`, `LAYER_ORDER`, and rebalanced default weights so all layer weights still sum to 1.0
- Added L9 registry contract:
  - `app/signal/layer_registry.py`
  - label=`Research Overlay`, source=`research-engine-b`, event-driven freshness window, required detail keys for artifact provenance and translated score
- Added deterministic L9 translation layer:
  - `app/signal/layers/research.py`
  - converts latest Engine B scoring artifact into `LayerScore`
  - preserves provenance via `provenance_ref=artifact_id`
  - outcome-aware score mapping: `promote` keeps score, `revise` caps lower, `park`/`reject` degrade sharply
  - unresolved objections emit `research_blocking_objections`; rejected/retired states emit `research_rejected`
- Extended signal veto policy:
  - `app/signal/decision.py` hard-block set now includes research veto codes so blocked research cannot silently pass through on strong L1-L8 scores
- Added L9 ingestion job runner:
  - `intelligence/jobs/research_signal_job.py`
  - loads latest Engine B `scoring_result` artifact per ticker from PostgreSQL research storage
  - writes `signal_layer` events into the existing SQLite event stream for shadow/composite consumption
- Wired L9 into tier-1 shadow orchestration:
  - `intelligence/jobs/signal_layer_jobs.py`
  - runs research-signal ingest alongside other layer jobs
  - preserves non-disruptive defaults by changing `Tier1ShadowJobsConfig.required_layers` to `DEFAULT_REQUIRED_LAYERS` instead of raw `LAYER_ORDER`
  - adds `research_summary` to the tier-1 result payload
- Minor consistency updates:
  - `app/engine/trading_dag.py` and `app/engine/scheduler.py` comments updated from L1-L8 to L1-L9
  - `tests/test_signal_engine_e2e.py` updated one missing-layer assertion to stay aligned with canonical layer count

**Key files touched:**
- `app/signal/types.py`
- `app/signal/layer_registry.py`
- `app/signal/decision.py`
- `app/signal/layers/research.py`
- `intelligence/jobs/research_signal_job.py`
- `intelligence/jobs/signal_layer_jobs.py`
- `app/engine/trading_dag.py`
- `app/engine/scheduler.py`
- `tests/test_signal_l9.py`
- `tests/test_signal_engine_e2e.py`

**Verification:**
- `python -m py_compile app/signal/types.py app/signal/layer_registry.py app/signal/decision.py app/signal/layers/research.py intelligence/jobs/research_signal_job.py intelligence/jobs/signal_layer_jobs.py app/engine/trading_dag.py app/engine/scheduler.py tests/test_signal_l9.py`
- `pytest -q tests/test_signal_l9.py tests/test_signal_contracts.py tests/test_signal_layer_registry.py tests/test_signal_composite.py`
- Result: `41 passed in 0.67s`
- `pytest -q tests/test_signal_engine_e2e.py -k 'test_shadow_cycle_soft_mode_scores_partial_layers or test_tier1_jobs_reports_layer_job_statuses or test_tier1_jobs_sa_quant_failure_handled or test_tier1_result_json_serializable'`
- Result: `4 passed, 66 deselected in 20.87s`

**Current state:**
- Engine B research output now has a real path into composite scoring via persisted `signal_layer` events.
- Research remains additive, not mandatory, in default shadow-job configs.
- Blocking/rejected research can now suppress otherwise-strong composite decisions through explicit research veto codes.

**Next steps:**
- Expose operator surfaces/actions for synthesis and post-mortem generation in `/research`
- Add archive/history surfaces for generated synthesis/post-mortem artifacts
- Expand E2E coverage once the broader `TestClient(server.app)` path is stable in this runtime

- 2026-03-09 11:10 UTC — Research operator/archive tranche start
  - Scope: synthesis action, post-mortem action, archive/history fragments for research artifacts
- 2026-03-09 11:24 UTC — Research build tranche 16 complete (operator actions + archive surfaces)
  - Added chain-viewer synthesis/post-mortem actions targeting a dedicated operator-output panel
  - Added synthesis history + post-mortem + retirement archive fragment on /research
  - Added focused route/helper coverage for research operator/archive surfaces
- 2026-03-09 11:34 UTC — Signal-shadow research overlay tranche start
  - Scope: expose L9 research scores and research veto diagnostics in the Signal Engine shadow UI
- 2026-03-09 11:45 UTC — Research build tranche 17 complete (signal-shadow research overlay visibility)
  - Enriched signal-shadow payloads with L9 research score, research veto metadata, and research overlay diagnostics
  - Exposed research score and veto columns in the Signal Engine shadow UI
  - Verified with focused enrichment test + direct signal-engine template render smoke check
- 2026-03-09 11:57 UTC — Research build tranche 18 complete (archive filtering + completed-chain drilldown)
  - Added ticker/query/view filtering for the /research archive fragment
  - Added completed-chain summary cards with direct chain-view and synthesize actions
  - Verified with focused archive helper tests and direct research-archive template render smoke check
- 2026-03-09 12:05 UTC — Job-detail signal/research summary tranche start
  - Scope: compact summaries for signal shadow / tier-1 jobs plus jobs-table access to those results
- 2026-03-09 12:13 UTC — Research build tranche 19 complete (job-detail signal/research summaries)
  - Added compact signal shadow / tier-1 research overlay summaries to job detail
  - Extended Jobs table view support to signal shadow and Engine B research jobs
  - Verified with focused job-detail helper tests and direct template render smoke check
- 2026-03-09 14:06 UTC — Research build tranche 20 start
  - Scope: enrich /research completed-chain cards with lifecycle milestone summaries from full chain artifacts
- 2026-03-09 14:10 UTC — Research build tranche 20 complete (completed-chain lifecycle summaries)
  - Added chain lifecycle summarization to /research archive completed-chain cards using full artifact-chain contents
  - Exposed lifecycle stage badges and stage coverage counts for completed chains
  - Verified with focused archive helper tests, archive template route-surface checks, and direct research-archive render smoke check
- 2026-03-09 14:16 UTC — Research build tranche 21 start
  - Scope: expose research-system routing and Engine B state on intel/jobs operator surfaces
- 2026-03-09 14:22 UTC — Research build tranche 21 complete (routing state on intel/jobs surfaces)
  - Added shared research-system routing context with Engine B running/status/queue depth
  - Exposed routing-state badges on /intel, Jobs, and Job Detail surfaces
  - Verified with focused cutover/job-detail tests and direct intel/jobs template render smoke check
- 2026-03-09 14:24 UTC — Research build tranche 22 start
  - Scope: add integrated route-level verification for research operator workflow without TestClient
- 2026-03-09 14:27 UTC — Research build tranche 22 complete (integrated research operator workflow verification)
  - Added route-level workflow coverage for chain viewer, synthesis action, post-mortem action, and archive rendering
  - Avoided the hanging TestClient path by exercising route endpoints with shared fake stores and events
  - Verified with focused research operator/archive pytest slice
- 2026-03-09 14:24 UTC — Research build tranche 23 start
  - Scope: stabilize TestClient-based app coverage for research cutover and operator workflows
- 2026-03-09 15:04 UTC — Research build tranche 23 complete (pytest-side sync endpoint stabilization)
  - Added a pytest autouse workaround for the broken anyio sync-offload path so FastAPI sync endpoints stop deadlocking under research-targeted tests
  - Added a lightweight ASGI test client and moved the hanging research/signal/intel API suites off TestClient
  - Verified with: 9 passed (signal shadow API), 15 passed (intel pipeline), 2 passed (research jobs), 26 passed combined
- 2026-03-09 15:08 UTC — Research build tranche 24 start
  - Scope: sweep remaining legacy TestClient suites and convert only the still-blocking API/UI tests to the ASGI harness
- 2026-03-09 15:26 UTC — Research build tranche 24 complete (legacy TestClient sweep)
  - Migrated the remaining API/UI acceptance suites from TestClient to the in-repo ASGI harness, including Phase O and signal-engine E2E coverage
  - Verified 95 passed across the API/control/ledger/risk/promotion/webhook/status surface batch, 138 passed across signal-engine E2E + Phase O, and 259 passed across the full migrated batch
  - Confirmed no remaining real TestClient imports/usages under tests/
- 2026-03-09 15:29 UTC — Research build tranche 25 start
  - Scope: run a broader full-suite pytest re-baseline after ASGI harness migration and fix remaining failures
- 2026-03-09 15:36 UTC — Research build tranche 25 complete (full-suite pytest re-baseline)
  - Refactored `test_api_ping.py` into a manual IG smoke script with all live broker activity behind `main()`, so pytest collection no longer performs network/auth side effects at import time
  - Verified the broader repository baseline with `pytest -q --maxfail=20`: 2510 passed, 608 warnings in 181.84s
  - Remaining follow-up is warning cleanup only, primarily `datetime.utcnow()` deprecations and upstream `yfinance` deprecation noise
- 2026-03-09 15:37 UTC — Research build tranche 26 start
  - Scope: remove the post-baseline warning debt from repo-owned UTC timestamp calls, noisy tests, and the yfinance earnings fallback
- 2026-03-09 16:29 UTC — Research build tranche 26 complete (warning cleanup + clean full-suite baseline)
  - Added shared UTC helpers for legacy naive timestamp storage paths and replaced deprecated `datetime.utcnow()` usage across orchestration, ledger, dispatcher, risk, and order-intent persistence
  - Updated warning-heavy tests to use the shared UTC helpers and tightened `intelligence/earnings_client.py` to prefer `earnings_dates` before the deprecated yfinance quarterly earnings path
  - Verified with focused slices (51 passed; 3 passed in the signal-engine PEAD path) and a final clean repository baseline: `pytest -q --maxfail=20` → 2510 passed in 174.46s
- 2026-03-09 16:33 UTC — Research build tranche 27 start
  - Scope: address Claude review findings by fixing the RegimeSnapshot sizing-factor safety floor and restoring explicit scoring-stage granularity without breaking PromotionOutcome consumers
- 2026-03-09 16:39 UTC — Research build tranche 27 complete (Claude review follow-up)
  - Tightened `RegimeSnapshot.sizing_factor` validation from `ge=0.0` to `ge=0.5` and added a lower-bound regression test
  - Kept the existing 4-state `PromotionOutcome` contract, but added explicit `ProgressionStage` / `next_stage` metadata to scoring artifacts so 70-79 test, 80-89 experiment, and 90+ pilot are preserved without a breaking enum change
  - Propagated `next_stage` into the L9 research snapshot metadata and verified the focused research slice: `26 passed`
- 2026-03-09 16:33 UTC — Research build tranche 27 start
  - Scope: address Claude review findings by fixing RegimeSnapshot sizing floor and adding explicit non-breaking scoring stage metadata
- 2026-03-09 16:40 UTC — Research build tranche 28 start
  - Scope: make scoring-stage metadata operational by driving Engine B artifact progression, artifact-side promotion gating, and operator-visible stage surfaces
- 2026-03-09 16:50 UTC — Research build tranche 28 complete (stage-aware Engine B progression)
  - Engine B now routes by `next_stage`: `test` creates `TestSpec`, `experiment` produces `ExperimentReport`, and `pilot` produces `TradeSheet` plus `pilot_ready` state with human-signoff flagging
  - Artifact-side promotion gating now distinguishes test/experiment/pilot chains instead of treating all `promote` scores identically, and operator-facing research summaries now surface the stage explicitly
  - Verified focused coverage across pipeline progression, promotion gating, runtime wiring, dashboard, control-plane status, artifact viewer, scorer, and L9 metadata: `42 passed`
- 2026-03-09 17:20 UTC — Research build tranche 29 start
  - Scope: begin operational activation by adding research DB readiness visibility, MVP market-data seeding/ingest tooling, and a real backtest adapter behind Engine B experiments
- 2026-03-09 17:33 UTC — Research build tranche 29 complete (operational activation foundations)
  - Added `research_db_status()` and surfaced research PostgreSQL readiness in control-plane, research engine-status, and intel routing views so missing schema/connectivity is visible before operators try to run Engine A/B
  - Added idempotent MVP market-data bootstrapping: `seed_mvp_universe()`, `ingest_seeded_market_data()`, `market_data_readiness()`, plus a one-shot bootstrap script for seeding and historical ingest
  - Replaced Engine B's stub experiment runner on the default runtime path with `ResearchBacktestAdapter`, which wraps the existing analytics backtester into `VariantResult` payloads used by `ExperimentService`
  - Verified targeted and adjacent slices: `17 passed` on the new operational modules, then `20 passed` on nearby experiment/pipeline/server surface tests
- 2026-03-09 17:42 UTC — Research build tranche 30 start
  - Scope: close the pilot sign-off gap by making operator approval/rejection an explicit artifact-driven workflow instead of a passive `requires_human_signoff` flag
- 2026-03-09 17:55 UTC — Research build tranche 30 complete (pilot sign-off workflow)
  - Added a dedicated `pilot_decision` artifact schema plus `PilotSignoffService` so operator approval/rejection is persisted into the research chain with audit history
  - Added `POST /api/actions/research/pilot-approve` and `POST /api/actions/research/pilot-reject`, surfaced pilot sign-off status/actions in the chain viewer and operator-output panel, and updated pipeline state to `review_cleared` / `review_rejected`
  - Tightened artifact-side promotion gating so `pilot_ready` no longer implicitly passes: pending sign-off now blocks with `ARTIFACT_PILOT_SIGNOFF_PENDING`, approved pilot chains pass with `ARTIFACT_PILOT_APPROVED`, and rejected pilot chains hard-block with `ARTIFACT_PILOT_REJECTED`
  - Verified focused and adjacent research slices: `22 passed` on gate/viewer/operator/action tests, then `20 passed` on nearby engine/runtime/server tests

- 2026-03-09 18:31 UTC — Research build tranche 31 start
  - Scope: deliver the remaining operator action endpoints for review kills and Engine A rebalance decisions, keeping them artifact-backed and visible in the research UI
- 2026-03-09 18:38 UTC — Research build tranche 31 complete (operator action endpoints)
  - Added `POST /api/actions/research/confirm-kill`, `POST /api/actions/research/override-kill`, `POST /api/actions/research/execute-rebalance`, and `POST /api/actions/research/dismiss-rebalance`, all wired through the existing research operator-output surface
  - Confirm/override kill now resolve pending review chains with explicit operator decisions, and confirm-kill also records a retirement memo on the same chain so the action is auditable in artifacts plus dashboard history
  - Engine A rebalance decisions now supersede the latest rebalance sheet with operator metadata; execute also records a manual trade sheet plus execution report, while dismiss records a blocked rebalance decision
  - Updated research alert/rebalance/operator-output templates to expose the new controls and feedback, and verified with focused plus adjacent slices: `22 passed` then `23 passed`

- 2026-03-09 19:01 UTC — Research build tranche 32 start
  - Scope: add an explicit research readiness report so operators can see DB, market-data, engine-run, and sign-off blockers for real-data validation in one place
- 2026-03-09 19:08 UTC — Research build tranche 32 complete (research readiness surface)
  - Added shared readiness logic in `research/readiness.py`, including pipeline-stage counts, market-data readiness summarization, engine last-run normalization, and actionable next steps for cutover
  - Added a new `/fragments/research/readiness` surface plus `_research_readiness.html`, and surfaced it at the top of the research dashboard so DB provisioning, market-data coverage, Engine A/B validation state, and pending operator queues are visible together
  - Added `scripts/research_readiness_report.py` for CLI JSON output and verified with focused plus adjacent slices: `15 passed` then `18 passed`, plus a Jinja render smoke

- 2026-03-09 19:15 UTC — Research restart checkpoint written
  - Wrote `ops/RESEARCH_RESTART_CHECKPOINT.md` with current operational state, recent tranches, green test slices, and the exact resume procedure for a fresh shell that can see `RESEARCH_DB_DSN`
  - Explicitly recorded that the current shell does not see `RESEARCH_DB_DSN`, so live PostgreSQL validation must resume from a refreshed environment

