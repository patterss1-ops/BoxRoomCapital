# Research System — Implementation Backlog

**Date:** 2026-03-08 | **Target:** Single overnight Codex build session
**Source specs:** `RESEARCH_SYSTEM_ARCHITECTURE.md`, `RESEARCH_SYSTEM_TECH_SPEC.md`, `RESEARCH_SYSTEM_UX_SPEC.md`, `RESEARCH_SYSTEM_PLAN_FINAL.md`
**Assumes:** Architecture Plan v2 (P0-P7) complete. PostgreSQL available on Replit. Existing UX surfaces (intel council, idea pipeline, SA captures, costs, intelligence feed, 2 chart endpoints) already built.

---

## Build Strategy

**Two parallel tracks:**
- **Track A:** Phase 0 (market data) + Phase 1 (artifacts) + Engine A pipeline
- **Track B:** Phase 1 (shared) + Phases 2-7 + Engine B pipeline + UX fragments

Phase 1 is shared by both tracks. Phases 0 and 1 can build in parallel since Phase 0 produces market_data models while Phase 1 produces artifact models — no dependency until integration.

**Landing order (per agreed plan):**
1. Extend existing `/research` and `/intel` surfaces with artifact-aware fragments
2. Replace 4-model council with artifact chain on current event/intel flow
3. Evolve `/research` into mature multi-tab surface after artifact flow proves stable
4. Land Engine A futures views after market-data layer is live

---

## INFRASTRUCTURE (do first — everything else depends on these)

### I-1: PostgreSQL connection factory
- **File:** `data/pg_connection.py`
- **Work:** Thread-safe connection pool using `psycopg2.pool.ThreadedConnectionPool`
- **Functions:** `get_pg_connection()`, `release_pg_connection(conn)`, `init_research_schema()`
- **Config:** Add `RESEARCH_DB_DSN = _env_str("RESEARCH_DB_DSN", "postgresql://localhost:5432/boxroom_research")` to `config.py`
- **Also:** Update `.env.example` with `RESEARCH_DB_DSN`
- **Schema init:** Create `research` schema + all tables from architecture doc sections 2.3-2.4 (instruments, raw_bars, canonical_bars, universe_membership, corporate_actions, futures_contracts, roll_calendar, snapshots, artifacts, model_calls, artifact_links, pipeline_state)
- **Test:** `tests/test_pg_connection.py` — pool init, get/release, schema creation
- **Acceptance:** Pool works, schema creates idempotently, connection reuse works
- **Spec ref:** Tech spec §10.1-10.3

### I-2: Research package scaffold
- **Work:** Create entire directory structure from architecture doc §7
- **Directories:** `research/`, `research/market_data/`, `research/engine_a/`, `research/engine_b/`, `research/shared/`, `research/prompts/`
- **Files:** All `__init__.py` files
- **No logic yet — just the skeleton so subsequent tasks have somewhere to land

---

## PHASE 0: MARKET DATA INFRASTRUCTURE

### P0-1: InstrumentMaster model + CRUD
- **File:** `research/market_data/instruments.py`
- **Model:** `InstrumentMaster` Pydantic model (symbol, asset_class, venue, currency, session_template, multiplier, tick_size, vendor_ids, is_active, listing_date, delisting_date, metadata)
- **CRUD:** `create_instrument()`, `get_instrument()`, `get_by_symbol()`, `update_instrument()`, `list_instruments()`, `search_instruments()`
- **SQL:** Uses `research.instruments` table
- **Test:** `tests/test_instruments.py` — create, get, update, vendor ID mapping, deactivation, listing/delisting
- **Est tests:** ~8
- **Spec ref:** Tech spec §0.1

### P0-2: RawBar model + immutable ingestion
- **File:** `research/market_data/raw_bars.py`
- **Model:** `RawBar` Pydantic model (instrument_id, vendor, bar_timestamp, session_code, OHLCV, bid/ask, ingestion_ver)
- **Functions:** `ingest_bars(bars: list[RawBar])` — bulk INSERT, never UPDATE; `get_bars(instrument_id, start, end, vendor)`, `get_latest_bar(instrument_id, vendor)`
- **SQL:** Uses `research.raw_bars` table
- **Immutability:** No update functions. Append-only.
- **Test:** `tests/test_raw_bars.py` — ingest, query by range, vendor provenance preserved, no update allowed
- **Est tests:** ~8
- **Spec ref:** Tech spec §0.2

### P0-3: CanonicalBar model + versioned normalization
- **File:** `research/market_data/canonical_bars.py`
- **Model:** `CanonicalBar` Pydantic model (instrument_id, bar_date, OHLCV, adj_close, dollar_volume, session_template, data_version, quality_flags)
- **Functions:** `normalize_raw_to_canonical(raw_bars, corporate_actions)`, `get_canonical_bars(instrument_id, start, end)`, `reprocess_bars(instrument_id, start, end)` — increments data_version
- **Quality checks:** Spike detection, session alignment validation
- **Test:** `tests/test_canonical_bars.py` — normalization, version increment on reprocess, quality flag assignment, adjustment for splits/dividends
- **Est tests:** ~8
- **Spec ref:** Tech spec §0.3

### P0-4: CorporateAction + UniverseMembership models
- **File:** `research/market_data/corporate_actions.py`
- **Model:** `CorporateAction` (instrument_id, action_type, ex_date, ratio, details)
- **Functions:** `record_action()`, `get_actions(instrument_id, start, end)`, `get_adjustment_factor(instrument_id, from_date, to_date)`
- **File:** `research/market_data/universe.py`
- **Model:** `UniverseMembership` (instrument_id, universe, from_date, to_date)
- **Functions:** `add_membership()`, `remove_membership()`, `get_universe_as_of(universe, date)`, `was_member(instrument_id, universe, date)`
- **SQL:** Uses `research.corporate_actions` and `research.universe_membership`
- **Test:** `tests/test_corporate_actions.py` + `tests/test_universe.py` — action recording, adjustment factor calc, membership queries, point-in-time membership check
- **Est tests:** ~10
- **Spec ref:** Tech spec §0.4

### P0-5: Futures block (Carver pattern)
- **File:** `research/market_data/futures.py`
- **Models:** `FuturesContract`, `RollCalendarEntry`, `MultiplePrices`, `ContinuousSeries`
- **Functions:**
  - `register_contract()`, `get_contracts(root_symbol)`, `get_front_contract(root_symbol, as_of)`
  - `add_roll_entry()`, `get_roll_calendar(root_symbol)`, `get_next_roll(root_symbol, as_of)`
  - `build_multiple_prices(root_symbol, as_of)` — current/next/carry mapping
  - `build_continuous_series(root_symbol, method='panama')` — back-adjusted from contract-level data
  - `get_carry_series(root_symbol, start, end)` — term structure carry
- **SQL:** Uses `research.futures_contracts` and `research.roll_calendar`
- **Test:** `tests/test_futures_data.py` — contract registration, roll calendar, multiple prices, continuous series generation, carry calculation
- **Est tests:** ~10
- **Spec ref:** Tech spec §0.5

