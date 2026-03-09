# Research System Architecture

**Date:** 2026-03-08 | **Status:** Ready for review
**Assumes:** Architecture Plan v2 (P0-P6) complete

---

## 1. System Overview

BoxRoomCapital's research system replaces the current 4-model council vote with a structured, artifact-driven pipeline organized around two independent engines sharing common infrastructure.

```
                    ┌─────────────────────────────┐
                    │     SHARED INFRASTRUCTURE    │
                    │                              │
                    │  Artifact Store (PostgreSQL)  │
                    │  Operational DB (SQLite now,  │
                    │   optional PostgreSQL later)  │
                    │  Model Router                │
                    │  Cost Tracker                │
                    │  Promotion Gate              │
                    │  Kill-Rule Monitor           │
                    │  Decay-Triggered Review      │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┴────────────────┐
              │                                 │
    ┌─────────▼──────────┐          ┌──────────▼──────────┐
    │     ENGINE A       │          │      ENGINE B       │
    │  Futures Trend /   │          │  Equity Event /     │
    │  Carry / Macro     │          │  Revision / NLP     │
    │                    │          │                     │
    │  Deterministic     │          │  LLM-Heavy          │
    │  Low LLM budget    │          │  High LLM budget    │
    │  IBKR execution    │          │  IG + IBKR exec     │
    └────────────────────┘          └─────────────────────┘
```

**Design principle:** The engines share artifact schemas, storage, promotion gates, and monitoring — but have distinct pipeline stages, signal generation logic, and LLM involvement levels. If engines prove better merged later, the shared artifact layer makes consolidation straightforward.

---

## 2. Storage Architecture

### 2.1 Why Not SQLite Alone

SQLite is adequate for BoxRoomCapital's current scale (25-250 MB/year), but has real limitations for the research system:

| Concern | SQLite | PostgreSQL |
|---------|--------|-----------|
| Concurrent writes | Single writer (WAL helps reads) | Full MVCC — multiple writers |
| JSON querying | `json_extract()` — limited | JSONB — indexed, queryable, fast |
| Full-text search | FTS5 extension (basic) | `tsvector` + GIN indexes |
| Connection pooling | Thread-local hacks | Proper pooling (asyncpg, psycopg pool) |
| Transactions | Implicit commit-per-statement in current code | Explicit transaction blocks with isolation levels |
| Dashboard + background jobs | Manageable today, but concurrency headroom is limited | Better long-run fit for heavier concurrent workloads |

PostgreSQL is the better long-run home for artifact-heavy querying and higher-concurrency research workloads. But the P0 runtime work should not be retrospectively treated as "solved by PostgreSQL"; the immediate migration case is strongest for the research artifact store first, with operational-table migration remaining optional until measured load justifies it.

### 2.2 Recommended Split

```
┌────────────────────────────────┐    ┌──────────────────────────────┐
│   ARTIFACT STORE               │    │   OPERATIONAL DB             │
│   (PostgreSQL JSONB)           │    │   (SQLite now / PG later)    │
│                                │    │                              │
│   Research artifacts:          │    │   Existing operational data: │
│   - EventCard                  │    │   - trades, positions        │
│   - HypothesisCard             │    │   - order_intents            │
│   - FalsificationMemo          │    │   - broker_accounts/cash     │
│   - TestSpec                   │    │   - risk_verdicts            │
│   - ExperimentReport           │    │   - strategy_params          │
│   - TradeSheet                 │    │   - nav_snapshots            │
│   - RetirementMemo             │    │   - daily_snapshots          │
│   - RegimeSnapshot             │    │   - jobs                     │
│   - ModelCallLog               │    │   - council_costs            │
│                                │    │   - signal/layer scores      │
│   Characteristics:             │    │                              │
│   - Immutable after creation   │    │   Characteristics:           │
│   - Version-chained            │    │   - Mutable state            │
│   - Full-text searchable       │    │   - ACID transactions        │
│   - Complex nested structure   │    │   - Fast indexed lookups     │
│   - Audit trail required       │    │   - Strict schema            │
└────────────────────────────────┘    └──────────────────────────────┘
```

**Default deployment:**
- PostgreSQL `research` schema — JSONB-heavy artifact tables with GIN indexes
- Existing SQLite operational store remains in place initially
- Optional later PostgreSQL `ops` schema if operational concurrency or query shape outgrows SQLite comfortably

This gives document-store semantics (JSONB querying, nested indexing, full-text search) without the operational burden of running MongoDB separately. The important first move is the artifact store, not a forced big-bang operational DB migration.

**Migration path:** New research artifacts build directly on PostgreSQL. Existing SQLite tables stay in place initially and migrate only if operational load or query needs justify it. A `data/pg_connection.py` module provides the PostgreSQL connection factory alongside the existing SQLite `data/connection.py`.

### 2.3 Market Data Architecture (5-Layer Model)

