# Research System Technical Specification

**Date:** 2026-03-08 | **Status:** Ready for review
**Companion to:** `ops/RESEARCH_SYSTEM_ARCHITECTURE.md`
**Assumes:** Architecture Plan v2 (P0-P6) complete

---

## Table of Contents

0. [Phase 0: Market Data Infrastructure](#phase-0)
1. [Phase 1: Artifact Schemas & Storage](#phase-1)
2. [Phase 2: Structured Challenge Pipeline (Replace Council)](#phase-2)
3. [Phase 3: Edge Taxonomy Enforcement](#phase-3)
4. [Phase 4: Regime/State Context Service](#phase-4)
5. [Phase 5: Cost Model Integration](#phase-5)
6. [Phase 6: Retirement/Kill Formalization](#phase-6)
7. [Phase 7: Decay-Triggered Review](#phase-7)
8. [Engine A: Futures Pipeline](#engine-a)
9. [Engine B: Equity Event Pipeline](#engine-b)
10. [Database Migration Plan](#db-migration)
11. [Testing Strategy](#testing)

---

<a id="phase-0"></a>
## Phase 0: Market Data Infrastructure

**Goal:** Build the numeric-first data foundation (Layers 1-3) needed for Engine A and long-lived research durability. Solo operator evidence (Carver, Alvarez, Davey) shows that clean price/metadata plumbing is the true substrate.

**Depends on:** P3 (data layer consolidation) + PostgreSQL available.

This phase starts first, but it should not unnecessarily block the artifact spine or the Engine B council replacement. For the event/revision engine, begin with current point-in-time-capable datasets and close the most important data gaps in parallel.

**Minimum Engine B start dataset:**
- daily OHLCV
- basic corporate actions
- current S&P 500 constituent list

That is enough to begin event/revision scoring. Full historical universe membership is still required later for stronger bias control, but it should not block the initial migration off the council vote.

### 0.1 InstrumentMaster

```python
# research/market_data/instruments.py

from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class InstrumentMaster(BaseModel):
    """Central instrument registry with vendor provenance."""
    instrument_id: Optional[int] = None
    symbol: str
    asset_class: str                      # 'equity', 'future', 'fx', 'crypto'
    venue: str                            # 'CME', 'NYSE', 'LSE', 'IG'
    currency: str                         # 'USD', 'GBP', 'EUR'
    session_template: Optional[str] = None  # 'us_equity', 'cme_globex', 'lse'
    multiplier: Optional[float] = None    # futures contract multiplier
    tick_size: Optional[float] = None
    vendor_ids: dict[str, str] = Field(default_factory=dict)  # {"ibkr": "265598", "norgate": "AAPL"}
    is_active: bool = True
    listing_date: Optional[date] = None
    delisting_date: Optional[date] = None
    metadata: dict = Field(default_factory=dict)
```

### 0.2 RawBar (Vendor-Native, Immutable)

```python
# research/market_data/raw_bars.py

class RawBar(BaseModel):
    """Vendor-native bar — never modified after ingestion.

    Critical lesson: "close" is not universal. IB historical data
    differs from real-time feed; Quantopian's close came from last
    trade not exchange auction; session-definition changes rewrite
    bar history (Davey crude-oil example).
    """
    bar_id: Optional[int] = None
    instrument_id: int
    vendor: str                           # 'ibkr', 'norgate', 'barchart'
    bar_timestamp: str                    # vendor-native timestamp
    session_code: Optional[str] = None    # vendor's session definition
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    ingestion_ver: int = 1
```

### 0.3 CanonicalBar (Normalized, Versioned)

```python
# research/market_data/canonical_bars.py

class CanonicalBar(BaseModel):
    """Normalized bar after session rules, adjustments, quality checks.

    Version this. Never overwrite silently. Increment data_version
    when reprocessing (e.g., after session-template correction or
    corporate action recalculation).
    """
    bar_id: Optional[int] = None
    instrument_id: int
    bar_date: date
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adj_close: Optional[float] = None     # adjusted for splits/dividends
    volume: Optional[int] = None
    dollar_volume: Optional[float] = None
    session_template: str
    data_version: int = 1
    quality_flags: list[str] = Field(default_factory=list)  # 'spike_checked', 'session_aligned'
```

### 0.4 Corporate Actions & Universe Membership

```python
# research/market_data/corporate_actions.py

class CorporateAction(BaseModel):
    """Splits, dividends, delistings — mandatory for equities.
    Without delisted-stock histories and as-traded pricing,
    backtest results are survivorship-bias generators (Alvarez).
    """
    action_id: Optional[int] = None
    instrument_id: int
    action_type: str                      # 'split', 'dividend', 'spinoff', 'delist'
    ex_date: date
    ratio: Optional[float] = None        # split ratio or dividend amount
    details: dict = Field(default_factory=dict)


# research/market_data/universe.py

class UniverseMembership(BaseModel):
    """Historical index/universe constituent membership.
    Answers: 'Was this stock in the S&P 500 on that date?'
    """
    instrument_id: int
    universe: str                         # 'sp500', 'ftse100', 'nasdaq100'
    from_date: date
    to_date: Optional[date] = None       # NULL = current member
```

### 0.5 Futures Block (Carver Pattern)

```python
# research/market_data/futures.py

class FuturesContract(BaseModel):
    """Individual futures contract — the atomic unit of futures data.
    Without contract-level storage, futures Engine A is a toy.
    """
    contract_id: Optional[int] = None
    instrument_id: int
    root_symbol: str                      # 'ES', 'CL', 'GC'
    expiry_date: date
    contract_code: str                    # 'ESZ26', 'CLF27'
    roll_date: Optional[date] = None
    is_front: bool = False


class RollCalendarEntry(BaseModel):
    """When to roll from one contract to the next."""
    root_symbol: str
    roll_date: date
    from_contract: str
    to_contract: str
    roll_type: str = "standard"           # 'standard', 'volume_triggered'


class MultiplePrices(BaseModel):
    """Carver's 'multiple prices' frame — current/next/carry for a date.
    Carry = price difference between contracts that captures term structure.
    """
    root_symbol: str
    price_date: date
    current_contract: str
    current_price: float
    next_contract: Optional[str] = None
    next_price: Optional[float] = None
    carry_contract: Optional[str] = None
    carry_price: Optional[float] = None


class ContinuousSeries(BaseModel):
    """Back-adjusted continuous price series for backtesting."""
    root_symbol: str
    bar_date: date
    price: float
    adjustment_method: str = "panama"     # 'panama', 'ratio', 'none'
    data_version: int = 1
```

### 0.6 Liquidity & Cost Series

```python
# research/market_data/liquidity.py

class LiquidityCostEntry(BaseModel):
    """Spread-cost and commission data per instrument per date.
    Both Carver and Davey lean hard on cost realism.
    """
    instrument_id: int
    as_of: date
    inside_spread: Optional[float] = None     # in price terms
    spread_cost_bps: Optional[float] = None   # in basis points
    commission_per_unit: Optional[float] = None
    funding_rate: Optional[float] = None      # for CFDs/spread bets
    borrow_cost: Optional[float] = None       # for shorts
```

### 0.7 Snapshot Engine

```python
# research/market_data/snapshots.py

from enum import Enum

class SnapshotType(str, Enum):
    EOD_MARKET = "eod_market"             # End-of-day prices + indicators
    INTRADAY_SIGNAL = "intraday_signal"   # Hourly signal state (Carver-style)
    TERM_STRUCTURE = "term_structure"     # Futures strip snapshot
    UNIVERSE = "universe"                 # Current active universe
    REGIME = "regime"                     # Macro/vol/trend state
    BROKER_ACCOUNT = "broker_account"     # Positions, cash, margin, P&L
    EXEC_QUALITY = "exec_quality"         # Slippage, fill rates

class Snapshot(BaseModel):
    """Explicit point-in-time state capture.
    Strategies consume snapshots, not raw data — this prevents
    recomputation and ensures reproducibility.
    """
    snapshot_id: Optional[int] = None
    snapshot_type: SnapshotType
    as_of: str                            # ISO timestamp
    body: dict                            # Snapshot-type-specific content
```

### 0.8 Vendor Adapters

```python
# research/market_data/ingestion.py

from abc import ABC, abstractmethod

class VendorAdapter(ABC):
    """Base for market data vendor adapters."""

    @abstractmethod
    def vendor_name(self) -> str: ...

    @abstractmethod
    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[RawBar]: ...

    @abstractmethod
    def fetch_instrument_info(self, symbol: str) -> InstrumentMaster: ...


class IBKRAdapter(VendorAdapter):
    """Interactive Brokers — live + recent history for futures + equities."""
    def vendor_name(self) -> str: return "ibkr"
    # ... implementation

class NorgateAdapter(VendorAdapter):
    """Norgate Data — equities with delistings, constituents, adjustments."""
    def vendor_name(self) -> str: return "norgate"
    # ... implementation

class BarchartAdapter(VendorAdapter):
    """Barchart — deep futures history for backfill (Carver pattern)."""
    def vendor_name(self) -> str: return "barchart"
    # ... implementation
```

### 0.9 Required Time Series Checklist

| Asset Class | Series | Priority |
|-------------|--------|----------|
| **Equities** | Raw daily OHLCV | P0 |
| | Adjusted OHLCV (splits/dividends) | P0 |
| | As-traded price/volume | P0 |
| | Corporate actions | P0 |
| | Delisted status | P0 |
| | Historical constituent membership | P0 |
| | Benchmark/index series (SPY, FTSE) | P0 |
| | Daily dollar volume | P0 |
| | Realized volatility (21d, 63d, 252d) | P1 |
| | Common indicators (RSI, ATR, MA) | P1 |
| | Optional fundamentals | P2 |
| | Earnings/event calendar | P2 |
| **Futures** | Contract-level OHLCV | P0 |
| | Bid/ask or mid + inside spread | P0 |
| | Open interest/volume | P0 |
| | Roll parameters + roll calendars | P0 |
| | Current/next/carry mapping | P0 |
| | Continuous adjusted price | P0 |
| | Carry/term-structure series | P0 |
| | Spot FX conversion | P0 |
| | Spread-cost + commission series | P1 |
| **FX/CFD** | Bid/ask or tick history | P1 |
| | Spread series | P1 |
| | Funding/swap rates | P1 |

### 0.10 Buy vs Build

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Equities data (delistings/constituents) | **Buy** — Norgate or equivalent | Quality + survivorship bias control not worth building |
| Futures live + recent history | **Buy** — IBKR API (already connected) | Broker-native, already integrated |
| Deep futures history | **Buy** — Barchart or external provider | Backfill only, Carver pattern |
| Data normalization + canonical series | **Build** — `research/market_data/` | Core app logic, must own |
| Snapshot engine | **Build** | Strategy-specific, must own |
| Charting | **Build** — embedded in app | Tightly coupled to backtest output + trade replay |

### 0.11 Phase 0 Deliverables

| File | Purpose |
|------|---------|
| `research/market_data/instruments.py` | InstrumentMaster model + DB CRUD |
| `research/market_data/raw_bars.py` | RawBar model + immutable ingestion |
| `research/market_data/canonical_bars.py` | CanonicalBar model + versioned normalization |
| `research/market_data/corporate_actions.py` | Corp action + universe membership models |
| `research/market_data/futures.py` | Contract, roll calendar, multiple prices, continuous series |
| `research/market_data/liquidity.py` | LiquidityCostEntry model |
| `research/market_data/snapshots.py` | Snapshot engine (7 types) |
| `research/market_data/ingestion.py` | Vendor adapters (IBKR, Norgate, Barchart) |
| `data/pg_connection.py` | PostgreSQL connection factory |
| `tests/test_instruments.py` | InstrumentMaster CRUD tests |
| `tests/test_raw_bars.py` | Ingestion + provenance tests |
| `tests/test_canonical_bars.py` | Normalization + versioning tests |
| `tests/test_futures_data.py` | Contract + roll + continuous series tests |
| `tests/test_snapshots.py` | Snapshot engine tests |

**Estimated:** ~10 files, ~50 tests, ~2,500-3,500 lines.

---

<a id="phase-1"></a>
## Phase 1: Artifact Schemas & Storage

**Goal:** Define all canonical artifact types as typed Python models + build PostgreSQL artifact store.

**Depends on:** Phase 0 (market data infrastructure) + P4 (intel pipeline refactor) complete.

### 1.1 Artifact Base

```python
# research/artifacts.py

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ArtifactType(str, Enum):
    EVENT_CARD = "event_card"
    HYPOTHESIS_CARD = "hypothesis_card"
    FALSIFICATION_MEMO = "falsification_memo"
    TEST_SPEC = "test_spec"
    EXPERIMENT_REPORT = "experiment_report"
    TRADE_SHEET = "trade_sheet"
    RETIREMENT_MEMO = "retirement_memo"
    REGIME_SNAPSHOT = "regime_snapshot"
    REGIME_JOURNAL = "regime_journal"
    REBALANCE_SHEET = "rebalance_sheet"
    ENGINE_A_SIGNAL_SET = "engine_a_signal_set"
    EXECUTION_REPORT = "execution_report"
    POST_MORTEM_NOTE = "post_mortem_note"
    REVIEW_TRIGGER = "review_trigger"
    SCORING_RESULT = "scoring_result"


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class EdgeFamily(str, Enum):
    UNDERREACTION_REVISION = "underreaction_revision"
    CARRY_RISK_TRANSFER = "carry_risk_transfer"
    TREND_MOMENTUM = "trend_momentum"
    FLOW_POSITIONING = "flow_positioning"
    RELATIVE_VALUE = "relative_value"
    CONVEXITY_INSURANCE = "convexity_insurance"
    REGIME_DISLOCATION = "regime_dislocation"


class Engine(str, Enum):
    ENGINE_A = "engine_a"
    ENGINE_B = "engine_b"


class PromotionOutcome(str, Enum):
    PROMOTE = "promote"
    REVISE = "revise"
    PARK = "park"
    REJECT = "reject"


@dataclass
class ArtifactEnvelope:
    """Wraps any artifact body with metadata for storage."""
    artifact_type: ArtifactType
    engine: Engine
    body: dict[str, Any]
    ticker: Optional[str] = None
    edge_family: Optional[EdgeFamily] = None
    status: ArtifactStatus = ArtifactStatus.ACTIVE
    created_by: str = "system"
    tags: list[str] = field(default_factory=list)
    # Set by artifact store on save:
    artifact_id: Optional[str] = None
    chain_id: Optional[str] = None
    version: int = 1
    parent_id: Optional[str] = None
    created_at: Optional[str] = None
```

### 1.2 Artifact Type Definitions

Each artifact body has a defined schema. Validated at creation time using Pydantic.

```python
# research/artifacts.py (continued)

from pydantic import BaseModel, Field, field_validator
from typing import Literal


# ── EventCard ──────────────────────────────────────────────

class EventCard(BaseModel):
    """Structured observation from a raw source event."""
    source_ids: list[str]
    source_class: Literal["filing", "transcript", "analyst_revision",
                          "news_wire", "sa_quant", "social_curated", "social_general"]
    source_credibility: float = Field(ge=0.0, le=1.0)
    event_timestamp: str
    corroboration_count: int = 0
    claims: list[str]                    # What changed (fact extraction)
    affected_instruments: list[str]      # Tickers affected
    market_implied_prior: str            # What the market expected
    materiality: Literal["high", "medium", "low"]
    time_sensitivity: Literal["immediate", "days", "weeks", "months"]
    raw_content_hash: str                # SHA256 of raw source text


# ── HypothesisCard ─────────────────────────────────────────

class HypothesisCard(BaseModel):
    """Testable trading hypothesis constrained to an edge family."""
    hypothesis_id: str = Field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:12]}")
    edge_family: EdgeFamily
    event_card_ref: str                  # artifact_id of source EventCard
    market_implied_view: str             # What the market currently prices
    variant_view: str                    # What we think is different
    mechanism: str                       # Why the edge exists (causal story)
    catalyst: str                        # What triggers convergence
    direction: Literal["long", "short"]
    horizon: Literal["intraday", "days", "weeks", "months"]
    confidence: float = Field(ge=0.0, le=1.0)
    invalidators: list[str]              # Specific conditions that kill the thesis
    failure_regimes: list[str]           # Regimes where this historically fails
    candidate_expressions: list[str]     # Possible instruments/structures
    testable_predictions: list[str]      # Falsifiable statements


# ── FalsificationMemo ──────────────────────────────────────

class FalsificationMemo(BaseModel):
    """Structured challenge of a hypothesis."""
    hypothesis_ref: str                  # artifact_id of HypothesisCard
    cheapest_alternative: str            # Simplest explanation that doesn't require edge
    beta_leakage_check: BetaLeakageResult
    crowding_check: CrowdingResult
    prior_evidence: list[PriorEvidence]
    unresolved_objections: list[str]     # CRITICAL: these block promotion
    resolved_objections: list[str]       # Addressed concerns
    challenge_model: str                 # Which model performed the challenge
    challenge_confidence: float = Field(ge=0.0, le=1.0)

class BetaLeakageResult(BaseModel):
    is_just_market_exposure: bool
    explanation: str
    estimated_beta: float = Field(ge=-2.0, le=2.0)

class CrowdingResult(BaseModel):
    crowding_level: Literal["low", "medium", "high", "extreme"]
    explanation: str
    correlated_strategies: list[str]

class PriorEvidence(BaseModel):
    description: str
    supports_hypothesis: bool
    source: str
    strength: Literal["strong", "moderate", "weak"]


# ── TestSpec ───────────────────────────────────────────────

class TestSpec(BaseModel):
    """Frozen experiment specification — must exist before any backtest."""
    hypothesis_ref: str
    datasets: list[DatasetSpec]
    feature_list: list[str]
    train_split: SplitSpec
    validation_split: SplitSpec
    test_split: SplitSpec
    baselines: list[str]                 # Benchmark strategies
    search_budget: int = Field(ge=1, le=50)  # Max parameter variants
    cost_model_ref: str                  # Which cost template
    eval_metrics: list[str]              # ["sharpe", "profit_factor", "max_dd", ...]
    frozen_at: str                       # ISO timestamp — immutable after this

class DatasetSpec(BaseModel):
    name: str
    ticker: str
    start_date: str
    end_date: str
    frequency: Literal["daily", "hourly", "minute"]
    point_in_time: bool = True           # No look-ahead

class SplitSpec(BaseModel):
    start_date: str
    end_date: str


# ── ExperimentReport ───────────────────────────────────────

class ExperimentReport(BaseModel):
    """Results of a registered experiment."""
    test_spec_ref: str
    variants_tested: int
    best_variant: dict[str, Any]         # Parameter set
    gross_metrics: PerformanceMetrics
    net_metrics: PerformanceMetrics      # After cost model
    robustness_checks: list[RobustnessCheck]
    capacity_estimate: Optional[CapacityEstimate] = None
    correlation_with_existing: dict[str, float]  # strategy_id → correlation
    implementation_caveats: list[str]

class PerformanceMetrics(BaseModel):
    sharpe: float
    sortino: float
    profit_factor: float
    win_rate: float
    max_drawdown: float
    total_return_pct: float
    avg_holding_days: float
    trade_count: int
    annual_turnover: float

class RobustnessCheck(BaseModel):
    name: str                            # "walk_forward", "subsample", "parameter_sensitivity"
    passed: bool
    detail: str

class CapacityEstimate(BaseModel):
    max_notional_usd: float
    limiting_factor: str                 # "daily_volume", "spread_impact", "market_depth"


# ── TradeSheet ─────────────────────────────────────────────

class TradeSheet(BaseModel):
    """Execution plan for a validated hypothesis."""
    hypothesis_ref: str
    experiment_ref: str
    instruments: list[InstrumentSpec]
    sizing: SizingSpec
    entry_rules: list[str]               # Human-readable conditions
    exit_rules: list[str]
    holding_period_target: str           # "5 days", "until invalidation"
    hedge_plan: Optional[str] = None
    risk_limits: RiskLimits
    kill_criteria: list[str]             # Conditions that force immediate exit

class InstrumentSpec(BaseModel):
    ticker: str
    instrument_type: Literal["spread_bet", "cfd", "future", "equity", "etf", "option"]
    broker: Literal["ig", "ibkr", "kraken", "paper"]
    contract_details: Optional[str] = None

class SizingSpec(BaseModel):
    method: Literal["fixed_notional", "vol_target", "kelly", "risk_parity"]
    target_risk_pct: float               # % of portfolio
    max_notional: float
    sizing_parameters: dict[str, Any] = {}

class RiskLimits(BaseModel):
    max_loss_pct: float                  # Stop loss as % of entry
    max_portfolio_impact_pct: float      # Max drawdown contribution
    max_correlated_exposure_pct: float   # With existing positions


# ── RetirementMemo ─────────────────────────────────────────

class RetirementMemo(BaseModel):
    """Post-mortem when a strategy is killed or parked."""
    hypothesis_ref: str
    trigger: Literal["invalidation", "decay", "drawdown", "operator_decision",
                      "regime_change", "cost_exceeded", "data_breach"]
    trigger_detail: str
    diagnosis: str                       # What went wrong
    lessons: list[str]                   # What we learned
    final_status: Literal["dead", "parked"]  # Dead = never retry; parked = revisit later
    performance_summary: Optional[PerformanceMetrics] = None
    live_duration_days: Optional[int] = None


# ── RegimeSnapshot ─────────────────────────────────────────

class RegimeSnapshot(BaseModel):
    """Point-in-time regime classification."""
    as_of: str
    vol_regime: Literal["low", "normal", "high", "crisis"]
    trend_regime: Literal["strong_trend", "choppy", "reversal"]
    carry_regime: Literal["steep", "flat", "inverted"]
    macro_regime: Literal["risk_on", "transition", "risk_off"]
    sizing_factor: float = Field(ge=0.0, le=1.0)
    active_overrides: list[str] = []
    indicators: dict[str, float] = {}    # VIX, yield spread, trend strength, etc.


# ── ScoringResult ──────────────────────────────────────────

class ScoringResult(BaseModel):
    """100-point rubric evaluation of a hypothesis."""
    hypothesis_ref: str
    falsification_ref: str
    dimension_scores: dict[str, float]   # dimension_name → score
    raw_total: float
    penalties: dict[str, float]          # penalty_name → deduction
    final_score: float
    outcome: PromotionOutcome
    outcome_reason: str
    blocking_objections: list[str]       # From FalsificationMemo
```

### 1.3 Artifact Store

```python
# research/artifact_store.py

class ArtifactStore:
    """PostgreSQL JSONB-backed immutable artifact persistence."""

    def __init__(self, dsn: str):
        """Connect to PostgreSQL. DSN from config.RESEARCH_DB_DSN."""

    def save(self, envelope: ArtifactEnvelope) -> str:
        """
        Insert artifact. Returns artifact_id.

        If envelope.chain_id is None → new chain (first version).
        If envelope.parent_id is set → new version of existing chain.
        Previous version gets status=SUPERSEDED.
        """

    def get(self, artifact_id: str) -> Optional[ArtifactEnvelope]:
        """Fetch single artifact by ID."""

    def get_chain(self, chain_id: str) -> list[ArtifactEnvelope]:
        """Fetch all versions of an artifact, ordered by version ASC."""

    def get_latest(self, chain_id: str) -> Optional[ArtifactEnvelope]:
        """Fetch latest version of an artifact chain."""

    def query(
        self,
        artifact_type: Optional[ArtifactType] = None,
        engine: Optional[Engine] = None,
        ticker: Optional[str] = None,
        edge_family: Optional[EdgeFamily] = None,
        status: Optional[ArtifactStatus] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        tags: Optional[list[str]] = None,
        search_text: Optional[str] = None,  # Full-text search
        limit: int = 50,
        offset: int = 0,
    ) -> list[ArtifactEnvelope]:
        """Query artifacts with filtering and full-text search."""

    def get_linked(
        self,
        artifact_id: str,
        link_type: Optional[str] = None,
    ) -> list[ArtifactEnvelope]:
        """Get artifacts linked to this one (via body refs)."""

    def count(
        self,
        artifact_type: Optional[ArtifactType] = None,
        engine: Optional[Engine] = None,
        status: Optional[ArtifactStatus] = None,
    ) -> int:
        """Count artifacts matching filters."""
```

### 1.4 Artifact Links

Artifacts reference each other via `*_ref` fields in their body (artifact_id strings). The store provides `get_linked()` to traverse these chains:

```
EventCard
    ↓ (event_card_ref)
HypothesisCard
    ↓ (hypothesis_ref)
├── FalsificationMemo
├── ScoringResult
├── TestSpec
│       ↓ (test_spec_ref)
│       ExperimentReport
├── TradeSheet
└── RetirementMemo (if killed)
```

### 1.5 Database Schema (PostgreSQL)

See architecture document section 2.3 for the `research.artifacts` table DDL.

Additional tables:

```sql
-- research.model_calls (see architecture doc section 2.4)

-- research.artifact_links (materialized for fast traversal)
CREATE TABLE research.artifact_links (
    from_id     UUID NOT NULL REFERENCES research.artifacts(artifact_id),
    to_id       UUID NOT NULL REFERENCES research.artifacts(artifact_id),
    link_type   TEXT NOT NULL,    -- 'event_card_ref', 'hypothesis_ref', etc.
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_id, to_id, link_type)
);
CREATE INDEX idx_links_to ON research.artifact_links(to_id);

-- research.pipeline_state (tracks where each hypothesis is in the pipeline)
CREATE TABLE research.pipeline_state (
    chain_id        UUID PRIMARY KEY,
    engine          TEXT NOT NULL,
    current_stage   TEXT NOT NULL,    -- 'intake', 'hypothesis', 'challenge', 'scoring',
                                     -- 'experiment', 'expression', 'shadow', 'staged', 'live', 'retired'
    outcome         TEXT,             -- 'promote', 'revise', 'park', 'reject' (null if in-progress)
    score           NUMERIC(5,1),
    ticker          TEXT,
    edge_family     TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    operator_ack    BOOLEAN DEFAULT FALSE,
    operator_notes  TEXT
);
CREATE INDEX idx_pipeline_stage ON research.pipeline_state(current_stage);
CREATE INDEX idx_pipeline_engine ON research.pipeline_state(engine);
```

### 1.6 Promotion Gate Extension

Modify `fund/promotion_gate.py` to support 4-state outcomes:

```python
# Extend PromotionGateDecision
@dataclass
class PromotionGateDecision:
    outcome: PromotionOutcome       # NEW: promote/revise/park/reject
    allowed: bool                   # Kept for backward compat (allowed = outcome == PROMOTE)
    reason_code: str
    message: str
    soak_remaining_hours: Optional[float] = None
    artifact_refs: list[str] = field(default_factory=list)  # NEW: supporting artifacts
    blocking_objections: list[str] = field(default_factory=list)  # NEW: from falsification
```

### 1.7 Deliverables

| File | Description |
|------|-------------|
| `research/__init__.py` | Package init |
| `research/artifacts.py` | All artifact Pydantic models + enums |
| `research/artifact_store.py` | PostgreSQL JSONB persistence |
| `data/pg_connection.py` | PostgreSQL connection factory |
| `fund/promotion_gate.py` | Extended with 4-state outcomes |
| `tests/test_artifacts.py` | Artifact validation tests |
| `tests/test_artifact_store.py` | Store CRUD + query tests |
| `tests/test_promotion_gate_v2.py` | 4-state outcome tests |

### 1.8 Acceptance Criteria

- All 7 artifact types validate correctly with Pydantic (required fields, types, ranges)
- Artifact store: save, get, get_chain, query, full-text search all work
- Immutability enforced: no UPDATE on artifacts, only INSERT with version chain
- Promotion gate returns 4-state outcomes
- Existing promotion gate tests still pass (backward compat)
- PostgreSQL connection factory works alongside existing SQLite

---

<a id="phase-2"></a>
## Phase 2: Structured Challenge Pipeline (Replace Council)

**Goal:** Replace the 4-model council vote with EventCard → HypothesisCard → FalsificationMemo → ScoringResult artifact flow.

**Depends on:** Phase 1 complete.

### 2.1 Model Router

```python
# research/model_router.py

@dataclass
class ModelConfig:
    provider: str            # 'anthropic', 'openai', 'xai', 'google'
    model_id: str            # 'claude-opus-4-6', 'gpt-5.4', etc.
    timeout_s: float = 60.0
    max_retries: int = 2
    backoff_s: float = 1.0
    thinking: bool = False   # Extended thinking (Anthropic/Google)
    thinking_budget: int = 10000
    temperature: float = 0.2
    max_tokens: int = 8192
    fallback: Optional[str] = None  # Fallback service key if primary fails

class ModelRouter:
    """Routes LLM calls to configured providers with retry, cost logging, and audit."""

    def __init__(self, config: dict[str, ModelConfig], artifact_store: ArtifactStore):
        """
        config: mapping of service_name → ModelConfig
        Loaded from config.RESEARCH_MODEL_CONFIG.
        """

    def call(
        self,
        service: str,             # 'signal_extraction', 'hypothesis_formation', etc.
        prompt: str,
        system_prompt: str = "",
        artifact_id: Optional[str] = None,  # For cost attribution
        engine: Engine = Engine.ENGINE_B,
    ) -> ModelResponse:
        """
        Route call to configured model for this service.
        Logs to research.model_calls.
        Returns parsed response.
        """

    def get_model_for_service(self, service: str) -> ModelConfig:
        """Look up which model handles this service."""

    def validate_no_self_challenge(self, formation_service: str, challenge_service: str) -> None:
        """
        Raise if formation and challenge share the same service configuration or
        prompt lineage. Different providers are preferred, but same-provider is
        acceptable if model/config families are independently versioned and
        benchmarked for disagreement quality.
        """

@dataclass
class ModelResponse:
    raw_text: str
    parsed: Optional[dict[str, Any]]    # JSON-extracted
    thinking: Optional[str]              # Extended thinking trace
    model_provider: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    prompt_hash: str
```

### 2.2 Signal Extraction Service

```python
# research/engine_b/signal_extraction.py

class SignalExtractionService:
    """Convert raw source events into structured EventCards."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        pass

    def extract(
        self,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
        source_url: Optional[str] = None,
    ) -> ArtifactEnvelope:
        """
        1. Normalize content (truncate to 16k chars, strip HTML)
        2. Build prompt from versioned template (v1_signal_extraction)
        3. Call model_router.call("signal_extraction", prompt)
        4. Parse response into EventCard
        5. Compute raw_content_hash
        6. Save as artifact (type=EVENT_CARD, engine=ENGINE_B)
        7. Return envelope with artifact_id
        """
```

**Prompt template** (`research/prompts/v1_signal_extraction.py`):

```python
SIGNAL_EXTRACTION_SYSTEM = """You are a financial event analyst for BoxRoomCapital,
a systematic trading fund. Your job is to extract structured facts from source material.

RULES:
- Extract WHAT CHANGED, not opinions about what it means
- Identify ALL affected instruments (tickers)
- State what the market was expecting (the prior)
- Assess materiality: would this move the stock >1% in isolation?
- Assess time sensitivity: how quickly must this be acted on?
- Do NOT speculate about trading direction — that comes later
- Be precise about claims — distinguish fact from analyst opinion
"""

SIGNAL_EXTRACTION_PROMPT = """Analyze this {source_class} content and extract structured facts.

SOURCE: {source_class} (credibility: {credibility})
CONTENT:
{content}

Respond with JSON matching this schema:
{{
    "claims": ["<factual claim 1>", "<factual claim 2>"],
    "affected_instruments": ["<TICKER1>", "<TICKER2>"],
    "market_implied_prior": "<what the market expected>",
    "materiality": "high|medium|low",
    "time_sensitivity": "immediate|days|weeks|months"
}}
"""
```

### 2.3 Hypothesis Formation Service

```python
# research/engine_b/hypothesis.py

class HypothesisService:
    """Generate constrained trading hypotheses from EventCards."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        pass

    def form_hypothesis(
        self,
        event_card_id: str,
        regime_snapshot: Optional[RegimeSnapshot] = None,
    ) -> ArtifactEnvelope:
        """
        1. Fetch EventCard from artifact store
        2. Build prompt with event + regime context + edge taxonomy list
        3. Call model_router.call("hypothesis_formation", prompt)
        4. Parse into HypothesisCard
        5. Validate edge_family is in EdgeFamily enum (taxonomy gate)
        6. Save as artifact (type=HYPOTHESIS_CARD)
        7. Create artifact_link from HypothesisCard → EventCard
        8. Return envelope
        """

    def _validate_taxonomy(self, edge_family: str) -> EdgeFamily:
        """Raise ValueError if edge_family not in approved taxonomy."""
```

**Prompt template** (`research/prompts/v1_hypothesis.py`):

```python
HYPOTHESIS_SYSTEM = """You are a systematic trading researcher for BoxRoomCapital.
Your job is to generate testable trading hypotheses from structured event observations.

CONSTRAINTS:
- Every hypothesis MUST map to exactly one approved edge family:
  {edge_families}
- State the market-implied view you disagree with
- Define specific, measurable invalidation criteria
- Propose a realistic holding period
- Suggest candidate instruments for expression

Do NOT generate hypotheses that don't fit an edge family.
Do NOT propose edges in illiquid or OTC instruments.
"""

HYPOTHESIS_PROMPT = """Generate a trading hypothesis from this event observation.

EVENT:
{event_card_json}

CURRENT REGIME:
{regime_json}

Respond with JSON matching this schema:
{{
    "edge_family": "<one of: {edge_family_list}>",
    "market_implied_view": "<what the market prices>",
    "variant_view": "<what we think is different>",
    "mechanism": "<why the edge exists>",
    "catalyst": "<what triggers convergence>",
    "direction": "long|short",
    "horizon": "intraday|days|weeks|months",
    "confidence": <0.0-1.0>,
    "invalidators": ["<condition that kills thesis>"],
    "failure_regimes": ["<regime where this historically fails>"],
    "candidate_expressions": ["<instrument/structure>"],
    "testable_predictions": ["<falsifiable statement>"]
}}
"""
```

### 2.4 Challenge & Falsification Service

```python
# research/engine_b/challenge.py

class ChallengeService:
    """Structured falsification of hypotheses. Uses DIFFERENT model than formation."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        # Validate that challenge model != hypothesis model
        self.model_router.validate_no_self_challenge(
            "hypothesis_formation", "challenge_falsification"
        )

    def challenge(self, hypothesis_id: str) -> ArtifactEnvelope:
        """
        1. Fetch HypothesisCard from artifact store
        2. Fetch linked EventCard for context
        3. Build prompt with hypothesis + event + challenge instructions
        4. Call model_router.call("challenge_falsification", prompt)
        5. Parse into FalsificationMemo
        6. Save as artifact (type=FALSIFICATION_MEMO)
        7. Create artifact_link from FalsificationMemo → HypothesisCard
        8. Return envelope

        CRITICAL: Unresolved objections in the FalsificationMemo block
        promotion regardless of scoring. The challenge model cannot
        resolve its own objections — only the operator can.
        """
```

**Prompt template** (`research/prompts/v1_challenge.py`):

```python
CHALLENGE_SYSTEM = """You are a skeptical risk analyst for BoxRoomCapital.
Your ONLY job is to find reasons this trade might be wrong.

RULES:
- Find the CHEAPEST alternative explanation (Occam's razor)
- Check for beta leakage: is this just market exposure in disguise?
- Check for crowding: is everyone else seeing this? Will they front-run?
- Search for prior evidence both for AND against
- List ALL unresolved objections — do NOT smooth them away
- Be specific: "this might not work" is useless; "AAPL has shown <2% post-guidance
  drift in 4 of last 6 quarters when VIX > 20" is useful
- You are REWARDED for finding real problems, not for being agreeable
"""

CHALLENGE_PROMPT = """Challenge this trading hypothesis. Find every reason it might fail.

HYPOTHESIS:
{hypothesis_json}

SOURCE EVENT:
{event_card_json}

Respond with JSON matching this schema:
{{
    "cheapest_alternative": "<simplest explanation not requiring an edge>",
    "beta_leakage_check": {{
        "is_just_market_exposure": true|false,
        "explanation": "<why>",
        "estimated_beta": <float>
    }},
    "crowding_check": {{
        "crowding_level": "low|medium|high|extreme",
        "explanation": "<why>",
        "correlated_strategies": ["<strategy>"]
    }},
    "prior_evidence": [
        {{"description": "<evidence>", "supports_hypothesis": true|false,
          "source": "<where>", "strength": "strong|moderate|weak"}}
    ],
    "unresolved_objections": ["<objection that hasn't been answered>"],
    "resolved_objections": ["<concern that has a good counter>"],
    "challenge_confidence": <0.0-1.0>
}}
"""
```

### 2.5 Scoring Engine

```python
# research/scorer.py

class ScoringEngine:
    """Deterministic 100-point rubric evaluation."""

    DIMENSION_WEIGHTS = {
        "source_integrity": 10,
        "mechanism_clarity": 15,
        "prior_empirical_support": 15,
        "incremental_info_advantage": 10,
        "regime_fit": 10,
        "testability": 10,
        "implementation_realism": 15,
        "portfolio_fit": 10,
        "kill_clarity": 5,
    }

    PENALTY_CAPS = {
        "search_complexity": -15,
        "crowding": -10,
        "data_fragility": -10,
    }

    THRESHOLDS = {
        PromotionOutcome.REJECT: (0, 60),
        PromotionOutcome.PARK: (60, 70),      # "revise" also maps here
        PromotionOutcome.PROMOTE: (70, 100),   # 70-79 test, 80-89 paper, 90+ live
    }

    def score(
        self,
        hypothesis: HypothesisCard,
        falsification: FalsificationMemo,
        regime: Optional[RegimeSnapshot] = None,
        existing_positions: Optional[dict[str, float]] = None,
    ) -> ScoringResult:
        """
        1. Compute each dimension score from artifact fields
        2. Sum to raw total
        3. Apply penalties (crowding from FalsificationMemo, etc.)
        4. Check for blocking objections (unresolved_objections > 0)
        5. If blocking objections: outcome = PARK regardless of score
        6. Otherwise: outcome from score thresholds
        7. Save as ScoringResult artifact
        8. Return result
        """

    def _score_source_integrity(self, hypothesis: HypothesisCard, event: EventCard) -> float:
        """Map source_credibility to 0-10 scale."""

    def _score_mechanism_clarity(self, hypothesis: HypothesisCard) -> float:
        """Rate mechanism specificity, causal chain clarity."""

    def _score_crowding_penalty(self, falsification: FalsificationMemo) -> float:
        """Map crowding_level to penalty: low=0, medium=-3, high=-7, extreme=-10."""

    # ... etc for each dimension
```

### 2.6 Engine B Pipeline Orchestrator

```python
# research/engine_b/pipeline.py

class EngineBPipeline:
    """Orchestrate the full Engine B artifact flow."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        model_router: ModelRouter,
        signal_extraction: SignalExtractionService,
        hypothesis_service: HypothesisService,
        challenge_service: ChallengeService,
        scoring_engine: ScoringEngine,
        experiment_service: ExperimentService,  # Phase 5
        cost_model: CostModel,                  # Phase 5
        kill_monitor: KillMonitor,              # Phase 6
    ):
        pass

    def process_event(
        self,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
    ) -> PipelineResult:
        """
        Full pipeline: intake → extract → hypothesize → challenge → score → decide.
        Each step produces an artifact. Pipeline halts at any rejection point.

        Returns PipelineResult with:
        - artifacts: list of all artifacts created
        - outcome: promote/revise/park/reject
        - score: final rubric score
        - blocking_reasons: list of reasons if not promoted
        """

    def process_event_async(self, ..., job_id: Optional[str] = None) -> str:
        """Spawn background thread for full pipeline. Returns job_id."""

    def _update_pipeline_state(self, chain_id: str, stage: str, outcome: Optional[str] = None):
        """Update research.pipeline_state table."""
```

### 2.7 Deliverables

| File | Description |
|------|-------------|
| `research/model_router.py` | Configurable LLM routing + cost logging |
| `research/engine_b/signal_extraction.py` | Raw → EventCard |
| `research/engine_b/hypothesis.py` | EventCard → HypothesisCard |
| `research/engine_b/challenge.py` | HypothesisCard → FalsificationMemo |
| `research/scorer.py` | 100-point rubric engine |
| `research/engine_b/pipeline.py` | Full pipeline orchestrator |
| `research/prompts/v1_signal_extraction.py` | Extraction prompt template |
| `research/prompts/v1_hypothesis.py` | Hypothesis prompt template |
| `research/prompts/v1_challenge.py` | Challenge prompt template |
| `tests/test_model_router.py` | Router tests (mocked providers) |
| `tests/test_signal_extraction.py` | Extraction tests |
| `tests/test_hypothesis.py` | Hypothesis + taxonomy validation |
| `tests/test_challenge.py` | Challenge + independence enforcement |
| `tests/test_scorer.py` | Rubric computation tests |
| `tests/test_engine_b_pipeline.py` | End-to-end pipeline tests |

### 2.8 Acceptance Criteria

- Model router routes to correct provider per service config
- Model router enforces separate service configs/prompt lineage for formation vs challenge
- Model router logs every call to `research.model_calls`
- Signal extraction produces valid EventCards from raw text
- Hypothesis service rejects hypotheses outside edge taxonomy
- Challenge service produces FalsificationMemo with unresolved objections
- Scoring engine computes correct 100-point scores with penalties
- Unresolved objections block promotion regardless of score
- Full pipeline produces linked artifact chain
- Pipeline state tracked in `research.pipeline_state`

---

<a id="phase-3"></a>
## Phase 3: Edge Taxonomy Enforcement

**Goal:** Ensure ALL hypotheses across both engines map to an approved edge family. Reject anything that doesn't fit.

**Depends on:** Phase 2 complete.

### 3.1 Taxonomy Service

```python
# research/taxonomy.py

class TaxonomyService:
    """Enforce edge family classification on all hypotheses."""

    APPROVED_FAMILIES = list(EdgeFamily)

    FAMILY_DESCRIPTIONS = {
        EdgeFamily.UNDERREACTION_REVISION: {
            "description": "Post-earnings drift, analyst revision, slow information diffusion",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["equities", "sector_etfs"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.CARRY_RISK_TRANSFER: {
            "description": "Interest rate differential, term premium, insurance premium",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "fx", "crypto_basis"],
            "primary_engine": Engine.ENGINE_A,
        },
        EdgeFamily.TREND_MOMENTUM: {
            "description": "Time-series continuation, cross-sectional momentum",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "equities", "etfs"],
            "primary_engine": Engine.ENGINE_A,
        },
        EdgeFamily.FLOW_POSITIONING: {
            "description": "Hedging pressure, forced selling, index rebalancing",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["equities", "futures"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.RELATIVE_VALUE: {
            "description": "Law-of-one-price violations, temporary divergences",
            "typical_horizon": "days to weeks",
            "typical_instruments": ["pairs", "etfs", "futures_spreads"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.CONVEXITY_INSURANCE: {
            "description": "Variance risk premium, skew premium, event-specific vol",
            "typical_horizon": "days to expiry",
            "typical_instruments": ["options"],
            "primary_engine": Engine.ENGINE_B,
        },
        EdgeFamily.REGIME_DISLOCATION: {
            "description": "Structural breaks, liquidity regime shifts, policy regime changes",
            "typical_horizon": "weeks to months",
            "typical_instruments": ["futures", "fx", "rates"],
            "primary_engine": Engine.ENGINE_A,
        },
    }

    def validate(self, edge_family: str) -> EdgeFamily:
        """Validate and return EdgeFamily enum. Raise if not approved."""
        try:
            return EdgeFamily(edge_family)
        except ValueError:
            raise TaxonomyRejection(
                f"Edge family '{edge_family}' not in approved taxonomy. "
                f"Approved: {[f.value for f in self.APPROVED_FAMILIES]}"
            )

    def get_family_info(self, family: EdgeFamily) -> dict:
        """Return description, typical horizon, instruments, primary engine."""
        return self.FAMILY_DESCRIPTIONS[family]

    def suggest_engine(self, family: EdgeFamily) -> Engine:
        """Suggest which engine should handle this edge family."""
        return self.FAMILY_DESCRIPTIONS[family]["primary_engine"]


class TaxonomyRejection(Exception):
    """Raised when a hypothesis doesn't map to an approved edge family."""
    pass
```

### 3.2 Integration Points

- `HypothesisService.form_hypothesis()` calls `TaxonomyService.validate()` after LLM generation
- LLM prompt includes the full taxonomy list so it can self-classify
- If LLM output doesn't match, the hypothesis is rejected with reason `TAXONOMY_REJECTION`
- Rejected hypotheses are still stored as artifacts (status=`RETIRED`) for audit

### 3.3 Deliverables

| File | Description |
|------|-------------|
| `research/taxonomy.py` | Taxonomy service + validation + family metadata |
| `tests/test_taxonomy.py` | Validation, rejection, suggestion tests |

### 3.4 Acceptance Criteria

- All 7 edge families validate correctly
- Invalid edge families raise TaxonomyRejection
- LLM hypothesis prompt includes full taxonomy
- Rejected hypotheses stored with audit trail
- Engine suggestion works for each family

---

<a id="phase-4"></a>
## Phase 4: Regime/State Context Service

**Goal:** Build a deterministic regime classifier that conditions both engines. LLM annotates regime changes for operator review.

**Depends on:** Phase 3 complete.

### 4.1 Regime Classifier

```python
# research/engine_a/regime.py

class RegimeClassifier:
    """Deterministic regime classification from market data."""

    def classify(self, as_of: str, market_data: dict[str, pd.DataFrame]) -> RegimeSnapshot:
        """
        Compute regime state from:
        - VIX level + percentile (vol regime)
        - Trend strength across major indices (trend regime)
        - Yield curve slope (carry regime)
        - Composite of above (macro regime)
        - Sizing factor derived from regime combination

        Returns RegimeSnapshot artifact body.
        """

    def _classify_vol(self, vix: float, vix_percentile: float) -> str:
        """
        < 15 and < 25th pctl → "low"
        15-25 and 25-75th pctl → "normal"
        25-35 and 75-90th pctl → "high"
        > 35 or > 90th pctl → "crisis"
        """

    def _classify_trend(self, index_data: dict[str, pd.DataFrame]) -> str:
        """
        Compute trend strength across SPY, EFA, IEF, DBC.
        Strong: 3+ indices above 200d EMA and positive 3-month momentum
        Choppy: mixed signals
        Reversal: 3+ indices below 200d EMA with negative momentum
        """

    def _classify_carry(self, yield_data: dict[str, float]) -> str:
        """
        10y-2y spread:
        > 100bp → "steep"
        0-100bp → "flat"
        < 0bp → "inverted"
        """

    def _compute_sizing_factor(self, vol: str, trend: str, carry: str) -> float:
        """
        risk_on (low/normal vol + strong trend + steep carry): 1.0
        transition: 0.75
        risk_off (high/crisis vol + reversal + inverted): 0.5
        """
```

### 4.2 Regime Journal (LLM, light)

```python
# research/shared/regime_journal.py

class RegimeJournalService:
    """Optional LLM annotation of regime changes."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        pass

    def annotate_transition(
        self,
        previous: RegimeSnapshot,
        current: RegimeSnapshot,
    ) -> ArtifactEnvelope:
        """
        Called only when regime changes.
        LLM produces ~200 word journal entry explaining the shift.
        Stored as REGIME_JOURNAL artifact linked to RegimeSnapshot.
        Purely for operator review — never feeds back into signals.
        """
```

### 4.3 Integration

- Engine A: `RegimeSnapshot.sizing_factor` directly scales position sizes
- Engine A: `RegimeSnapshot.active_overrides` adjusts signal weights (e.g., reduce trend in choppy)
- Engine B: `RegimeSnapshot` is passed to hypothesis formation as conditioning input
- Engine B: `ScoringEngine._score_regime_fit()` checks hypothesis compatibility with current regime
- Both: `RegimeSnapshot` saved as artifact daily (or on regime change)

### 4.4 Deliverables

| File | Description |
|------|-------------|
| `research/engine_a/regime.py` | Deterministic regime classifier |
| `research/shared/regime_journal.py` | LLM regime annotation |
| `research/prompts/v1_regime_journal.py` | Journal prompt template |
| `tests/test_regime_classifier.py` | Classification logic tests |
| `tests/test_regime_journal.py` | Journal generation tests |

### 4.5 Acceptance Criteria

- Regime classifier produces deterministic results from market data
- All regime states (vol, trend, carry, macro) correctly classified
- Sizing factor computed correctly for regime combinations
- Journal only generated on regime transitions
- RegimeSnapshot artifacts saved and queryable
- Engine B hypothesis prompt receives regime context

---

<a id="phase-5"></a>
## Phase 5: Cost Model Integration

**Goal:** Realistic cost modeling integrated into backtester. No strategy evaluated on gross returns alone.

**Depends on:** Phase 3 complete.

### 5.1 Cost Model

```python
# research/shared/cost_model.py

class CostModel:
    """Asset-class-specific cost templates."""

    # IG spread bet costs
    IG_COSTS = {
        "uk_equity": {"spread_bps": 10, "funding_daily_bps": 2.5, "min_spread_gbp": 0.5},
        "us_equity": {"spread_bps": 8, "funding_daily_bps": 2.5, "min_spread_gbp": 0.3},
        "index": {"spread_bps": 6, "funding_daily_bps": 1.5, "min_spread_gbp": 0.0},
        "commodity": {"spread_bps": 15, "funding_daily_bps": 3.0, "min_spread_gbp": 0.0},
        "fx": {"spread_bps": 3, "funding_daily_bps": 0.5, "min_spread_gbp": 0.0},
    }

    # IBKR futures costs
    IBKR_FUTURES = {
        "micro_equity": {"commission_per_side": 0.62, "exchange_fee": 0.25},
        "mini_equity": {"commission_per_side": 1.18, "exchange_fee": 0.50},
        "standard": {"commission_per_side": 2.25, "exchange_fee": 1.00},
    }

    # IBKR equity costs (ISA)
    IBKR_EQUITY = {
        "us": {"commission_pct": 0.0035, "min_commission": 0.35, "max_commission_pct": 0.5},
        "uk": {"commission_pct": 0.05, "min_commission": 3.0},
    }

    def estimate_round_trip_cost(
        self,
        instrument_type: str,
        broker: str,
        notional: float,
        holding_days: int,
        asset_class: str,
    ) -> CostEstimate:
        """
        Returns total estimated round-trip cost including:
        - Entry spread/commission
        - Exit spread/commission
        - Holding cost (funding for spread bets, roll for futures)
        - Slippage estimate (based on notional vs typical volume)
        """

    def apply_to_backtest(
        self,
        trades: list[dict],
        instrument_type: str,
        broker: str,
        asset_class: str,
    ) -> list[dict]:
        """
        Adjust backtest trade returns by subtracting realistic costs.
        Returns trades with 'net_return' field added.
        """

@dataclass
class CostEstimate:
    entry_cost: float
    exit_cost: float
    holding_cost: float
    slippage_estimate: float
    total_round_trip: float
    total_as_pct: float          # Total cost as % of notional
    cost_template: str           # Which template was used
```

### 5.2 Experiment Service

```python
# research/engine_b/experiment.py

class ExperimentService:
    """Manage registered experiments with cost-aware backtesting."""

    def __init__(self, artifact_store: ArtifactStore, cost_model: CostModel):
        pass

    def register_test(self, hypothesis_id: str, test_spec: TestSpec) -> ArtifactEnvelope:
        """
        Freeze TestSpec as artifact. After this point, no changes allowed.
        Validates:
        - Datasets are point-in-time
        - Search budget is within cap (≤50 variants)
        - Cost model is specified
        - Eval metrics include at least sharpe and profit_factor
        """

    def run_experiment(self, test_spec_id: str) -> ArtifactEnvelope:
        """
        Execute backtest against frozen TestSpec.
        1. Load data per DatasetSpec
        2. Run strategy variants up to search_budget
        3. Apply cost model to all results
        4. Compute gross AND net metrics
        5. Run robustness checks (walk-forward, subsample)
        6. Estimate capacity
        7. Compute correlation with existing strategies
        8. Save ExperimentReport artifact
        """

    def _walk_forward_check(self, ...) -> RobustnessCheck:
        """Split test period into 3 sub-windows, check consistency."""

    def _subsample_check(self, ...) -> RobustnessCheck:
        """Random subsample 70% of trades, check metric stability."""

    def _parameter_sensitivity(self, ...) -> RobustnessCheck:
        """Perturb best params ±20%, check if metrics hold."""
```

### 5.3 Deliverables

| File | Description |
|------|-------------|
| `research/shared/cost_model.py` | Asset-class cost templates + estimation |
| `research/engine_b/experiment.py` | TestSpec registration + cost-aware backtest |
| `tests/test_cost_model.py` | Cost estimation tests per asset class |
| `tests/test_experiment.py` | Registration, execution, robustness tests |

### 5.4 Acceptance Criteria

- Cost estimates match known IG/IBKR fee schedules
- TestSpec immutability enforced after registration
- Backtests produce both gross and net metrics
- Net metrics used for promotion decisions, not gross
- Walk-forward, subsample, and sensitivity checks run
- Search budget cap enforced

---

<a id="phase-6"></a>
## Phase 6: Retirement/Kill Formalization

**Goal:** Track declared invalidators per strategy. Formal kill criteria with RetirementMemo.

**Depends on:** Phase 5 complete.

### 6.1 Kill Monitor

```python
# research/shared/kill_monitor.py

class KillMonitor:
    """Track declared invalidators and enforce kill criteria."""

    KILL_TRIGGERS = [
        "invalidation",       # Thesis invalidation condition met
        "decay",              # Strategy performance decay
        "drawdown",           # Max drawdown exceeded
        "operator_decision",  # Manual kill
        "regime_change",      # Current regime incompatible
        "cost_exceeded",      # Implementation costs exceed expected
        "data_breach",        # Data source became unavailable/unreliable
    ]

    def __init__(self, artifact_store: ArtifactStore):
        pass

    def register_kill_criteria(
        self,
        hypothesis_id: str,
        criteria: list[KillCriterion],
    ) -> None:
        """Register specific kill criteria from TradeSheet.kill_criteria."""

    def check_all(self, as_of: str) -> list[KillAlert]:
        """
        Run all registered kill criteria against current market state.
        Returns list of triggered alerts.

        For each active hypothesis:
        1. Check declared invalidators against current data
        2. Check drawdown vs max_loss_pct from TradeSheet.risk_limits
        3. Check if current regime is in hypothesis.failure_regimes
        4. Check data source health
        """

    def execute_kill(
        self,
        hypothesis_id: str,
        trigger: str,
        trigger_detail: str,
        operator_approved: bool = False,
    ) -> ArtifactEnvelope:
        """
        Kill a live strategy:
        1. Generate RetirementMemo artifact
        2. Update pipeline_state to 'retired'
        3. Mark strategy_parameter_set as 'archived'
        4. Notify operator via Telegram
        5. Return RetirementMemo envelope

        Auto-kills allowed within preauthorized limits.
        Never auto-scales up.
        """

@dataclass
class KillCriterion:
    name: str                    # "vix_threshold", "max_drawdown", "thesis_invalidation"
    condition: str               # Human-readable condition
    check_function: str          # Reference to check implementation
    parameters: dict[str, Any]   # e.g., {"max_vix": 30, "max_dd_pct": 5}
    auto_kill: bool = False      # Can system auto-kill without operator approval?

@dataclass
class KillAlert:
    hypothesis_id: str
    criterion_name: str
    triggered_at: str
    current_value: Any
    threshold: Any
    auto_kill: bool
    message: str
```

### 6.2 Deliverables

| File | Description |
|------|-------------|
| `research/shared/kill_monitor.py` | Kill criteria tracking + enforcement |
| `tests/test_kill_monitor.py` | Kill criteria, alerts, retirement tests |

### 6.3 Acceptance Criteria

- Kill criteria registered per hypothesis from TradeSheet
- All triggers checked on schedule (daily)
- RetirementMemo produced on kill with full diagnosis
- Strategy archived in promotion gate
- Auto-kill only within preauthorized limits
- Operator notified on all kills

---

<a id="phase-7"></a>
## Phase 7: Decay-Triggered Review

**Goal:** Wire `analytics/decay_detector.py` into the promotion gate. Decay doesn't just alert — it blocks scaling and requires review.

**Depends on:** Phase 6 complete.

### 7.1 Decay Review Service

```python
# research/shared/decay_review.py

class DecayReviewService:
    """Wire decay detection into promotion gate review triggers."""

    def __init__(self, artifact_store: ArtifactStore, decay_detector: DecayDetector):
        pass

    def run_decay_check(self, as_of: str) -> list[ReviewTrigger]:
        """
        1. Call decay_detector.detect_decay() for all active strategies
        2. For each strategy with status 'warning' or 'decay':
           a. Create ReviewTrigger artifact
           b. Update pipeline_state: block further scaling
           c. Notify operator via Telegram
           d. Require explicit operator_ack before resuming
        3. Return list of triggered reviews
        """

    def acknowledge_review(
        self,
        chain_id: str,
        operator_decision: PromotionOutcome,
        notes: str,
    ) -> None:
        """
        Operator acknowledges decay review:
        - PROMOTE: resume normal operation (with optional parameter adjustment)
        - REVISE: modify strategy parameters, reset soak period
        - PARK: pause strategy, keep parameters for potential reactivation
        - REJECT: kill strategy, generate RetirementMemo
        """

@dataclass
class ReviewTrigger:
    strategy_id: str
    trigger_source: str          # "decay_detector"
    health_status: str           # "warning" or "decay"
    flags: list[str]             # From StrategyHealth.flags
    recent_metrics: dict[str, float]
    baseline_metrics: dict[str, float]
    recommended_action: PromotionOutcome
    artifact_id: str             # ReviewTrigger artifact ID
```

### 7.2 Integration with Promotion Gate

```python
# In fund/promotion_gate.py — additions:

def evaluate_promotion_gate(...) -> PromotionGateDecision:
    # ... existing checks ...

    # NEW: Check for active decay review
    active_reviews = artifact_store.query(
        artifact_type=ArtifactType.REVIEW_TRIGGER,
        ticker=strategy_key,
        status=ArtifactStatus.ACTIVE,
    )
    if active_reviews:
        unacked = [r for r in active_reviews
                   if not pipeline_state_get(r.chain_id).operator_ack]
        if unacked:
            return PromotionGateDecision(
                outcome=PromotionOutcome.PARK,
                allowed=False,
                reason_code="DECAY_REVIEW_PENDING",
                message=f"Decay review pending operator acknowledgement: {unacked[0].body['flags']}",
                artifact_refs=[r.artifact_id for r in unacked],
            )
```

### 7.3 Deliverables

| File | Description |
|------|-------------|
| `research/shared/decay_review.py` | Decay → promotion gate wiring |
| `fund/promotion_gate.py` | Extended with decay review check |
| `tests/test_decay_review.py` | Trigger, acknowledge, block tests |

### 7.4 Acceptance Criteria

- Decay detector output creates ReviewTrigger artifacts
- Promotion gate blocks scaling when decay review pending
- Operator must acknowledge before operations resume
- 4-state outcome available on acknowledgement
- Telegram notification sent on decay trigger

---

<a id="engine-a"></a>
## Engine A: Futures Trend/Carry/Macro Pipeline

**Built across Phases 4-5 with additional Engine A-specific work.**

### A.1 Signal Generation

```python
# research/engine_a/signals.py

class TrendSignal:
    """EWMA crossover blended across multiple lookbacks."""

    LOOKBACKS = [8, 16, 32, 64]

    def compute(self, prices: pd.Series) -> float:
        """
        For each lookback pair (fast, slow):
          signal = (EMA_fast - EMA_slow) / ATR
        Blend with equal weight across pairs.
        Normalize to [-1, +1] via sigmoid.
        """

class CarrySignal:
    """Annualized carry from term structure."""

    def compute(self, front_price: float, deferred_price: float, days_to_roll: int) -> float:
        """
        carry = (front - deferred) / front * (365 / days_to_roll)
        Normalize to [-1, +1] via historical percentile.
        """

class ValueSignal:
    """Z-score of real yield or price-to-fair-value."""

    def compute(self, current_value: float, history: pd.Series, lookback: int = 1260) -> float:
        """
        z = (current - rolling_mean) / rolling_std
        Clip to [-3, +3], normalize to [-1, +1].
        """

class MomentumSignal:
    """12-month return minus last month (Jegadeesh-Titman style)."""

    def compute(self, prices: pd.Series) -> float:
        """
        mom = return_12m - return_1m
        Normalize to [-1, +1] via historical percentile.
        """
```

### A.2 Portfolio Construction

```python
# research/engine_a/portfolio.py

class PortfolioConstructor:
    """Volatility-targeted risk parity portfolio construction."""

    def __init__(self, target_vol: float = 0.12, max_leverage: float = 4.0):
        pass

    def construct(
        self,
        forecasts: dict[str, float],    # instrument → combined forecast [-1, +1]
        vol_estimates: dict[str, float], # instrument → annualized vol
        correlations: pd.DataFrame,      # instrument × instrument
        regime: RegimeSnapshot,
        capital: float,
        contract_sizes: dict[str, float],
    ) -> dict[str, TargetPosition]:
        """
        1. Scale each forecast by instrument vol to get vol-adjusted forecast
        2. Apply risk parity weights (inverse vol)
        3. Apply correlation adjustment (diversification multiplier)
        4. Scale to target portfolio vol
        5. Apply regime sizing factor
        6. Cap at max_leverage
        7. Round to contract sizes
        """

@dataclass
class TargetPosition:
    instrument: str
    contracts: int              # Signed: positive = long, negative = short
    notional: float
    weight: float               # Portfolio weight
    forecast: float             # Combined signal
    vol_contribution: float     # Contribution to portfolio vol
```

### A.3 Rebalancer

```python
# research/engine_a/rebalancer.py

class Rebalancer:
    """Cost-filtered rebalance generation."""

    def __init__(self, cost_model: CostModel, min_trade_threshold: float = 0.1):
        pass

    def generate_rebalance(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, TargetPosition],
        cost_model: CostModel,
    ) -> ArtifactEnvelope:
        """
        1. Compute delta for each instrument
        2. Filter out trades where |delta / target| < min_trade_threshold
           (avoid churning on small drifts)
        3. Estimate cost for each trade
        4. If total cost > threshold, defer non-critical trades
        5. Produce RebalanceSheet artifact
        """
```

### A.4 Engine A Pipeline

```python
# research/engine_a/pipeline.py

class EngineAPipeline:
    """Daily orchestration cycle for Engine A."""

    def run_daily(self, as_of: str) -> EngineAResult:
        """
        1. Fetch market data (prices, yields, vol surfaces)
        2. Compute regime classification → RegimeSnapshot artifact
        3. Compute signals per instrument (trend, carry, value, momentum)
        4. Combine into forecast per instrument → EngineASignalSet artifact
        5. Construct target portfolio
        6. Generate rebalance (if needed) → RebalanceSheet artifact
        7. Submit OrderIntents through promotion gate
        8. Log execution → ExecutionReport artifact
        """
```

### A.5 Deliverables

| File | Description |
|------|-------------|
| `research/engine_a/__init__.py` | Package init |
| `research/engine_a/signals.py` | Trend, carry, value, momentum signal classes |
| `research/engine_a/regime.py` | Deterministic regime classifier (from Phase 4) |
| `research/engine_a/portfolio.py` | Vol-target risk parity construction |
| `research/engine_a/rebalancer.py` | Cost-filtered rebalance generation |
| `research/engine_a/pipeline.py` | Daily orchestration cycle |
| `tests/test_engine_a_signals.py` | Signal computation tests |
| `tests/test_engine_a_portfolio.py` | Portfolio construction tests |
| `tests/test_engine_a_rebalancer.py` | Rebalance + cost filter tests |
| `tests/test_engine_a_pipeline.py` | End-to-end daily cycle tests |

---

<a id="engine-b"></a>
## Engine B: Equity Event Pipeline

Engine B is primarily built across Phases 2-6. Additional Engine B-specific work:

### B.1 Expression Service

```python
# research/engine_b/expression.py

class ExpressionService:
    """Select instruments and build TradeSheet from validated hypothesis."""

    def __init__(self, model_router: ModelRouter, cost_model: CostModel):
        pass

    def build_trade_sheet(
        self,
        hypothesis_id: str,
        experiment_id: str,
        regime: RegimeSnapshot,
        existing_positions: dict[str, float],
    ) -> ArtifactEnvelope:
        """
        1. Fetch hypothesis + experiment artifacts
        2. Select best instrument/broker from candidate_expressions
        3. Compute sizing (vol-adjusted, regime-factored)
        4. Define entry/exit rules from experiment best_variant
        5. Set risk limits from hypothesis + experiment
        6. Extract kill criteria from hypothesis.invalidators
        7. Save TradeSheet artifact
        """
```

### B.2 Synthesis Service

```python
# research/shared/synthesis.py

class SynthesisService:
    """Bounded LLM summarization for operator review."""

    def synthesize(self, chain_id: str) -> str:
        """
        Fetch all artifacts in chain.
        Produce concise summary for operator:
        - What triggered it (EventCard)
        - What the thesis is (HypothesisCard)
        - What the objections are (FalsificationMemo)
        - What the score was (ScoringResult)
        - What the backtest showed (ExperimentReport)
        - What the trade plan is (TradeSheet)

        CRITICAL: Unresolved objections must be prominently displayed.
        The synthesis CANNOT smooth them away.
        """
```

### B.3 Post-Mortem Service

```python
# research/shared/post_mortem.py

class PostMortemService:
    """LLM-assisted analysis of completed trades."""

    def generate_post_mortem(self, hypothesis_id: str) -> ArtifactEnvelope:
        """
        After a trade completes (win or loss):
        1. Fetch full artifact chain
        2. Fetch execution data (fills, slippage, P&L)
        3. LLM analyzes: was thesis correct? What happened?
        4. Extract lessons for future hypotheses
        5. Save PostMortemNote artifact
        """
```

### B.4 Deliverables

| File | Description |
|------|-------------|
| `research/engine_b/expression.py` | Instrument selection + TradeSheet |
| `research/shared/synthesis.py` | Bounded LLM summarization |
| `research/shared/post_mortem.py` | Trade post-mortem analysis |
| `research/prompts/v1_synthesis.py` | Synthesis prompt template |
| `research/prompts/v1_post_mortem.py` | Post-mortem prompt template |
| `tests/test_expression.py` | TradeSheet generation tests |
| `tests/test_synthesis.py` | Summarization tests |
| `tests/test_post_mortem.py` | Post-mortem generation tests |

---

<a id="db-migration"></a>
## Database Migration Plan

### 10.1 Phase 1: Add PostgreSQL Alongside SQLite

```python
# data/pg_connection.py

from typing import Optional

import psycopg2
import psycopg2.pool
import threading

import config

_pool_lock = threading.Lock()
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None

def get_pg_connection():
    """Get PostgreSQL connection from thread-safe pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=10,
                    dsn=config.RESEARCH_DB_DSN,
                )
    return _pool.getconn()

def release_pg_connection(conn):
    """Return connection to pool."""
    if _pool is not None:
        _pool.putconn(conn)

def init_research_schema():
    """Create research schema + tables if they don't exist."""
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS research")
            cur.execute(ARTIFACTS_DDL)
            cur.execute(MODEL_CALLS_DDL)
            cur.execute(ARTIFACT_LINKS_DDL)
            cur.execute(PIPELINE_STATE_DDL)
        conn.commit()
    finally:
        release_pg_connection(conn)
```

### 10.2 Config Addition

```python
# config.py additions

# PostgreSQL for research system
RESEARCH_DB_DSN = _env_str("RESEARCH_DB_DSN", "postgresql://localhost:5432/boxroom_research")
```

### 10.3 Migration Sequence

| Step | Action | Downtime |
|------|--------|----------|
| 1 | Provision PostgreSQL for the `research` schema (Replit DB or external) | None |
| 2 | Create `research` schema + tables | None |
| 3 | Deploy research system writing to PostgreSQL | None |
| 4 | Existing operational data stays in SQLite | None |
| 5 | (Future) Migrate operational tables to PostgreSQL `ops` schema | Brief |
| 6 | (Future) Remove SQLite dependency | Brief |

Steps 5-6 are optional and can happen after the research system is proven.

---

<a id="testing"></a>
## Testing Strategy

### 11.1 Test Structure

```
tests/
├── test_instruments.py            # InstrumentMaster CRUD + vendor mapping
├── test_raw_bars.py               # Vendor-native ingestion + provenance
├── test_canonical_bars.py         # Normalization + versioning + quality flags
├── test_futures_data.py           # Contract + roll calendar + continuous series
├── test_snapshots.py              # Snapshot engine (7 types)
├── test_universe.py               # Historical constituent membership
├── test_liquidity.py              # Spread-cost + commission series
├── test_artifacts.py              # Pydantic model validation
├── test_artifact_store.py         # PostgreSQL CRUD + query
├── test_model_router.py           # Provider routing + cost logging
├── test_taxonomy.py               # Edge family validation
├── test_scorer.py                 # 100-point rubric computation
├── test_signal_extraction.py      # Raw → EventCard
├── test_hypothesis.py             # EventCard → HypothesisCard
├── test_challenge.py              # Challenge independence + objections
├── test_experiment.py             # TestSpec freeze + cost-aware backtest
├── test_cost_model.py             # Per-asset-class cost estimation
├── test_kill_monitor.py           # Kill criteria + alerts
├── test_decay_review.py           # Decay → promotion gate wiring
├── test_regime_classifier.py      # Deterministic regime classification
├── test_engine_a_signals.py       # Trend/carry/value/momentum
├── test_engine_a_portfolio.py     # Vol-target risk parity
├── test_engine_a_rebalancer.py    # Cost-filtered rebalance
├── test_engine_a_pipeline.py      # E2E daily cycle
├── test_engine_b_pipeline.py      # E2E event pipeline
├── test_expression.py             # TradeSheet generation
├── test_synthesis.py              # Bounded summarization
├── test_post_mortem.py            # Post-mortem generation
├── test_promotion_gate_v2.py      # 4-state outcomes
└── test_research_e2e.py           # Full system integration
```

### 11.2 Test Approach

| Layer | Method | Mocking |
|-------|--------|---------|
| Artifact models | Unit tests | None — pure Pydantic validation |
| Artifact store | Integration tests | Real PostgreSQL (test schema) |
| Model router | Unit tests | Mock HTTP clients |
| Scoring engine | Unit tests | None — deterministic computation |
| Signal generators | Unit tests | None — deterministic from data |
| Pipeline E2E | Integration tests | Mock LLM responses, real store |
| Cost model | Unit tests | None — deterministic computation |
| Regime classifier | Unit tests | Mock market data |
| Kill monitor | Unit tests | Mock artifact store |
| Decay review | Integration tests | Mock decay detector |

### 11.3 Estimated Test Count

| Phase | New Tests |
|-------|-----------|
| 0 (Market Data Infrastructure) | ~50 |
| 1 (Artifacts + Store) | ~40 |
| 2 (Challenge Pipeline) | ~60 |
| 3 (Taxonomy) | ~15 |
| 4 (Regime) | ~25 |
| 5 (Cost Model + Experiment) | ~35 |
| 6 (Kill Monitor) | ~20 |
| 7 (Decay Review) | ~15 |
| Engine A | ~40 |
| Engine B extras | ~20 |
| E2E integration | ~15 |
| **Total** | **~335** |

---

## Summary: Build Order

| Phase | Work | Key Files | Est. Tests |
|-------|------|-----------|-----------|
| **0** | Market data infrastructure (5-layer data model) | `research/market_data/*.py`, `data/pg_connection.py` | 50 |
| **1** | Artifact schemas + store + 4-state promotion | `research/artifacts.py`, `research/artifact_store.py` | 40 |
| **2** | Model router + signal extraction + hypothesis + challenge + scoring + pipeline | `research/model_router.py`, `research/engine_b/*.py`, `research/scorer.py` | 60 |
| **3** | Edge taxonomy enforcement | `research/taxonomy.py` | 15 |
| **4** | Regime classifier + journal | `research/engine_a/regime.py`, `research/shared/regime_journal.py` | 25 |
| **5** | Cost model + experiment service | `research/shared/cost_model.py`, `research/engine_b/experiment.py` | 35 |
| **6** | Kill monitor + retirement | `research/shared/kill_monitor.py` | 20 |
| **7** | Decay-triggered review | `research/shared/decay_review.py` | 15 |
| **A** | Engine A signals + portfolio + rebalancer + pipeline | `research/engine_a/*.py` | 40 |
| **B** | Engine B expression + synthesis + post-mortem | `research/engine_b/expression.py`, `research/shared/*.py` | 20 |
| **E2E** | Full integration tests | `tests/test_research_e2e.py` | 15 |

**Total new code:** ~40 files, ~335 tests, estimated 8,500-11,000 lines.

**Build sequence rationale:** Phase 0 first because both engines need clean market data as substrate. The solo operator evidence (Carver, Alvarez, Davey) uniformly shows that numeric data infrastructure is the foundation — textual intelligence (Phase 2, Engine B) is added on top, not the other way around.