### P0-6: LiquidityCostSeries
- **File:** `research/market_data/liquidity.py`
- **Model:** `LiquidityCostEntry` (instrument_id, as_of, inside_spread, spread_cost_bps, commission_per_unit, funding_rate, borrow_cost)
- **Functions:** `record_cost()`, `get_cost_series(instrument_id, start, end)`, `get_latest_cost(instrument_id)`
- **Test:** `tests/test_liquidity.py` — record, query, latest
- **Est tests:** ~4
- **Spec ref:** Tech spec §0.6

### P0-7: Snapshot engine
- **File:** `research/market_data/snapshots.py`
- **Models:** `SnapshotType` enum (eod_market, intraday_signal, term_structure, universe, regime, broker_account, exec_quality), `Snapshot` model
- **Functions:** `save_snapshot(snapshot_type, as_of, body)`, `get_latest_snapshot(snapshot_type)`, `get_snapshots(snapshot_type, start, end)`, `get_snapshot(snapshot_id)`
- **SQL:** Uses `research.snapshots` table
- **Test:** `tests/test_snapshots.py` — save, get latest, query by type + time range
- **Est tests:** ~6
- **Spec ref:** Tech spec §0.7

### P0-8: Vendor adapter base + IBKR stub
- **File:** `research/market_data/ingestion.py`
- **Classes:** `VendorAdapter` ABC (vendor_name, fetch_daily_bars, fetch_instrument_info), `IBKRAdapter` (stub using existing `broker/ibkr.py` patterns), `NorgateAdapter` (stub), `BarchartAdapter` (stub)
- **Only IBKR needs working implementation — others are interface stubs for now
- **Test:** `tests/test_ingestion.py` — adapter interface compliance, IBKR stub returns valid models
- **Est tests:** ~6
- **Spec ref:** Tech spec §0.8

### P0-9: Engine A universe seeding
- **File:** `research/market_data/seed_universe.py`
- **Work:** Idempotent `seed_mvp_universe()` populating InstrumentMaster with MVP futures (ES, NQ, YM, RTY, ZN, ZB, ZF, GC, SI, CL, NG, HG, ZC, ZS, ZW, 6E, 6B, 6J) + initial roll calendar entries for front 2 contracts
- **Depends on:** P0-1, P0-5
- **Test:** `tests/test_seed_universe.py` — seed creates instruments, idempotent re-run, roll calendar populated
- **Est tests:** ~5

---

## PHASE 1: ARTIFACT SCHEMAS & STORAGE

### P1-1: Artifact enums and base
- **File:** `research/artifacts.py`
- **Classes:** `ArtifactType` enum (15 types), `ArtifactStatus` enum, `EdgeFamily` enum (7 families), `Engine` enum, `PromotionOutcome` enum, `ArtifactEnvelope` dataclass
- **No DB — pure Python models
- **Test:** `tests/test_artifacts.py` — enum values, envelope creation, serialization
- **Est tests:** ~8
- **Spec ref:** Tech spec §1.1

### P1-2: Artifact body models (Pydantic)
- **File:** `research/artifacts.py` (continued)
- **Models (all Pydantic BaseModel):**
  - `EventCard` — source_ids, source_class, source_credibility, claims, affected_instruments, materiality, time_sensitivity, raw_content_hash
  - `HypothesisCard` — hypothesis_id, edge_family, event_card_ref, market_implied_view, variant_view, mechanism, catalyst, direction, horizon, invalidators, failure_regimes, candidate_expressions, testable_predictions
  - `FalsificationMemo` — hypothesis_ref, cheapest_alternative, beta_leakage_check (BetaLeakageResult), crowding_check (CrowdingResult), prior_evidence (list[PriorEvidence]), unresolved_objections, resolved_objections, challenge_model
  - `BetaLeakageResult`, `CrowdingResult`, `PriorEvidence` — supporting models
  - `TestSpec` — hypothesis_ref, datasets (list[DatasetSpec]), splits (SplitSpec), search_budget, cost_model_ref, eval_metrics
  - `DatasetSpec`, `SplitSpec` — supporting models
  - `ExperimentReport` — test_spec_ref, variants_tested, best_variant, gross_metrics (PerformanceMetrics), net_metrics, robustness_checks, capacity_estimate, implementation_caveats
  - `PerformanceMetrics`, `RobustnessCheck`, `CapacityEstimate` — supporting models
  - `TradeSheet` — hypothesis_ref, experiment_ref, instruments (list[InstrumentSpec]), sizing (SizingSpec), entry_rules, exit_rules, holding_period_target, risk_limits (RiskLimits), kill_criteria
  - `InstrumentSpec`, `SizingSpec`, `RiskLimits` — supporting models
  - `RetirementMemo` — hypothesis_ref, trigger, trigger_detail, diagnosis, lessons, final_status
  - `EngineASignalSet` — as_of, signals (dict[str, SignalValue]), forecast_weights, combined_forecast, regime_ref
  - `SignalValue` — signal_type, raw_value, normalized_value, lookback, confidence
  - `RebalanceSheet` — as_of, current_positions, target_positions, deltas, estimated_cost, approval_status
  - `ExecutionReport` — as_of, trades_submitted, trades_filled, fills (list[FillDetail]), slippage, cost, venue, latency
  - `FillDetail` — instrument, side, quantity, price, timestamp, venue
  - `PostMortemNote` — hypothesis_ref, thesis_assessment, what_worked, what_failed, lessons, data_quality_issues
  - `RegimeSnapshot` — as_of, vol_regime, trend_regime, carry_regime, macro_regime, sizing_factor, active_overrides, indicators
  - `ScoringResult` — hypothesis_ref, falsification_ref, dimension_scores, raw_total, penalties, final_score, outcome, blocking_objections
- **Test:** `tests/test_artifacts.py` (continued) — validate required fields, reject invalid values, field ranges (ge/le), Literal type enforcement
- **Est tests:** ~26
- **Spec ref:** Tech spec §1.2

### P1-3: Artifact store (PostgreSQL JSONB)
- **File:** `research/artifact_store.py`
- **Class:** `ArtifactStore`
- **Methods:**
  - `save(envelope)` → artifact_id. New chain if chain_id=None, new version if parent_id set. Supersede previous version.
  - `get(artifact_id)` → Optional[ArtifactEnvelope]
  - `get_chain(chain_id)` → list[ArtifactEnvelope] ordered by version
  - `get_latest(chain_id)` → Optional[ArtifactEnvelope]
  - `query(artifact_type, engine, ticker, edge_family, status, created_after, created_before, tags, search_text, limit, offset)` → list[ArtifactEnvelope]
  - `get_linked(artifact_id, link_type)` → list[ArtifactEnvelope]
  - `count(artifact_type, engine, status)` → int
