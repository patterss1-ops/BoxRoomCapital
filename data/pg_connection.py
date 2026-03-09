"""PostgreSQL connection pool and schema bootstrap for the research system."""

from __future__ import annotations

import threading
from typing import Any

import config

try:
    import psycopg2.pool as _psycopg2_pool
except ImportError:  # pragma: no cover - exercised via runtime guard
    _psycopg2_pool = None

_pool_lock = threading.Lock()
_pool: Any | None = None

_SCHEMA_DDL = [
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE SCHEMA IF NOT EXISTS research",
    """
    CREATE TABLE IF NOT EXISTS research.instruments (
        instrument_id SERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        asset_class TEXT NOT NULL,
        venue TEXT NOT NULL,
        currency TEXT NOT NULL,
        session_template TEXT,
        multiplier NUMERIC,
        tick_size NUMERIC,
        vendor_ids JSONB DEFAULT '{}'::jsonb,
        is_active BOOLEAN DEFAULT true,
        listing_date DATE,
        delisting_date DATE,
        metadata JSONB DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(symbol, venue, asset_class)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.raw_bars (
        bar_id BIGSERIAL PRIMARY KEY,
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        vendor TEXT NOT NULL,
        bar_timestamp TIMESTAMPTZ NOT NULL,
        session_code TEXT,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        volume BIGINT,
        bid NUMERIC,
        ask NUMERIC,
        ingestion_ver INTEGER DEFAULT 1,
        ingested_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_bars_lookup ON research.raw_bars(instrument_id, bar_timestamp)",
    """
    CREATE TABLE IF NOT EXISTS research.canonical_bars (
        bar_id BIGSERIAL PRIMARY KEY,
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        bar_date DATE NOT NULL,
        open NUMERIC,
        high NUMERIC,
        low NUMERIC,
        close NUMERIC,
        adj_close NUMERIC,
        volume BIGINT,
        dollar_volume NUMERIC,
        session_template TEXT NOT NULL,
        data_version INTEGER DEFAULT 1,
        quality_flags TEXT[] DEFAULT ARRAY[]::TEXT[],
        UNIQUE(instrument_id, bar_date, data_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.universe_membership (
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        universe TEXT NOT NULL,
        from_date DATE NOT NULL,
        to_date DATE,
        PRIMARY KEY (instrument_id, universe, from_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.corporate_actions (
        action_id SERIAL PRIMARY KEY,
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        action_type TEXT NOT NULL,
        ex_date DATE NOT NULL,
        ratio NUMERIC,
        details JSONB DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.futures_contracts (
        contract_id SERIAL PRIMARY KEY,
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        root_symbol TEXT NOT NULL,
        expiry_date DATE NOT NULL,
        contract_code TEXT NOT NULL,
        roll_date DATE,
        is_front BOOLEAN DEFAULT false,
        UNIQUE(root_symbol, expiry_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.roll_calendar (
        root_symbol TEXT NOT NULL,
        roll_date DATE NOT NULL,
        from_contract TEXT NOT NULL,
        to_contract TEXT NOT NULL,
        roll_type TEXT DEFAULT 'standard',
        PRIMARY KEY (root_symbol, roll_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.liquidity_costs (
        liquidity_id BIGSERIAL PRIMARY KEY,
        instrument_id INTEGER REFERENCES research.instruments(instrument_id),
        as_of DATE NOT NULL,
        inside_spread NUMERIC,
        spread_cost_bps NUMERIC,
        commission_per_unit NUMERIC,
        funding_rate NUMERIC,
        borrow_cost NUMERIC,
        UNIQUE (instrument_id, as_of)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.snapshots (
        snapshot_id BIGSERIAL PRIMARY KEY,
        snapshot_type TEXT NOT NULL,
        as_of TIMESTAMPTZ NOT NULL,
        body JSONB NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshots_type_time ON research.snapshots(snapshot_type, as_of DESC)",
    """
    CREATE TABLE IF NOT EXISTS research.artifacts (
        artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_type TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        parent_id UUID REFERENCES research.artifacts(artifact_id),
        chain_id UUID NOT NULL,
        engine TEXT NOT NULL,
        ticker TEXT,
        edge_family TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        body JSONB NOT NULL,
        scores JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by TEXT NOT NULL,
        tags TEXT[] DEFAULT ARRAY[]::TEXT[],
        search_text TSVECTOR GENERATED ALWAYS AS (
            to_tsvector(
                'english',
                coalesce(body->>'summary', '') || ' ' || coalesce(body->>'thesis', '')
            )
        ) STORED
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_type ON research.artifacts(artifact_type)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_chain ON research.artifacts(chain_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_engine ON research.artifacts(engine)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_ticker ON research.artifacts(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_edge ON research.artifacts(edge_family)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_status ON research.artifacts(status)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_body ON research.artifacts USING GIN(body)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_search ON research.artifacts USING GIN(search_text)",
    """
    CREATE TABLE IF NOT EXISTS research.model_calls (
        call_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_id UUID REFERENCES research.artifacts(artifact_id),
        service TEXT NOT NULL,
        engine TEXT NOT NULL,
        model_provider TEXT NOT NULL,
        model_id TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_usd NUMERIC(10,6) NOT NULL,
        latency_ms INTEGER NOT NULL,
        success BOOLEAN NOT NULL,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.artifact_links (
        from_id UUID NOT NULL REFERENCES research.artifacts(artifact_id),
        to_id UUID NOT NULL REFERENCES research.artifacts(artifact_id),
        link_type TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (from_id, to_id, link_type)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_links_to ON research.artifact_links(to_id)",
    """
    CREATE TABLE IF NOT EXISTS research.pipeline_state (
        chain_id UUID PRIMARY KEY,
        engine TEXT NOT NULL,
        current_stage TEXT NOT NULL,
        outcome TEXT,
        score NUMERIC(5,1),
        ticker TEXT,
        edge_family TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        operator_ack BOOLEAN DEFAULT FALSE,
        operator_notes TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pipeline_stage ON research.pipeline_state(current_stage)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_engine ON research.pipeline_state(engine)",
    """
    CREATE TABLE IF NOT EXISTS research.prompt_hashes (
        service TEXT PRIMARY KEY,
        prompt_hash TEXT NOT NULL,
        acknowledged_hash TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        drift_status TEXT NOT NULL DEFAULT 'ok',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        acknowledged_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research.feature_cache (
        instrument TEXT NOT NULL,
        as_of DATE NOT NULL,
        signal_type TEXT NOT NULL,
        data_version TEXT NOT NULL,
        raw_value NUMERIC NOT NULL,
        normalized_value NUMERIC NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (instrument, as_of, signal_type, data_version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feature_cache_lookup ON research.feature_cache(instrument, as_of, signal_type)",
]


def reset_pg_pool() -> None:
    """Reset the singleton pool. Used by tests to avoid cross-test leakage."""
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
    _pool = None


def get_pg_connection():
    """Return a pooled PostgreSQL connection."""
    global _pool
    if _psycopg2_pool is None:
        raise RuntimeError("psycopg2 is required for the research PostgreSQL layer")
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _psycopg2_pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=10,
                    dsn=config.RESEARCH_DB_DSN,
                )
    return _pool.getconn()


def release_pg_connection(conn) -> None:
    """Return a PostgreSQL connection to the pool."""
    if _pool is not None and conn is not None:
        _pool.putconn(conn)


def init_research_schema() -> None:
    """Create the research schema and all known tables idempotently."""
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            for statement in _SCHEMA_DDL:
                cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def research_db_status() -> dict[str, Any]:
    """Return coarse readiness for the research PostgreSQL layer."""
    dsn = str(getattr(config, "RESEARCH_DB_DSN", "") or "").strip()
    if not dsn:
        return {
            "configured": False,
            "driver_available": _psycopg2_pool is not None,
            "reachable": False,
            "schema_ready": False,
            "status": "missing_dsn",
            "detail": "RESEARCH_DB_DSN is empty",
        }
    if _psycopg2_pool is None:
        return {
            "configured": True,
            "driver_available": False,
            "reachable": False,
            "schema_ready": False,
            "status": "driver_missing",
            "detail": "psycopg2 is not installed",
        }

    conn = None
    try:
        conn = get_pg_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    to_regclass('research.artifacts'),
                    to_regclass('research.pipeline_state'),
                    to_regclass('research.feature_cache')
                """
            )
            row = cur.fetchone() or (None, None, None)
        schema_ready = all(row)
        return {
            "configured": True,
            "driver_available": True,
            "reachable": True,
            "schema_ready": schema_ready,
            "status": "ready" if schema_ready else "schema_missing",
            "detail": (
                "research schema ready"
                if schema_ready
                else "core research tables are missing; run init_research_schema()"
            ),
        }
    except Exception as exc:
        return {
            "configured": True,
            "driver_available": True,
            "reachable": False,
            "schema_ready": False,
            "status": "connect_failed",
            "detail": str(exc),
        }
    finally:
        release_pg_connection(conn)