Based on analysis of public solo-operator stacks (Carver, Alvarez, Davey, Darwinex/Wim), the data architecture is **numeric-first, not text-ingestion-first**. Solo winners build clean price/metadata plumbing before adding textual intelligence. In practice, that means Engine A should wait for the deeper substrate, while Engine B can begin once the minimum point-in-time data contract is trustworthy.

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 5: TEXTUAL / EVENT INTELLIGENCE (Engine B — add after    │
│  minimum numeric substrate exists)                               │
│  EventCards, transcripts, news, analyst revisions               │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 4: RESEARCH / ANALYSIS                                   │
│  Backtesting, walk-forward, Monte Carlo, ranking, screening     │
│  ResearchRun, BacktestRun, ExperimentReport                     │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 3: SNAPSHOT ENGINE                                        │
│  EOD market, intraday signal, term-structure, universe,          │
│  regime, broker/account, execution-quality snapshots             │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 2: CANONICAL MARKET SERIES                                │
│  Equities: adjusted + as-traded, corp actions, universe history  │
│  Futures: contracts, rolls, carry, continuous, FX conversion     │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 1: RAW MARKET DATA (vendor-native, provenance preserved) │
│  OHLCV, bid/ask, ticks — never normalize away vendor semantics   │
│  InstrumentMaster, RawBar, vendor metadata                       │
└─────────────────────────────────────────────────────────────────┘
```

**Critical lesson:** Bar definitions and vendor semantics matter. Different vendors define "close," "volume," and even bar count differently (Alvarez/Quantopian mismatch, Davey's session-definition change, Carver's IB historical vs real-time feed differences). The raw layer preserves vendor provenance; the canonical layer normalizes with version tracking.

**Storage:** All five layers live in the PostgreSQL `research` schema. Layers 1-3 use relational tables (high-volume numeric data with strict schemas). Layers 4-5 use JSONB artifact tables (variable-structure research documents).

```sql
-- research.instruments (Layer 1 — InstrumentMaster)
CREATE TABLE research.instruments (
    instrument_id   SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL,        -- 'equity', 'future', 'fx', 'crypto'
    venue           TEXT NOT NULL,        -- 'CME', 'NYSE', 'LSE', 'IG'
    currency        TEXT NOT NULL,
    session_template TEXT,                -- 'us_equity', 'cme_globex', 'lse', etc.
    multiplier      NUMERIC,
    tick_size       NUMERIC,
    vendor_ids      JSONB DEFAULT '{}',  -- {"ibkr": "265598", "norgate": "AAPL"}
    is_active       BOOLEAN DEFAULT true,
    listing_date    DATE,
    delisting_date  DATE,
    metadata        JSONB DEFAULT '{}',
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, venue, asset_class)
);

-- research.raw_bars (Layer 1 — vendor-native, immutable)
CREATE TABLE research.raw_bars (
    bar_id          BIGSERIAL PRIMARY KEY,
    instrument_id   INTEGER REFERENCES research.instruments(instrument_id),
    vendor          TEXT NOT NULL,        -- 'ibkr', 'norgate', 'barchart'
    bar_timestamp   TIMESTAMPTZ NOT NULL,
    session_code    TEXT,                 -- vendor's session definition
    open            NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
    volume          BIGINT,
    bid             NUMERIC, ask NUMERIC,
    ingestion_ver   INTEGER DEFAULT 1,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_raw_bars_lookup ON research.raw_bars(instrument_id, bar_timestamp);

-- research.canonical_bars (Layer 2 — normalized, versioned)
CREATE TABLE research.canonical_bars (
    bar_id          BIGSERIAL PRIMARY KEY,
    instrument_id   INTEGER REFERENCES research.instruments(instrument_id),
    bar_date        DATE NOT NULL,
    open            NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
    adj_close       NUMERIC,             -- adjusted for splits/dividends
    volume          BIGINT,
    dollar_volume   NUMERIC,
    session_template TEXT NOT NULL,
    data_version    INTEGER DEFAULT 1,   -- incremented on reprocessing
    quality_flags   TEXT[] DEFAULT '{}', -- 'spike_checked', 'session_aligned', etc.
    UNIQUE(instrument_id, bar_date, data_version)
);

-- research.universe_membership (Layer 2 — historical constituents)
CREATE TABLE research.universe_membership (
    instrument_id   INTEGER REFERENCES research.instruments(instrument_id),
    universe        TEXT NOT NULL,        -- 'sp500', 'ftse100', 'nasdaq100'
    from_date       DATE NOT NULL,
    to_date         DATE,                -- NULL = current member
    PRIMARY KEY(instrument_id, universe, from_date)
);

-- research.corporate_actions (Layer 2)
CREATE TABLE research.corporate_actions (
    action_id       SERIAL PRIMARY KEY,
    instrument_id   INTEGER REFERENCES research.instruments(instrument_id),
    action_type     TEXT NOT NULL,        -- 'split', 'dividend', 'spinoff', 'delist'
    ex_date         DATE NOT NULL,
    ratio           NUMERIC,             -- split ratio or dividend amount
    details         JSONB DEFAULT '{}'
);

-- research.futures_contracts (Layer 2 — Carver block)
CREATE TABLE research.futures_contracts (
    contract_id     SERIAL PRIMARY KEY,
    instrument_id   INTEGER REFERENCES research.instruments(instrument_id),
    root_symbol     TEXT NOT NULL,        -- 'ES', 'CL', 'GC'
    expiry_date     DATE NOT NULL,
    contract_code   TEXT NOT NULL,        -- 'ESZ26', 'CLF27'
    roll_date       DATE,
    is_front        BOOLEAN DEFAULT false,
    UNIQUE(root_symbol, expiry_date)
);

CREATE TABLE research.roll_calendar (
    root_symbol     TEXT NOT NULL,
    roll_date       DATE NOT NULL,
    from_contract   TEXT NOT NULL,
    to_contract     TEXT NOT NULL,
    roll_type       TEXT DEFAULT 'standard', -- 'standard', 'volume_triggered'
    PRIMARY KEY(root_symbol, roll_date)
);

-- research.snapshots (Layer 3 — explicit point-in-time state)
CREATE TABLE research.snapshots (
    snapshot_id     BIGSERIAL PRIMARY KEY,
    snapshot_type   TEXT NOT NULL,        -- 'eod_market', 'intraday_signal', 'term_structure',
                                         -- 'universe', 'regime', 'broker_account', 'exec_quality'
    as_of           TIMESTAMPTZ NOT NULL,
    body            JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_snapshots_type_time ON research.snapshots(snapshot_type, as_of DESC);
```

**Futures-native requirement:** Engine A needs contract-level storage first, then derives roll calendars, carry series, continuous prices, and liquidity snapshots. A generic "continuous futures close" is not enough.

**Equities universe requirement:** Engine B needs historical constituent membership, delisted securities, and as-traded pricing. Without "was this stock in the universe on that date?" the backtest is a survivorship-bias generator.

### 2.4 Artifact Store Design

Every research artifact is stored as an immutable versioned document:

```sql
-- research.artifacts (core table)
CREATE TABLE research.artifacts (
    artifact_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_type   TEXT NOT NULL,           -- 'hypothesis_card', 'falsification_memo', etc.
    version         INTEGER NOT NULL DEFAULT 1,
    parent_id       UUID REFERENCES research.artifacts(artifact_id),  -- previous version
    chain_id        UUID NOT NULL,           -- groups all versions of same artifact
    engine          TEXT NOT NULL,            -- 'engine_a' or 'engine_b'
    ticker          TEXT,                     -- primary instrument (nullable for macro)
    edge_family     TEXT,                     -- from approved taxonomy
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft, active, superseded, retired
    body            JSONB NOT NULL,           -- the artifact content
    scores          JSONB,                    -- scoring rubric results
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL,            -- 'system', 'model:claude', 'operator'
    tags            TEXT[] DEFAULT '{}',
    search_text     TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(body->>'summary', '') || ' ' || coalesce(body->>'thesis', ''))
    ) STORED
);