- **SQL:** Uses `research.artifacts`, `research.artifact_links` tables
- **Immutability enforced:** No UPDATE method exists. Versioning via INSERT + supersede.
- **Full-text search:** Via `search_text` tsvector column
- **Test:** `tests/test_artifact_store.py` — save/get, versioning, chain retrieval, query filters, full-text search, link traversal, count, immutability (no update path)
- **Est tests:** ~15
- **Spec ref:** Tech spec §1.3-1.5

### P1-4: Promotion gate 4-state extension
- **File:** `fund/promotion_gate.py` (modify existing)
- **Changes:**
  - Add `PromotionOutcome` import from `research.artifacts`
  - Extend `PromotionGateDecision` with: `outcome: PromotionOutcome`, `artifact_refs: list[str]`, `blocking_objections: list[str]`
  - Keep `allowed` field for backward compat (`allowed = outcome == PROMOTE`)
  - Add `evaluate_with_artifacts()` method that checks artifact chain + objections
- **Test:** `tests/test_promotion_gate_v2.py` — 4-state outcomes, artifact refs populated, backward compat (existing tests still pass), blocking objections flow through
- **Est tests:** ~10
- **Spec ref:** Tech spec §1.6

---

## PHASE 3: EDGE TAXONOMY ENFORCEMENT

### P3-1: Taxonomy service
- **File:** `research/taxonomy.py`
- **Class:** `TaxonomyService`
- **Data:** `APPROVED_FAMILIES` list (7 families), `FAMILY_DESCRIPTIONS` dict with description, typical_horizon, typical_instruments, primary_engine per family
- **Methods:** `validate(edge_family)` → EdgeFamily or raise TaxonomyRejection, `get_family_info(family)`, `suggest_engine(family)`
- **Exception:** `TaxonomyRejection`
- **Depends on:** I-2 (scaffold only)
- **Integration:** Called by HypothesisService after LLM generation
- **Test:** `tests/test_taxonomy.py` — all 7 families validate, invalid family raises, engine suggestion, family info retrieval
- **Est tests:** ~15
- **Spec ref:** Tech spec §3.1-3.2

---

## PHASE 2: STRUCTURED CHALLENGE PIPELINE (REPLACE COUNCIL)

### P2-1: Model router
- **File:** `research/model_router.py`
- **Classes:** `ModelConfig` dataclass, `ModelRouter`, `ModelResponse` dataclass
- **ModelRouter methods:**
  - `__init__(config, artifact_store)` — load from `config.RESEARCH_MODEL_CONFIG`
  - `call(service, prompt, system_prompt, artifact_id, engine)` → ModelResponse
  - `get_model_for_service(service)` → ModelConfig
  - `validate_no_self_challenge(formation_service, challenge_service)` — raise if same config/prompt lineage
- **Features:** Retry with exponential backoff, cost logging to `research.model_calls`, timeout enforcement, fallback model, prompt hash tracking
- **Refactor:** Wire existing `intelligence/ai_panel/` client patterns (anthropic_client, openai client, etc.) through unified interface
- **Config:** Add `RESEARCH_MODEL_CONFIG` dict to `config.py` (service → provider/model/params mapping)
- **Test:** `tests/test_model_router.py` — routing to correct provider, retry behavior, cost logging, timeout, fallback, independence validation, prompt hash stored and retrievable for audit
- **Est tests:** ~12
- **Spec ref:** Tech spec §2.1

### P2-1b: Prompt hash revalidation mechanism
- **File:** `research/prompt_registry.py`
- **Work:** Hash all prompt templates at startup. Store in `research.prompt_hashes`. Compare on each call. Flag PROMPT_DRIFT on change. Operator can acknowledge or re-run.
- **Functions:** `register_prompts()`, `check_drift(service)`, `acknowledge_drift(service)`, `get_prompt_hash(service)`
- **Depends on:** P2-1, P2-2
- **Test:** `tests/test_prompt_registry.py` — hash stable, drift detected on change, ack clears
- **Est tests:** ~6

### P2-2: Prompt templates v1
- **Files:**
  - `research/prompts/__init__.py`
  - `research/prompts/v1_signal_extraction.py` — system prompt + user prompt for raw text → EventCard JSON
  - `research/prompts/v1_hypothesis.py` — system prompt + user prompt for EventCard + RegimeSnapshot + taxonomy → HypothesisCard JSON
  - `research/prompts/v1_challenge.py` — system prompt + user prompt for HypothesisCard → FalsificationMemo JSON. Must include instruction: "Do NOT smooth away objections. List all unresolved concerns explicitly."
- **Each prompt template is a function returning (system_prompt, user_prompt) given the input data
- **Include full edge taxonomy in hypothesis prompt
- **No tests needed for templates themselves — tested via service integration tests

### P2-3: Signal extraction service
- **File:** `research/engine_b/signal_extraction.py`
- **Class:** `SignalExtractionService`
- **Methods:** `extract(raw_content, source_class, source_credibility, source_ids)` → ArtifactEnvelope
- **Logic:** Call model_router with extraction prompt, parse JSON response into EventCard, validate with Pydantic, wrap in ArtifactEnvelope, save to store
- **Test:** `tests/test_signal_extraction.py` — valid extraction, invalid source rejection, Pydantic validation of output, artifact saved correctly
- **Est tests:** ~8
- **Spec ref:** Tech spec §2.2

### P2-4: Hypothesis service
- **File:** `research/engine_b/hypothesis.py`
- **Class:** `HypothesisService`
- **Depends on:** P1-3, P3-1
- **Methods:** `form_hypothesis(event_card_id, regime_snapshot)` → ArtifactEnvelope
- **Logic:** Fetch EventCard, call model_router with hypothesis prompt (includes taxonomy + regime), parse into HypothesisCard, validate edge_family via TaxonomyService, save
- **Rejection:** If LLM output doesn't map to valid edge family → store as RETIRED with reason TAXONOMY_REJECTION
- **Test:** `tests/test_hypothesis.py` — valid formation, taxonomy validation, rejection for invalid family, artifact linking (event_card_ref populated)
- **Est tests:** ~8
- **Spec ref:** Tech spec §2.3

### P2-5: Challenge service
- **File:** `research/engine_b/challenge.py`
- **Class:** `ChallengeService`
- **Methods:** `challenge(hypothesis_id)` → ArtifactEnvelope
- **Logic:** Fetch HypothesisCard, validate model_router independence (different config from formation), call with challenge prompt, parse into FalsificationMemo, save
- **Critical:** Unresolved objections list MUST be populated. If LLM returns empty unresolved list but has clear concerns in text, flag as warning.
- **Test:** `tests/test_challenge.py` — valid challenge, independence enforcement (reject if same config as formation), unresolved objections preserved, artifact linking
- **Est tests:** ~8
- **Spec ref:** Tech spec §2.4

### P2-6: Scoring engine
- **File:** `research/scorer.py`
- **Class:** `ScoringEngine`
- **Methods:** `score(hypothesis_id, falsification_id)` → ArtifactEnvelope
- **Logic:** Fetch hypothesis + falsification + event artifacts, compute each rubric dimension (9 dimensions, 100 points max), apply penalties (complexity -15, crowding -10, fragility -10), compute final score, determine outcome per thresholds (<60 reject, 60-69 park/revise, 70-79 test, 80-89 experiment, 90+ live pilot (human sign-off required)), check blocking objections
- **Deterministic:** No LLM calls. Pure computation from artifact fields.
- **Blocking rule:** Any unresolved objection → outcome cannot be PROMOTE regardless of score
- **Test:** `tests/test_scorer.py` — each dimension scored correctly, penalties applied, thresholds enforced, blocking objections override score, edge cases (max/min scores)
- **Est tests:** ~15
- **Spec ref:** Tech spec §2.5

### P2-7: Engine B pipeline orchestrator
- **File:** `research/engine_b/pipeline.py`
- **Class:** `EngineBPipeline`
- **Methods:**
  - `process_event(raw_content, source_class, source_credibility, source_ids)` → PipelineResult
  - `process_event_async(...)` → job_id (background thread)
  - `_update_pipeline_state(chain_id, stage, outcome)`
- **Logic:** Full chain: extract → taxonomy gate → fetch current regime → hypothesize (with regime context) → challenge → score → decide. Each step produces artifact. Pipeline halts at any rejection. Updates `research.pipeline_state`.
- **Result:** `PipelineResult` with artifacts list, outcome, score, blocking_reasons
- **Test:** `tests/test_engine_b_pipeline.py` — full happy path, rejection at each stage, pipeline state tracking, artifact chain integrity
- **Est tests:** ~12
- **Spec ref:** Tech spec §2.6

### P2-8: Engine B intake service
- **File:** `research/engine_b/intake.py`
- **Class:** `IntakeService`
- **Methods:** `ingest(raw_content, source_class, source_ids)` → normalized content
- **Logic:** Dedup (check raw_content_hash against recent artifacts), timestamp, entity-to-instrument mapping, source classification
- **File:** `research/engine_b/source_scoring.py`
- **Class:** `SourceScoringService`
- **Methods:** `score_source(source_class, source_ids)` → credibility float
- **Logic:** Tier-based scoring (filing=0.95, transcript=0.90, analyst_revision=0.85, news_wire=0.80, sa_quant=0.75, social_curated=0.50, social_general=0.20), corroboration bonus
- **Test:** `tests/test_intake.py` — dedup, scoring per tier, corroboration
- **Est tests:** ~6

---

## PHASE 4: REGIME/STATE CONTEXT SERVICE

### P4-1: Regime classifier (deterministic)
- **File:** `research/engine_a/regime.py`
- **Class:** `RegimeClassifier`
- **Methods:**
  - `classify(as_of, market_data)` → RegimeSnapshot
  - `_classify_vol(vix, vix_percentile)` → str (low/normal/high/crisis)
  - `_classify_trend(index_data)` → str (strong_trend/choppy/reversal)
  - `_classify_carry(yield_data)` → str (steep/flat/inverted)
  - `_compute_sizing_factor(vol, trend, carry)` → float (0.5-1.0, enforced ge=0.5 in Pydantic model)
- **Thresholds:** VIX <15=low, 15-25=normal, 25-35=high, >35=crisis. 10y-2y spread >100bp=steep, 0-100=flat, <0=inverted.
- **Test:** `tests/test_regime_classifier.py` — each regime state classification, sizing factor computation, combined macro regime, edge cases
- **Est tests:** ~15
- **Spec ref:** Tech spec §4.1

### P4-2: Regime journal (LLM, light)
- **File:** `research/shared/regime_journal.py`
- **Prompt:** `research/prompts/v1_regime_journal.py`
- **Class:** `RegimeJournalService`
- **Methods:** `annotate_transition(previous, current)` → ArtifactEnvelope
- **Logic:** Only called when regime changes. LLM produces ~200 word entry. Stored as REGIME_JOURNAL artifact linked to RegimeSnapshot. Never feeds back into signals.
- **Test:** `tests/test_regime_journal.py` — journal generated on change, not on same-state, artifact linkage, content is non-empty
- **Est tests:** ~6
- **Spec ref:** Tech spec §4.2

---

## PHASE 5: COST MODEL + EXPERIMENT

### P5-1: Cost model
- **File:** `research/shared/cost_model.py`
- **Class:** `CostModel`
- **Data:** `IG_COSTS` dict (uk_equity, us_equity, index, commodity, fx — spread_bps, funding_daily_bps, min_spread), `IBKR_FUTURES` dict (micro/mini/standard — commission, exchange fee), `IBKR_EQUITY` dict (us/uk — commission_pct, min)
- **Methods:**
  - `estimate_round_trip_cost(instrument_type, broker, notional, holding_days, asset_class)` → CostEstimate
  - `apply_to_backtest(trades, instrument_type, broker, asset_class)` → trades with net_return
- **Dataclass:** `CostEstimate` (entry_cost, exit_cost, holding_cost, slippage_estimate, total_round_trip, total_as_pct)
- **Test:** `tests/test_cost_model.py` — IG spread bet costs match fee schedule, IBKR futures costs, IBKR equity costs, holding cost over time, slippage, backtest application
- **Est tests:** ~15
- **Spec ref:** Tech spec §5.1

### P5-2: Experiment service
- **File:** `research/engine_b/experiment.py`
- **Class:** `ExperimentService`
- **Methods:**
  - `register_test(hypothesis_id, test_spec)` → ArtifactEnvelope. Freeze TestSpec. Validate: point-in-time, budget ≤50, cost model specified, metrics include sharpe + profit_factor.
  - `run_experiment(test_spec_id)` → ArtifactEnvelope. Execute backtest, apply cost model, compute gross AND net metrics, run robustness checks (walk_forward, subsample, parameter_sensitivity), estimate capacity, compute correlation.
  - `_walk_forward_check()` → RobustnessCheck
  - `_subsample_check()` → RobustnessCheck
  - `_parameter_sensitivity()` → RobustnessCheck
- **Immutability:** TestSpec cannot be modified after registration
- **Test:** `tests/test_experiment.py` — registration freezes spec, budget enforcement, gross vs net metrics, robustness checks run, capacity estimate, correlation computation between strategy returns, immutability enforcement
- **Est tests:** ~15
- **Spec ref:** Tech spec §5.2

---

## PHASE 6: KILL MONITOR

### P6-1: Kill monitor + retirement
- **File:** `research/shared/kill_monitor.py`
- **Classes:** `KillMonitor`, `KillCriterion` dataclass, `KillAlert` dataclass
- **Methods:**
  - `register_kill_criteria(hypothesis_id, criteria)` — from TradeSheet.kill_criteria
  - `check_all(as_of)` → list[KillAlert]. Check invalidators, drawdown, regime, data health for all active hypotheses.
  - `execute_kill(hypothesis_id, trigger, trigger_detail, operator_approved)` → ArtifactEnvelope (RetirementMemo). Update pipeline_state to retired, archive strategy, notify via Telegram.