CREATE INDEX idx_artifacts_type ON research.artifacts(artifact_type);
CREATE INDEX idx_artifacts_chain ON research.artifacts(chain_id);
CREATE INDEX idx_artifacts_engine ON research.artifacts(engine);
CREATE INDEX idx_artifacts_ticker ON research.artifacts(ticker);
CREATE INDEX idx_artifacts_edge ON research.artifacts(edge_family);
CREATE INDEX idx_artifacts_status ON research.artifacts(status);
CREATE INDEX idx_artifacts_body ON research.artifacts USING GIN(body);
CREATE INDEX idx_artifacts_search ON research.artifacts USING GIN(search_text);
```

**Immutability rule:** Artifacts are never updated. New versions create a new row with `parent_id` pointing to the previous version and `version` incremented. The `chain_id` groups all versions. Previous versions get `status='superseded'`.

### 2.4 Model Call Logging

Every LLM invocation is logged for cost tracking, debugging, and audit:

```sql
CREATE TABLE research.model_calls (
    call_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id     UUID REFERENCES research.artifacts(artifact_id),
    service         TEXT NOT NULL,        -- 'signal_extraction', 'hypothesis_formation', etc.
    engine          TEXT NOT NULL,
    model_provider  TEXT NOT NULL,        -- 'anthropic', 'openai', 'xai', 'google'
    model_id        TEXT NOT NULL,        -- 'claude-opus-4-6', 'gpt-5.4', etc.
    prompt_hash     TEXT NOT NULL,        -- SHA256 of full prompt (for dedup/audit)
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        NUMERIC(10,6) NOT NULL,
    latency_ms      INTEGER NOT NULL,
    success         BOOLEAN NOT NULL,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 3. Model Router

All LLM calls go through a single `ModelRouter` that handles provider selection, configuration, retry, and cost logging.

```
┌──────────────────────────────────────────────────────┐
│                    MODEL ROUTER                       │
│                                                      │
│  Config: service → (provider, model_id, params)      │
│                                                      │
│  ┌─────────┐  ┌─────────┐  ┌─────┐  ┌───────┐      │
│  │Anthropic│  │ OpenAI  │  │ xAI │  │Google │      │
│  │ Client  │  │ Client  │  │     │  │       │      │
│  └─────────┘  └─────────┘  └─────┘  └───────┘      │
│                                                      │
│  Features:                                           │
│  - Configurable model per service (config.py)        │
│  - Automatic retry with exponential backoff          │
│  - Cost logging to research.model_calls              │
│  - Timeout enforcement                               │
│  - Graceful degradation (fallback model if primary   │
│    fails after retries)                              │
│  - Prompt versioning (hash tracked per call)          │
└──────────────────────────────────────────────────────┘
```

**Configuration pattern** (in `config.py`):

```python
RESEARCH_MODEL_CONFIG = {
    # Engine B services (LLM-heavy)
    "signal_extraction":      {"provider": "anthropic", "model": "claude-opus-4-6", "thinking": True},
    "hypothesis_formation":   {"provider": "xai",      "model": "grok-3"},
    "challenge_falsification":{"provider": "anthropic", "model": "claude-opus-4-6", "thinking": True},
    "regime_context":         {"provider": "google",    "model": "gemini-2.5-pro"},
    "bounded_synthesis":      {"provider": "openai",    "model": "gpt-5.4"},

    # Engine A services (light LLM)
    "regime_journal":         {"provider": "google",    "model": "gemini-2.5-pro"},
    "post_mortem":            {"provider": "anthropic", "model": "claude-sonnet-4-6"},

    # Shared
    "cost_estimate":          {"provider": "openai",    "model": "gpt-5.4"},
}
```

The existing `intelligence/ai_panel/` clients are refactored into the Model Router. Same underlying HTTP calls, unified interface.

**Independence rule:** formation and challenge must use separate service configs, prompts, and call lineage. Different providers are preferred, but not mandatory; a same-provider setup is acceptable if it uses distinct model/config families and is benchmarked for disagreement quality rather than superficial vendor diversity.

---

## 4. Engine A: Futures Trend / Carry / Macro

### 4.1 Philosophy

Engine A is a **deterministic, rule-based system** with minimal LLM involvement. It implements the Carver/AHL miniaturization pattern: systematic trend-following and carry across liquid futures, with regime conditioning.

LLMs assist with: regime state journaling, post-mortem analysis, exposure summaries, and monitoring alerts. LLMs do NOT generate signals or make allocation decisions.

### 4.2 Pipeline

```
┌──────────────┐     ┌───────────────┐     ┌──────────────────┐
│  DATA INGEST │────▶│  SIGNAL GEN   │────▶│  POSITION SIZING │
│              │     │               │     │                  │
│ Price bars   │     │ Trend score   │     │ Vol-target       │
│ Yield curves │     │ Carry score   │     │ Risk parity      │
│ Macro series │     │ Value score   │     │ Regime factor    │
│ Vol surfaces │     │ Momentum      │     │ Correlation adj  │
└──────────────┘     └───────┬───────┘     └────────┬─────────┘
                             │                      │
                    ┌────────▼──────────┐   ┌───────▼──────────┐
                    │ REGIME CLASSIFIER │   │ PORTFOLIO CONST  │
                    │                   │   │                  │
                    │ Risk-on/off/      │   │ Target weights   │
                    │ transition        │   │ Rebalance trades │
                    │ Vol regime        │   │ Cost filter      │
                    │ Trend regime      │   │                  │
                    └───────────────────┘   └────────┬─────────┘
                                                     │
                                            ┌────────▼─────────┐
                                            │  EXECUTION       │
                                            │                  │
                                            │  OrderIntents    │
                                            │  via IBKR API    │
                                            │  Micro futures   │
                                            └──────────────────┘
```

### 4.3 Signal Generation (No LLM)

Each instrument gets independent scores from deterministic rules:

| Signal | Method | Lookback | Update |
|--------|--------|----------|--------|
| Trend | EWMA crossover (8/16/32/64 day) blended | 64 days | Daily |
| Carry | Front-month vs deferred yield, annualized | Current term structure | Daily |
| Value | Z-score of real yield / price-to-fair-value | 5-year rolling | Weekly |
| Momentum | 12-month return minus last month | 12 months | Daily |
| Vol regime | Realized vol vs long-run median | 252 days | Daily |

Combined into a single forecast per instrument, then volatility-targeted and risk-parity weighted.

### 4.4 Regime Conditioning

The `RegimeClassifier` (deterministic) produces a `RegimeSnapshot` artifact:

```python
@dataclass
class RegimeSnapshot:
    as_of: str
    vol_regime: str          # "low", "normal", "high", "crisis"
    trend_regime: str        # "strong_trend", "choppy", "reversal"
    carry_regime: str        # "steep", "flat", "inverted"
    macro_regime: str        # "risk_on", "transition", "risk_off"
    sizing_factor: float     # 0.0-1.0, applied to position sizes
    active_overrides: list   # e.g., ["reduce_trend_weight", "increase_carry"]
```

This is persisted as an artifact and conditions both position sizing and signal weight adjustments.

**LLM regime journal** (optional, light): After each regime change, the model router sends the regime transition to a configured model for a 200-word journal entry explaining the shift. Stored as a `RegimeJournal` artifact linked to the `RegimeSnapshot`. Purely for operator review — never feeds back into signals.

### 4.5 Artifact Flow

```
RegimeSnapshot (daily, deterministic)
    │
    ├──▶ RegimeJournal (optional LLM annotation)
    │
    ▼
EngineASignalSet (daily, deterministic)
    │   - Per-instrument: trend, carry, value, momentum scores
    │   - Combined forecast
    │   - Target position
    │
    ▼
RebalanceSheet (when target != current, passes cost filter)
    │   - Instruments to trade
    │   - Target quantities
    │   - Cost estimate
    │   - Risk impact
    │
    ▼
TradeSheet (after promotion gate approval)
    │   - OrderIntents submitted
    │   - Execution plan
    │
    ▼
ExecutionReport (post-fill)
    │   - Fill prices, slippage
    │   - Cost vs estimate
    │
    ▼
PostMortemNote (periodic LLM review of execution quality)
```

### 4.6 Universe

**MVP universe** (CME micro/mini + IBKR accessible):

| Asset Class | Instruments | Contract |
|-------------|------------|----------|
| Equity indices | ES, NQ, RTY, FESX, NKD | Micro futures |
| Rates | ZN, ZF, ZB | Mini/standard |
| Commodities | GC, SI, CL, NG | Micro futures |
| FX | 6E, 6B, 6J, 6A | Standard futures |
| Crypto (Phase 3) | BTC, ETH | CME micro futures |

---

## 5. Engine B: Equity Event / Revision / Transcript

### 5.1 Philosophy

Engine B is the **LLM-heavy research pipeline** where language intelligence genuinely adds value. The bottleneck is textual interpretation — earnings call parsing, guidance-change extraction, analyst revision mapping, and expectation modeling.

The pipeline follows the artifact flow from `RESEARCH_SYSTEM_PLAN_FINAL.md`: intake → signal extraction → hypothesis formation → challenge/falsification → experiment → promotion.

### 5.2 Pipeline

```
┌──────────────────────┐
│  SOURCE INTAKE        │
│                       │
│  Earnings releases    │
│  Transcripts          │
│  Analyst revisions    │
│  News wires           │
│  SA quant data        │
│  X/Twitter (curated)  │
└──────────┬────────────┘
           │
    ┌──────▼──────────────┐
    │ SIGNAL EXTRACTION   │  ◄── LLM: "What changed, for whom, vs what expectation?"
    │                     │
    │ → EventCard         │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ TAXONOMY GATE       │  ◄── Deterministic: must map to approved edge family
    │                     │
    │ Reject if no fit    │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ HYPOTHESIS          │  ◄── LLM: generate HypothesisCard within taxonomy
    │ FORMATION           │
    │                     │
    │ → HypothesisCard    │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ CHALLENGE &         │  ◄── LLM: cheapest alternative explanation,
    │ FALSIFICATION       │      beta leakage, crowding, prior evidence
    │                     │
    │ → FalsificationMemo │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ SCORING RUBRIC      │  ◄── Deterministic: 100-point rubric with penalties
    │                     │
    │ < 60 → reject       │
    │ 60-69 → park/revise │
    │ 70-79 → test        │
    │ 80+ → experiment    │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ EXPERIMENT          │  ◄── Deterministic: registered TestSpec,
    │ REGISTRY & TEST     │      frozen-before-backtest, cost-aware
    │                     │
    │ → TestSpec           │
    │ → ExperimentReport  │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ REGIME CHECK        │  ◄── Deterministic: is current regime compatible?
    │                     │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ EXPRESSION          │  ◄── LLM-assisted: instrument selection,
    │ SELECTION           │      sizing, hedge plan
    │                     │
    │ → TradeSheet        │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ PROMOTION GATE      │  ◄── Existing: shadow → staged → live
    │ (human sign-off     │
    │  for live)          │
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │ LIVE MONITORING     │  ◄── Deterministic + LLM post-mortems
    │ & KILL RULES        │
    │                     │
    │ → RetirementMemo    │
    │   (when killed)     │
    └─────────────────────┘
```

### 5.3 Source Intake & Signal Extraction

**Sources** (ranked by credibility):

| Tier | Source | Credibility Score | Intake Method |
|------|--------|-------------------|---------------|
| 1 | SEC filings (8-K, 10-Q) | 0.95 | API / webhook |
| 2 | Earnings transcripts | 0.90 | API (FinancialModelingPrep, Alpha Vantage) |
| 3 | Analyst revisions (consensus changes) | 0.85 | Data feed |
| 4 | Major news wires (Reuters, Bloomberg) | 0.80 | Finnhub API |
| 5 | SA Quant ratings | 0.75 | Chrome extension capture |
| 6 | Curated X accounts | 0.50 | Webhook / manual |
| 7 | General social media | 0.20 | Attention radar only — never signal |

**Signal Extraction** (LLM service):

The configured model receives raw source content and produces a structured `EventCard`:

```
Input:  Raw text + source metadata + credibility tier
Output: EventCard artifact with:
        - What changed (fact extraction)
        - For whom (affected instruments)
        - Vs what expectation (market-implied prior)
        - Materiality assessment
        - Time sensitivity
```

### 5.4 Hypothesis Formation & Challenge

**Formation** (LLM, constrained):

The model receives the `EventCard` + current `RegimeSnapshot` + the approved edge taxonomy, and must produce a `HypothesisCard` that:
- Declares exactly one edge family from the taxonomy
- States the market-implied view it disagrees with
- Identifies specific invalidation criteria
- Proposes a testable prediction with horizon

**Challenge** (LLM, different model than formation):

A DIFFERENT model (enforced by config) receives the `HypothesisCard` and must:
- Find the cheapest alternative explanation
- Check for beta leakage (is this just market exposure?)
- Check for crowding (is everyone else seeing this?)
- Retrieve prior evidence for/against
- Flag unresolved objections

**Critical rule:** The challenge model CANNOT smooth away objections. Unresolved objections persist in the `FalsificationMemo` and are visible to the operator. One unresolved critical objection blocks promotion regardless of score.

### 5.5 Scoring & Experiment

**100-point scoring rubric** (deterministic, computed from artifact fields):

| Dimension | Max Points | Source |
|-----------|-----------|--------|
| Source integrity | 10 | EventCard credibility tier |
| Mechanism clarity | 15 | HypothesisCard edge family + mechanism |
| Prior empirical support | 15 | FalsificationMemo prior evidence |
| Incremental information advantage | 10 | FalsificationMemo crowding check |
| Regime fit | 10 | RegimeSnapshot compatibility |
| Point-in-time testability | 10 | TestSpec data availability |
| Implementation realism / costs / capacity | 15 | Cost model estimate |
| Portfolio fit | 10 | Correlation with existing positions |
| Monitoring / kill clarity | 5 | HypothesisCard invalidation criteria |

**Penalties:** Search-space complexity (-15 max), crowding (-10 max), data fragility (-10 max).

**Thresholds:** <60 reject, 60-69 revise/park, 70-79 eligible for registered test, 80-89 paper/micro pilot, 90+ live pilot with human sign-off.

**Experiment registration:**
Before any backtest, a `TestSpec` artifact is frozen:
- Point-in-time datasets declared
- Train/val/test splits locked
- Search budget capped (max variants)
- Baselines declared
- Cost model specified
- Eval metrics chosen

This prevents the "backtest until you find something" failure mode.

### 5.6 Universe

**MVP universe** (large-cap, liquid, accessible via IG spread bets + IBKR ISA):

| Market | Instruments | Execution |
|--------|-------------|-----------|
| US large-cap | S&P 500 constituents (top 100 by liquidity) | IG spread bet + IBKR ISA |
| UK large-cap | FTSE 100 constituents (top 50) | IG spread bet |
| Sector ETFs | XLK, XLF, XLE, XLV, XLI, etc. | IBKR ISA |

---

## 6. Shared Services

### 6.1 Deterministic Services

| Service | Module | Engine | Responsibility |
|---------|--------|--------|---------------|
| **Intake & Normalization** | `research/intake.py` | B (A uses data feeds) | Dedup, timestamp, source classification, entity-to-instrument mapping |
| **Source Reliability** | `research/source_scoring.py` | B | Credibility score by source tier, corroboration tracking |
| **Taxonomy Enforcement** | `research/taxonomy.py` | Both | Reject hypotheses that don't map to approved edge family |
| **Experiment Registry** | `research/experiment.py` | Both | Freeze TestSpec before backtest, cap search budget |
| **Promotion Gate** | `fund/promotion_gate.py` (existing) | Both | shadow → staged_live → live with 4-state outcomes |
| **Cost Model** | `research/cost_model.py` | Both | IG spread/funding/slippage, futures commission/roll, asset-class templates |
| **Kill-Rule Monitor** | `research/kill_monitor.py` | Both | Track invalidators per strategy, auto-pause within preauthorized limits |
| **Decay-Triggered Review** | `research/decay_review.py` | Both | Wire `analytics/decay_detector.py` into promotion gate review triggers |
| **Scoring Engine** | `research/scorer.py` | B (A uses numeric rules) | 100-point rubric computation from artifact fields |

### 6.2 LLM-Assisted Services

| Service | Module | Engine | LLM Role | Budget |
|---------|--------|--------|----------|--------|
| **Signal Extraction** | `research/signal_extraction.py` | B | Convert events → structured EventCards | High |
| **Hypothesis Formation** | `research/hypothesis.py` | B | Generate HypothesisCards within taxonomy | High |
| **Challenge & Falsification** | `research/challenge.py` | B | Find cheapest alternative, check crowding/leakage | High |
| **Regime Context** | `research/regime.py` | Both | Maintain state vector, condition hypotheses | Medium |
| **Bounded Synthesis** | `research/synthesis.py` | B | Summarize artifacts for human review | Medium |
| **Regime Journal** | `research/regime.py` | A | Annotate regime transitions | Low |
| **Post-Mortem** | `research/post_mortem.py` | Both | Analyze execution quality and strategy health | Low |

### 6.3 Promotion Gate Enhancement

The existing `fund/promotion_gate.py` is extended with:

1. **4-state outcomes:** `promote`, `revise`, `park`, `reject` (currently binary pass/fail)
2. **Artifact linkage:** Each promotion decision references the chain of artifacts that justified it
3. **Decay-triggered review:** When `analytics/decay_detector.py` flags a strategy as `warning` or `decay`, the promotion gate automatically:
   - Blocks further scaling
   - Creates a `ReviewTrigger` artifact
   - Notifies operator via Telegram
   - Requires explicit operator acknowledgement before resuming
4. **Human sign-off gate:** Live promotion requires operator approval (not just metric thresholds)

---

## 7. Module Structure

```
research/                          # NEW top-level package
├── __init__.py
├── artifacts.py                   # Artifact dataclasses (all types)
├── artifact_store.py              # PostgreSQL JSONB persistence
├── model_router.py                # Configurable LLM routing + cost logging
├── taxonomy.py                    # Edge family enum + enforcement
├── scorer.py                      # 100-point rubric engine
│
├── market_data/                   # Numeric data layers (1-3)
│   ├── __init__.py
│   ├── instruments.py             # InstrumentMaster CRUD + vendor ID mapping
│   ├── raw_bars.py                # Vendor-native bar ingestion + provenance
│   ├── canonical_bars.py          # Normalized bars + quality checks + versioning
│   ├── corporate_actions.py       # Splits, dividends, delistings
│   ├── universe.py                # Historical constituent membership
│   ├── futures.py                 # Contract storage, roll calendar, multiple prices, continuous series
│   ├── liquidity.py               # Spread-cost series, commission tracking
│   ├── snapshots.py               # EOD, intraday, term-structure, regime, broker snapshots
│   └── ingestion.py               # Vendor adapters (IBKR, Norgate, Barchart)
│
├── engine_a/                      # Futures trend/carry/macro
│   ├── __init__.py
│   ├── signals.py                 # Trend, carry, value, momentum scorers
│   ├── regime.py                  # Deterministic regime classifier
│   ├── portfolio.py               # Vol-target, risk-parity construction
│   ├── rebalancer.py              # Cost-filtered rebalance generation
│   └── pipeline.py                # Engine A orchestration (daily cycle)
│
├── engine_b/                      # Equity event/revision/NLP
│   ├── __init__.py
│   ├── intake.py                  # Source normalization + dedup
│   ├── source_scoring.py          # Credibility tiers
│   ├── signal_extraction.py       # LLM: raw → EventCard
│   ├── hypothesis.py              # LLM: EventCard → HypothesisCard
│   ├── challenge.py               # LLM: HypothesisCard → FalsificationMemo
│   ├── experiment.py              # TestSpec registration + backtest execution
│   ├── expression.py              # Instrument selection + TradeSheet
│   └── pipeline.py                # Engine B orchestration (event-driven)
│
├── shared/                        # Cross-engine services
│   ├── __init__.py
│   ├── cost_model.py              # IG/futures/crypto cost templates
│   ├── kill_monitor.py            # Invalidator tracking + auto-pause
│   ├── decay_review.py            # Decay detector → promotion gate wiring
│   ├── synthesis.py               # Bounded LLM summarization
│   └── post_mortem.py             # Execution quality LLM review
│
└── prompts/                       # Versioned prompt templates
    ├── __init__.py
    ├── v1_signal_extraction.py
    ├── v1_hypothesis.py
    ├── v1_challenge.py
    ├── v1_regime_journal.py
    ├── v1_synthesis.py
    └── v1_post_mortem.py
```

---

## 8. Data Flow: End-to-End Example

### Engine B: Earnings Event → Live Trade

```
1. INTAKE
   Earnings transcript for AAPL lands via Finnhub webhook
   → Normalized, timestamped, source_class="transcript", credibility=0.90

2. SIGNAL EXTRACTION (LLM: Claude Opus)
   "AAPL guided FY revenue +8% vs consensus +5%, margin expansion on services"
   → EventCard artifact stored (immutable)

3. TAXONOMY GATE (deterministic)
   Edge family = "underreaction_revision" ✓ (guidance surprise → PEAD variant)

4. HYPOTHESIS FORMATION (LLM: Grok)
   → HypothesisCard: "AAPL post-guidance drift, 3-5 day horizon,
      target 2-3% move, invalidated if market sells off >2% same session"

5. CHALLENGE (LLM: Claude Opus — different model enforced)
   → FalsificationMemo:
      - Cheapest alternative: "Already priced in — AAPL up 4% after-hours"
      - Beta leakage: "Low — guidance is company-specific"
      - Crowding: "Medium — high-profile name, many algos watch earnings"
      - Unresolved: "After-hours move may have captured most of the drift"

6. SCORING (deterministic)
   Source: 9/10, Mechanism: 13/15, Prior support: 12/15, Info advantage: 5/10,
   Regime fit: 8/10, Testability: 9/10, Implementation: 12/15, Portfolio: 8/10,
   Kill clarity: 4/5 = 80/100
   Penalties: crowding -5 = 75/100
   → Eligible for registered testing

7. EXPERIMENT (deterministic)
   TestSpec frozen: AAPL daily bars, 5-year PEAD backtest,
   entry = close of earnings day, exit = close +5 days,
   baseline = buy-and-hold, cost model = IG spread bet 0.1% round-trip

8. BACKTEST (deterministic)
   → ExperimentReport: Sharpe 0.8, profit factor 1.4, win rate 58%,
      net of costs, 180 events tested

9. REGIME CHECK (deterministic)
   Current regime: risk_on, vol: normal → compatible ✓

10. EXPRESSION (LLM-assisted)
    → TradeSheet: IG spread bet AAPL, £2/point, 5-day hold,
       stop at entry -2%, kill if VIX > 30

11. PROMOTION GATE
    Shadow run → 2 weeks soak → staged_live → operator sign-off → live

12. MONITORING
    Kill rules checked daily: VIX level, thesis invalidation, P&L stop
    Decay detector runs weekly: win rate, profit factor, consecutive losses
```

---

## 9. Integration with Existing System

### 9.1 What Gets Replaced

| Current | Replaced By | Migration |
|---------|------------|-----------|
| 4-model council vote (`intel_pipeline.py`) | Engine B structured pipeline | Phase 2 build |
| Free-form idea generation | Taxonomy-constrained HypothesisCards | Phase 3 build |
| `IntelAnalysis` dataclass | `EventCard` + `HypothesisCard` artifacts | Phase 1 build |
| Binary pass/fail promotion | 4-state promote/revise/park/reject | Phase 1 build |
| `idea_research.py` 4-step pipeline | Engine B full pipeline | Phase 2 build |
| `ROUND1_PROMPT` / `ROUND2_PROMPT` | Versioned prompt templates per service | Phase 2 build |

### 9.2 What Stays

| Component | Reason |
|-----------|--------|
| Signal layers L1-L8 | Stable, well-tested — Engine B adds research-derived signals alongside |
| Composite scorer | Stays for L1-L8 numeric scoring; new rubric is separate |
| Promotion gate state machine | Extended, not replaced |
| Strategy slots + orchestrator | Engine A/B output OrderIntents into existing pipeline |
| Broker adapters (IG, IBKR, Kraken) | Unchanged |
| Feature store | Reused for Engine A time-series features |
| Decay detector | Wired into new decay-triggered review |
| Cost tracking (`council_costs`) | Migrated to `research.model_calls` |
| `app/api/ideas.py` manual submit lane | Stays during migration so the operator can seed Engine B intentionally while automated intake ramps |

### 9.3 Signal Layer Integration

Engine B research can feed the existing signal system via a new layer:

```python
# New layer: L9_RESEARCH (or override L1 PEAD with richer research signal)
LayerScore(
    layer_id=LayerId.L9_RESEARCH,
    ticker="AAPL",
    score=75.0,       # From research scoring rubric
    as_of="2026-03-08T16:00:00Z",
    source="research-engine-b",
    provenance_ref="artifact:hypothesis_card:uuid",
    confidence=0.85,
    details={"edge_family": "underreaction_revision", "rubric_score": 75, ...}
)
```

This lets research findings flow into the existing composite scorer without disrupting the L1-L8 architecture.

### 9.4 Landing Order

To keep migration realistic for a one-person operation:

1. Land the artifact spine and Engine B council replacement on the existing `/research` and `/intel` surfaces first.
2. Evolve `/research` into the mature research-system surface only after the artifact workflow is stable and operator-friendly.
3. Build out Engine A futures surfaces once the market-data layer is proven in live diagnostics.

**Engine B start condition:** daily OHLCV, basic corporate actions, and the current S&P 500 constituent list are sufficient to start event/revision scoring. Full historical universe membership improves research quality later, but does not block the initial council replacement.

---

## 10. Governance

| Rule | Enforcement |
|------|-------------|
| Human sign-off before live promotion | Promotion gate requires `actor != 'system'` for live transitions |
| Locked TestSpec before backtest | `ExperimentRegistry` rejects backtest if TestSpec not frozen |
| Immutable artifacts | PostgreSQL artifact store — no UPDATE, only INSERT with version chain |
| No same-context generate + challenge | `ModelRouter` enforces separate service configs/prompt lineage; different provider or model family preferred |
| Kill rules per strategy | `KillMonitor` checks declared invalidators daily |
| Decay triggers review, not just alerts | `DecayReview` creates ReviewTrigger artifact + blocks scaling |
| Material prompt change = revalidation | Prompt hash tracked per model call; hash change flags affected artifacts |
| Cost tracking | Every LLM call logged with tokens, cost, latency |

---

## 11. Non-Goals (Explicit Exclusions)

- **No ORM** — raw SQL with helper functions
- **No broad async conversion** — sync-in-threadpool for LLM calls
- **No signal layer rework** — L1-L8 stays, research adds alongside
- **No broker adapter rework** — current pattern is correct
- **No dependency injection framework** — keep it simple
- **No autonomous capital allocation** — human approves all live promotions
- **No fully autonomous LLM PM** — LLMs are research amplifiers, not decision-makers

---

## Source Documents

| File | Role |
|------|------|
| `ops/RESEARCH_SYSTEM_PLAN_FINAL.md` | Consensus principles, MVP scope, strategy roadmap |
| `ops/RESEARCH_REPORT.md` | Original commissioned report |
| `ops/RESEARCH_REPORT_REVIEW.md` | Claude's migration path assessment |
| `ops/RESEARCH_REPORT_REVIEW_codex.md` | Codex's deterministic-first critique |
| `ops/RESEARCH_FOLLOWUP_SOLO_OPS_AND_STRATEGY_MAP.md` | Solo operator evidence + strategy feasibility |
| `ops/RESEARCH_FOLLOWUP_DATA_SOURCES.md` | Solo operator data sources, numeric infrastructure, 5-layer data model |
| `ops/ARCHITECTURE_PLAN_v2.md` | Infrastructure prerequisites (P0-P6) |