- **Auto-kill:** Within preauthorized limits only. Never auto-scale up.
- **Test:** `tests/test_kill_monitor.py` — criteria registration, trigger detection, auto-kill within limits, operator-approved kill, RetirementMemo generation, data health check (stale data triggers alert), notification
- **Est tests:** ~12
- **Spec ref:** Tech spec §6.1

---

## PHASE 7: DECAY-TRIGGERED REVIEW

### P7-1: Decay review service
- **File:** `research/shared/decay_review.py`
- **Class:** `DecayReviewService`
- **Methods:**
  - `run_decay_check(as_of)` → list[ReviewTrigger]. Call existing `analytics/decay_detector.py`, create ReviewTrigger artifacts for warning/decay strategies, block scaling, notify operator.
  - `acknowledge_review(chain_id, operator_decision, notes)` — 4-state outcome on acknowledgement
- **Dataclass:** `ReviewTrigger` (strategy_id, trigger_source, health_status, flags, recent_metrics, baseline_metrics, recommended_action)
- **Integration:** Modify `fund/promotion_gate.py` to check for active unacknowledged decay reviews before allowing promotion. Block if found.
- **Test:** `tests/test_decay_review.py` — decay detection triggers review, promotion blocked when review pending, operator ack resumes, 4-state ack, Telegram notification
- **Est tests:** ~10
- **Spec ref:** Tech spec §7.1-7.2

---

## ENGINE A: FUTURES PIPELINE

### EA-1: Signal generators
- **File:** `research/engine_a/signals.py`
- **Classes:**
  - `TrendSignal` — EWMA crossover blended (lookbacks 8/16/32/64), normalize to [-1,+1] via sigmoid
  - `CarrySignal` — annualized carry from term structure, normalize via historical percentile
  - `ValueSignal` — z-score of real yield (5yr rolling), clip [-3,+3], normalize
  - `MomentumSignal` — 12-month return minus last month, normalize via percentile
- **All deterministic, no LLM
- **Test:** `tests/test_engine_a_signals.py` — each signal type produces expected output from test data, normalization bounds, edge cases (insufficient history)
- **Est tests:** ~12
- **Spec ref:** Tech spec §A.1

### EA-2: Portfolio constructor
- **File:** `research/engine_a/portfolio.py`
- **Depends on:** P0 (market data), P4-1 (regime classifier)
- **Class:** `PortfolioConstructor`
- **Methods:** `construct(forecasts, vol_estimates, correlations, regime, capital, contract_sizes)` → dict[str, TargetPosition]
- **Logic:** Vol-adjusted forecast → risk parity weights → correlation adjustment → target vol scaling → regime sizing factor → leverage cap → round to contracts
- **Dataclass:** `TargetPosition` (instrument, contracts, notional, weight, forecast, vol_contribution)
- **Test:** `tests/test_engine_a_portfolio.py` — weight computation, vol targeting, regime factor applied, leverage capped, contract rounding
- **Est tests:** ~10
- **Spec ref:** Tech spec §A.2

### EA-3: Rebalancer
- **File:** `research/engine_a/rebalancer.py`
- **Class:** `Rebalancer`
- **Methods:** `generate_rebalance(current_positions, target_positions, cost_model)` → ArtifactEnvelope (RebalanceSheet)
- **Logic:** Compute deltas, filter small trades (|delta/target| < threshold), estimate costs, defer non-critical if total cost too high
- **Test:** `tests/test_engine_a_rebalancer.py` — delta computation, small trade filtering, cost threshold, RebalanceSheet artifact produced
- **Est tests:** ~8
- **Spec ref:** Tech spec §A.3

### EA-4: Engine A pipeline
- **File:** `research/engine_a/pipeline.py`
- **Depends on:** EA-1..EA-3, P4-1 (regime), P1-3 (artifact store)
- **Class:** `EngineAPipeline`
- **Methods:** `run_daily(as_of)` → EngineAResult
- **Logic:** Fetch data → regime classify → compute signals → combine forecasts → construct portfolio → generate rebalance → submit through promotion gate → log execution
- **Artifacts produced:** RegimeSnapshot → EngineASignalSet → RebalanceSheet → TradeSheet → ExecutionReport
- **Test:** `tests/test_engine_a_pipeline.py` — full daily cycle, artifact chain, regime conditioning flows through, cost filter works
- **Est tests:** ~10
- **Spec ref:** Tech spec §A.4

### EA-1b: Feature store integration for Engine A
- **File:** `research/engine_a/feature_cache.py`
- **Work:** Cache computed signal values with instrument+date+signal_type key. Invalidate on data version change.
- **SQL:** `research.feature_cache` table
- **Depends on:** EA-1, I-1
- **Test:** `tests/test_feature_cache.py` — store/retrieve, invalidation, cache hit avoids recompute
- **Est tests:** ~6

### EA-5: BotControlService registration for Engine A
- **File:** `app/engine/control.py` (modify existing)
- **Work:** Register EngineAPipeline as managed service. Add start/stop/status/health.
- **Config:** `ENGINE_A_ENABLED = _env_bool("ENGINE_A_ENABLED", False)` in config.py
- **Depends on:** EA-4
- **Test:** `tests/test_engine_a_control.py` — registers, starts, stops, reports status
- **Est tests:** ~4

### EA-6: BotControlService registration for Engine B
- **File:** `app/engine/control.py` (modify existing)
- **Work:** Register EngineBPipeline as managed service.
- **Config:** `ENGINE_B_ENABLED = _env_bool("ENGINE_B_ENABLED", False)` in config.py
- **Depends on:** P2-7
- **Test:** `tests/test_engine_b_control.py` — registers, starts, stops, reports status
- **Est tests:** ~4

### SCHED-1: Scheduler integration for engines
- **File:** `app/engine/scheduler.py` (modify existing)
- **Work:** Add daily Engine A job (market close + 30min), 6-hourly decay check, hourly kill check during market hours
- **Depends on:** EA-4, P6-1, P7-1
- **Test:** `tests/test_scheduler_research.py` — jobs registered, fire at correct times (mocked clock)
- **Est tests:** ~6

---

## ENGINE B: ADDITIONAL SERVICES

### EB-1: Expression service
- **File:** `research/engine_b/expression.py`
- **Class:** `ExpressionService`
- **Methods:** `build_trade_sheet(hypothesis_id, experiment_id, regime, existing_positions)` → ArtifactEnvelope (TradeSheet)
- **Logic:** Fetch artifacts, select best instrument/broker, compute sizing (vol-adjusted, regime-factored), define entry/exit, set risk limits, extract kill criteria
- **Test:** `tests/test_expression.py` — TradeSheet produced with all required fields, sizing respects regime, kill criteria from hypothesis
- **Est tests:** ~8
- **Spec ref:** Tech spec §B.1

### EB-2: Synthesis service
- **File:** `research/shared/synthesis.py`
- **Prompt:** `research/prompts/v1_synthesis.py`
- **Class:** `SynthesisService`
- **Methods:** `synthesize(chain_id)` → str
- **Logic:** Fetch all artifacts in chain, produce operator summary. CRITICAL: Unresolved objections displayed prominently. Cannot smooth them away.
- **Test:** `tests/test_synthesis.py` — summary includes all chain elements, objections not smoothed, unresolved highlighted
- **Est tests:** ~6
- **Spec ref:** Tech spec §B.2

### EB-3: Post-mortem service
- **File:** `research/shared/post_mortem.py`
- **Prompt:** `research/prompts/v1_post_mortem.py`
- **Class:** `PostMortemService`
- **Methods:** `generate_post_mortem(hypothesis_id)` → ArtifactEnvelope (PostMortemNote)
- **Logic:** Fetch full chain + execution data, LLM analyzes thesis correctness, extracts lessons
- **Test:** `tests/test_post_mortem.py` — post-mortem generated, lessons extracted, artifact linked
- **Est tests:** ~5
- **Spec ref:** Tech spec §B.3

---

## UX: RESEARCH SURFACE (6-tab /research page + extensions)

**IMPORTANT:** The following surfaces are ALREADY BUILT — do NOT rebuild:
- Intel council panel (`/fragments/intel/council-*`)
- Idea pipeline kanban (`/fragments/ideas/*`)
- SA captures panel (`/fragments/intel/sa-*`)
- Cost tracking dashboard (`/fragments/costs/*`)
- Intelligence feed (`/fragments/intel/feed`)
- Chart endpoints: `/api/charts/equity-curve`, `/api/charts/market-prices`

All new fragments go in `app/api/server.py` inside `create_app()` unless otherwise noted.
Only visible tabs poll; hidden tabs lazy-load on activation (UX spec §11 polling budget).

### UX-1: Research Dashboard tab (Tab 1)
- **File:** `app/api/surfaces.py` (extend existing)
- **Fragments:**
  - `GET /fragments/research/pipeline-funnel` — hypothesis counts at each pipeline stage (from `research.pipeline_state`)
  - `GET /fragments/research/active-hypotheses` — table of active hypotheses with scores, stage, edge family
  - `GET /fragments/research/engine-status` — Engine A + Engine B status cards (last run, health, next scheduled)
  - `GET /fragments/research/recent-decisions` — last 20 operator decisions with outcomes
  - `GET /fragments/research/alerts` — kill alerts + decay reviews pending
- **Templates:**
  - `app/web/templates/_research_pipeline_funnel.html` — funnel visualization
  - `app/web/templates/_research_active_hypotheses.html` — hypothesis table
  - `app/web/templates/_research_engine_status.html` — engine status cards
  - `app/web/templates/_research_recent_decisions.html` — decision log
  - `app/web/templates/_research_alerts.html` — alert cards
- **Styling:** Dark Bloomberg-density theme per DESIGN_TOKENS.md. Space Grotesk + JetBrains Mono.
- **HTMX:** Polling intervals per budget table (alerts 10s, engine-status 10s, active-hypotheses 15s, funnel 30s, recent-decisions 30s). Staggered load delays.
- **Depends on:** P2-7, P6-1, P7-1
- **Test:** `tests/test_research_fragments.py` — fragment endpoints return valid HTML, correct data from store
- **Est tests:** ~8
- **Spec ref:** UX spec §2-3

### UX-2: Engine A tab (Tab 2)
- **File:** `app/api/surfaces.py` (extend)
- **Fragments:**
  - `GET /fragments/research/regime-panel` — current regime snapshot with vol/trend/carry/macro classification
  - `GET /fragments/research/signal-heatmap` — signal values across instruments (color-coded)
  - `GET /fragments/research/portfolio-targets` — current vs target positions table
  - `GET /fragments/research/rebalance-panel` — pending rebalance with approve/dismiss buttons
  - `GET /fragments/research/regime-journal` — recent regime transition journal entries
- **Templates:** `_research_regime_panel.html`, `_research_signal_heatmap.html`, `_research_portfolio_targets.html`, `_research_rebalance_panel.html`, `_research_regime_journal.html`
- **Depends on:** EA-4, P4-1, P4-2
- **Test:** `tests/test_engine_a_fragments.py` — fragments render with mock data, regime panel shows correct classification
- **Est tests:** ~6

### UX-3: Engine B tab (Tab 3)
- **File:** `app/api/surfaces.py` (extend)
- **Fragments:**
  - `GET /fragments/research/intake-feed` — recent EventCards with source, credibility, materiality
  - `GET /fragments/research/hypothesis-board` — active hypotheses kanban by stage (extracted → hypothesized → challenged → scored → decided)
  - `GET /fragments/research/review-queue` — hypotheses awaiting operator decision (score ≥70), review cards with 4-button decisions
- **Templates:** `_research_intake_feed.html`, `_research_hypothesis_board.html`, `_research_review_queue.html`
- **Depends on:** P2-7, P2-8
- **Test:** `tests/test_engine_b_fragments.py` — fragments render, review queue shows correct items
- **Est tests:** ~6

### UX-4: Costs tab (Tab 4 — evolve existing)
- **File:** `app/api/surfaces.py` (extend existing cost fragments)
- **New fragments:**
  - `GET /fragments/research/costs-by-service` — LLM costs broken down by service (extraction, hypothesis, challenge, synthesis, journal)
  - `GET /fragments/research/costs-daily-trend` — daily LLM cost trend chart data
- **Templates:** `_research_costs_by_service.html`, `_research_costs_daily_trend.html`
- **Note:** Existing `/fragments/costs/*` endpoints remain — these ADD research-specific cost views
- **Depends on:** P2-1 (model router cost logging)
- **Test:** `tests/test_cost_fragments.py` — cost breakdown renders, daily trend data correct
- **Est tests:** ~4

### UX-5: Decay & Health tab (Tab 5)
- **File:** `app/api/surfaces.py` (extend)
- **Fragments:**
  - `GET /fragments/research/strategy-health-grid` — all active strategies with health status (green/amber/red)
  - `GET /fragments/research/pending-reviews` — unacknowledged decay reviews with action buttons
  - `GET /fragments/research/review-history` — past reviews with outcomes
- **Templates:** `_research_strategy_health_grid.html`, `_research_pending_reviews.html`, `_research_review_history.html`
- **Depends on:** P6-1, P7-1
- **Test:** `tests/test_decay_health_fragments.py` — health grid renders, pending reviews show correctly
- **Est tests:** ~4

### UX-6: Archive tab (Tab 6)
- **File:** `app/api/surfaces.py` (extend)
- **Fragments:**
  - `GET /fragments/research/archive-search` — search retired hypotheses and strategies with filters
  - `GET /fragments/research/retired-strategies` — list of retired strategies with kill reasons
  - `GET /fragments/research/post-mortems` — post-mortem notes with lessons learned
- **Templates:** `_research_archive_search.html`, `_research_retired_strategies.html`, `_research_post_mortems.html`
- **Depends on:** P6-1, EB-3
- **Test:** `tests/test_archive_fragments.py` — archive search works, retired strategies listed
- **Est tests:** ~4

### UX-7: Charting (5 chart types)
- **File:** `app/api/server.py` (add chart endpoints inside `create_app()`)
- **Endpoints:**
  - `GET /api/charts/regime-timeline` — regime state over time (stacked area)
  - `GET /api/charts/signal-history` — signal values over time per instrument (line)
  - `GET /api/charts/portfolio-weights` — portfolio weight evolution (stacked area)
  - `GET /api/charts/cost-trend` — LLM cost trend (bar)
  - `GET /api/charts/decay-metrics` — strategy health metrics over time (line)
- **Note:** Existing `/api/charts/equity-curve` and `/api/charts/market-prices` are NOT duplicated
- **Depends on:** P4-1, EA-1, P2-1, P7-1
- **Test:** `tests/test_research_charts.py` — each chart endpoint returns valid JSON, correct data shape
- **Est tests:** ~5

### UX-8: Artifact chain viewer
- **File:** `app/api/research_actions.py` (extend)
- **Endpoints:**
  - `GET /api/research/artifact-chain/{chain_id}` — returns full chain for slide-over/inline viewer
  - `GET /api/research/artifact/{artifact_id}` — single artifact detail
- **Template:** `app/web/templates/_research_artifact_chain.html` — linked artifact viewer showing EventCard → HypothesisCard → FalsificationMemo → ScoringResult → ExperimentReport → TradeSheet chain
- **HTMX:** Click hypothesis in review queue → load chain viewer inline
- **Depends on:** P1-3
- **Test:** `tests/test_research_actions.py` (extend) — chain endpoint returns ordered artifacts, single artifact retrieval
- **Est tests:** ~4

### UX-9: Operator actions
- **File:** `app/api/research_actions.py` (new router, mount in `create_app()` in server)
- **Endpoints:**
  - `POST /api/research/decide` — body: {chain_id, outcome: promote|revise|park|reject, notes}. Updates pipeline_state, creates audit artifact.
  - `POST /api/research/confirm-kill` — body: {hypothesis_id, notes}. Operator-confirmed kill.
  - `POST /api/research/override-kill` — body: {hypothesis_id, notes}. Override auto-kill recommendation.
  - `POST /api/research/execute-rebalance` — body: {rebalance_id}. Approve and execute Engine A rebalance.
  - `POST /api/research/dismiss-rebalance` — body: {rebalance_id, reason}. Dismiss proposed rebalance.
  - `POST /api/research/acknowledge-review` — body: {chain_id, outcome, notes}. Calls DecayReviewService.acknowledge_review.
- **Mount:** Add router to `create_app()` in server
- **Depends on:** P2-7, P6-1, P7-1, EA-3
- **Test:** `tests/test_research_actions.py` — decide endpoint updates state, kill confirm/override, rebalance execute/dismiss, acknowledge clears block, invalid outcomes rejected
- **Est tests:** ~10

### UX-10: Intel intake extension for Engine B
- **File:** `app/api/surfaces.py` (extend existing intel fragments)
- **Fragments:**
  - `GET /fragments/intel/engine-b-intake` — event intake feed showing recent EventCards with source, credibility, materiality
  - `GET /fragments/intel/engine-b-submit` — manual submit form for operator to push content into Engine B pipeline
- **Templates:**
  - `app/web/templates/_intel_engine_b_intake.html` — event feed
  - `app/web/templates/_intel_engine_b_submit.html` — submit form
- **Integration:** Submit triggers `EngineBPipeline.process_event_async()`, returns job_id
- **Depends on:** P2-7, P2-8
- **Test:** `tests/test_intel_fragments.py` — intake fragment renders, submit triggers pipeline
- **Est tests:** ~4

### UX-11: Top bar KPI additions
- **File:** `app/web/templates/_top_strip.html` (modify existing)
- **Add:** Research pipeline KPIs — active hypothesis count, pending review count, today's LLM spend
- **Data source:** Simple query on `research.pipeline_state` + `research.model_calls`
- **No new poll loop — piggyback on existing page refresh cadence
- **Depends on:** P2-7, P2-1
- **Test:** Manual verification only
- **Est tests:** 0

---

## INTEGRATION & E2E

### INT-1: Signal layer L9_RESEARCH
- **File:** `app/signal/layers/` (add L9 or extend existing)
- **Logic:** Engine B research findings feed into composite scorer via new LayerScore with `layer_id=LayerId.L9_RESEARCH`, source="research-engine-b", provenance_ref pointing to artifact
- **Non-disruptive:** L1-L8 unchanged. L9 adds alongside.
- **Test:** `tests/test_signal_l9.py` — L9 score computed from research artifacts, flows into composite
- **Est tests:** ~4

### INT-2: Wire Engine B to existing intel/event flow
- **Depends on:** P2-7 (Engine B pipeline), P2-8 (intake service)
- **Integration points:**
  - Existing Finnhub webhook → triggers Engine B intake
  - Existing SA quant capture webhook → triggers Engine B intake
  - Existing X/Twitter webhook → triggers Engine B intake
  - `app/api/ideas.py` manual submit → triggers Engine B pipeline (manual lane preserved)
- **File:** Modify `app/api/intel_webhooks.py` + `app/api/ideas.py` to call `EngineBPipeline.process_event_async()` after existing processing
- **Test:** `tests/test_engine_b_integration.py` — webhook triggers pipeline, manual submit triggers pipeline, existing functionality preserved
- **Est tests:** ~6

### INT-4: Council deprecation / cutover migration
- **File:** `research/migration/council_cutover.py`
- **Work:** Add `RESEARCH_SYSTEM_ACTIVE = _env_bool("RESEARCH_SYSTEM_ACTIVE", False)` to config.py. When True: intel webhooks route to Engine B instead of 4-model council. When False: unchanged. One-time migration script for existing idea data.
- **Depends on:** INT-2, P2-7
- **Test:** `tests/test_council_cutover.py` — flag routing both ways, migration idempotent, banner shown
- **Est tests:** ~6

### INT-3: E2E integration test
- **File:** `tests/test_research_e2e.py`
- **Test scenarios:**
  1. Full Engine B flow: raw event → EventCard → HypothesisCard → FalsificationMemo → ScoringResult → decide
  2. Engine B rejection: event → hypothesis rejected by taxonomy → stored with audit
  3. Engine B blocking: event → scored 80 but unresolved objection → outcome = PARK
  4. Engine A daily cycle: market data → regime → signals → portfolio → rebalance
  5. Decay flow: strategy with declining metrics → decay detected → review trigger → promotion blocked → operator ack → resumed
  6. Kill flow: strategy hits invalidation → kill alert → RetirementMemo → archived
  7. Full chain viewer: artifact chain traversal returns correct linked artifacts in order
- **Mocking:** Mock LLM responses (deterministic test fixtures), real PostgreSQL artifact store
- **Est tests:** ~15

---

## SUMMARY

| Track | Tasks | New Files | Est Tests | Est Lines |
|-------|-------|-----------|-----------|-----------|
| Infrastructure | I-1, I-2 | 2 | 6 | 300 |
| Phase 0 (Market Data) | P0-1 to P0-9 | 11 | 65 | 2,700 |
| Phase 1 (Artifacts) | P1-1 to P1-4 | 4 | 59 | 1,400 |
| Phase 2 (Pipeline) | P2-1, P2-1b, P2-2 to P2-8 | 12 | 75 | 2,200 |
| Phase 3 (Taxonomy) | P3-1 | 1 | 15 | 200 |
| Phase 4 (Regime) | P4-1, P4-2 | 3 | 21 | 400 |
| Phase 5 (Cost+Experiment) | P5-1, P5-2 | 2 | 30 | 600 |
| Phase 6 (Kill) | P6-1 | 1 | 12 | 300 |
| Phase 7 (Decay) | P7-1 | 2 | 10 | 300 |
| Engine A | EA-1, EA-1b, EA-2 to EA-6 | 7 | 50 | 1,300 |
| Engine B extras | EB-1 to EB-3 | 4 | 19 | 500 |
| Scheduler | SCHED-1 | 1 | 6 | 200 |
| UX | UX-1 to UX-11 | 22 | 55 | 2,200 |
| Integration | INT-1 to INT-4 | 4 | 31 | 600 |
| **TOTAL** | **~55 tasks** | **~82 files** | **~460 tests** | **~13,600 lines** |

---

## DEPENDENCY GRAPH (build order)

```
I-1 (pg_connection) ──┬──► P0-1..P0-9 (market data) ──┬──► EA-1..EA-4 (Engine A, requires P0 + P4-1)
                      │                                │         │
I-2 (scaffold) ───────┤                                │         ├──► EA-1b (feature cache)
                      │                                │         ├──► EA-5 (Engine A control)
                      │                                │         └──► SCHED-1 (scheduler, requires EA-4 + P6-1 + P7-1)
                      │
                      ├──► P3-1 (taxonomy, needs I-2 only) ──► P2-4 (hypothesis needs P3-1)
                      │
                      ├──► P1-1..P1-4 (artifacts + store + promotion gate)
                      │         │
                      │         ├──► P2-1..P2-1b..P2-8 (challenge pipeline)
                      │         │         │
                      │         │         ├──► P5-1 (cost model) ──► P5-2 (experiment)
                      │         │         │
                      │         │         ├──► P4-1 (regime) ──► P4-2 (journal)
                      │         │         │
                      │         │         ├──► EB-1..EB-3 (expression, synthesis, post-mortem)
                      │         │         │
                      │         │         ├──► EA-6 (Engine B control)
                      │         │         │
                      │         │         ├──► INT-2 (wire webhooks, requires P2-7 + P2-8)
                      │         │         │         │
                      │         │         │         └──► INT-4 (council cutover) ──► INT-3 (E2E tests)
                      │         │         │
                      │         │         └──► UX-1..UX-11 (per-task dependencies below)
                      │         │
                      │         ├──► P6-1 (kill monitor)
                      │         │         │
                      │         │         └──► P7-1 (decay review)
                      │         │
                      │         └──► INT-1 (L9 signal layer)
```

**UX dependency mapping:**
- UX-1 (Dashboard): P2-7, P6-1, P7-1
- UX-2 (Engine A): EA-4, P4-1, P4-2
- UX-3 (Engine B): P2-7, P2-8
- UX-4 (Costs): P2-1
- UX-5 (Decay & Health): P6-1, P7-1
- UX-6 (Archive): P6-1, EB-3
- UX-7 (Charts): P4-1, EA-1, P2-1, P7-1
- UX-8 (Artifact viewer): P1-3
- UX-9 (Operator actions): P2-7, P6-1, P7-1, EA-3
- UX-10 (Intel intake): P2-7, P2-8
- UX-11 (Top bar KPIs): P2-7, P2-1

**Parallelizable:**
- P0 (market data) and P1 (artifacts) can build in parallel after I-1/I-2
- P3-1 (taxonomy) builds right after I-2 — no DB dependency, P2-4 needs it
- P4 (regime), P5 (cost model) can build in parallel after P2
- EA (Engine A) needs P0 + P4-1 (not just P0)
- EB (Engine B extras) can build in parallel with Engine A
- UX tasks can build in parallel with engine work per dependency map above

---

## INSTRUCTIONS FOR CODEX

1. Start with I-1 and I-2. Everything depends on them.
2. Build P1 (artifacts) and P0 (market data) in parallel — no cross-dependency until integration.
3. Build P3-1 (taxonomy) immediately after I-2 — no DB dependency needed, and P2-4 requires it.
4. Build P2 (challenge pipeline) once P1 + P3-1 are done — this is the largest phase.
5. Build P4, P5 in parallel after P2.
6. Build P6 after P1, then P7 after P6.
7. Build EA-1 to EA-4 after P0 + P4-1 are done (not just P0 — regime classifier is required).
8. Build EA-1b (feature cache) after EA-1. Build EA-5 (Engine A control) and EA-6 (Engine B control) after their respective pipelines.
9. Build EB-1 to EB-3 after P2 + P5 are done.
10. Build SCHED-1 after EA-4 + P6-1 + P7-1 are all complete.
11. Build UX tasks per dependency map above — each UX task has specific backend dependencies.
12. Build INT-1 after P1. Build INT-2 after P2-7 + P2-8. Build INT-4 after INT-2. Build INT-3 (E2E) last.
13. Run full test suite after each phase completes. Fix failures before proceeding.
14. **Do not create a new `/research-system` route.** Extend existing `/research` and `/intel` surfaces.
15. **Do not force-migrate SQLite operational tables.** PostgreSQL is for `research` schema only.
16. All LLM calls go through ModelRouter — no direct API calls.
17. All artifacts are immutable — no UPDATE, only INSERT with version chain.
18. Sizing factor floor is 0.5 — enforce `ge=0.5` in RegimeSnapshot Pydantic model.
19. Scoring uses 5-tier thresholds: <60 reject, 60-69 park/revise, 70-79 test, 80-89 experiment, 90+ live pilot (human sign-off required).

**Spec references:**
- Architecture: `ops/RESEARCH_SYSTEM_ARCHITECTURE.md`
- Tech spec: `ops/RESEARCH_SYSTEM_TECH_SPEC.md`
- UX spec: `ops/RESEARCH_SYSTEM_UX_SPEC.md`
- Plan: `ops/RESEARCH_SYSTEM_PLAN_FINAL.md`
- Codex review: `ops/RESEARCH_SYSTEM_REVIEW_codex.md`
- Solo ops followup: `ops/RESEARCH_FOLLOWUP_SOLO_OPS_AND_STRATEGY_MAP.md`
- Data sources followup: `ops/RESEARCH_FOLLOWUP_DATA_SOURCES.md`
