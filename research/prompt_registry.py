"""Prompt hash registration and drift detection."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from data.pg_connection import get_pg_connection, release_pg_connection
from research.prompts.v1_challenge import build_challenge_prompt
from research.prompts.v1_hypothesis import build_hypothesis_prompt
from research.prompts.v1_post_mortem import build_post_mortem_prompt
from research.prompts.v1_regime_journal import build_regime_journal_prompt
from research.prompts.v1_signal_extraction import build_signal_extraction_prompt
from research.prompts.v1_synthesis import build_synthesis_prompt


PromptBuilder = Callable[[], tuple[str, str, str]]


def _default_prompt_builders() -> dict[str, PromptBuilder]:
    return {
        "signal_extraction": lambda: (
            "v1",
            *build_signal_extraction_prompt("news_wire", 0.8, "Revenue beat and raised guide."),
        ),
        "hypothesis_formation": lambda: (
            "v1",
            *build_hypothesis_prompt(
                {
                    "claims": ["Revenue beat"],
                    "affected_instruments": ["AAPL"],
                    "market_implied_prior": "Muted growth",
                },
                {"vol_regime": "normal"},
            ),
        ),
        "hypothesis_challenge": lambda: (
            "v1_challenge",
            *build_challenge_prompt(
                {
                    "edge_family": "underreaction_revision",
                    "variant_view": "Positive drift",
                    "event_card_ref": "evt-1",
                },
                {"claims": ["Revenue beat"]},
            ),
        ),
        "regime_journal": lambda: (
            "v1",
            *build_regime_journal_prompt(
                {
                    "as_of": "2026-03-08T20:00:00Z",
                    "vol_regime": "normal",
                    "trend_regime": "choppy",
                    "carry_regime": "flat",
                    "macro_regime": "transition",
                    "sizing_factor": 0.75,
                    "active_overrides": ["reduce_trend_weight"],
                    "indicators": {"vix": 21.0},
                },
                {
                    "as_of": "2026-03-09T20:00:00Z",
                    "vol_regime": "high",
                    "trend_regime": "reversal",
                    "carry_regime": "inverted",
                    "macro_regime": "risk_off",
                    "sizing_factor": 0.5,
                    "active_overrides": ["de_risk"],
                    "indicators": {"vix": 31.0},
                },
            ),
        ),
        "research_synthesis": lambda: (
            "v1",
            *build_synthesis_prompt(
                "chain-1",
                [
                    {
                        "artifact_type": "event_card",
                        "body": {"claims": ["Revenue beat"], "affected_instruments": ["AAPL"]},
                    },
                    {
                        "artifact_type": "falsification_memo",
                        "body": {"unresolved_objections": ["Short sample"]},
                    },
                ],
            ),
        ),
        "post_mortem": lambda: (
            "v1",
            *build_post_mortem_prompt(
                "hyp-1",
                [
                    {
                        "artifact_type": "hypothesis_card",
                        "body": {"variant_view": "Positive drift", "invalidators": ["Guide cut"]},
                    },
                    {
                        "artifact_type": "execution_report",
                        "body": {"trades_filled": 3, "cost": 12.5},
                    },
                ],
            ),
        ),
    }


def _hash_prompt(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256(f"{system_prompt}\n\n{user_prompt}".encode("utf-8")).hexdigest()


def register_prompts(prompt_builders: dict[str, PromptBuilder] | None = None) -> None:
    builders = prompt_builders or _default_prompt_builders()
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            for service, builder in builders.items():
                prompt_version, system_prompt, user_prompt = builder()
                current_hash = _hash_prompt(system_prompt, user_prompt)
                cur.execute(
                    """
                    SELECT prompt_hash, acknowledged_hash
                    FROM research.prompt_hashes
                    WHERE service = %s
                    """,
                    (service,),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """
                        INSERT INTO research.prompt_hashes (
                            service, prompt_hash, acknowledged_hash, prompt_version,
                            drift_status, updated_at, acknowledged_at
                        )
                        VALUES (%s, %s, %s, %s, 'ok', now(), now())
                        """,
                        (service, current_hash, current_hash, prompt_version),
                    )
                elif row[0] != current_hash:
                    cur.execute(
                        """
                        UPDATE research.prompt_hashes
                        SET prompt_hash = %s,
                            prompt_version = %s,
                            drift_status = 'PROMPT_DRIFT',
                            updated_at = now()
                        WHERE service = %s
                        """,
                        (current_hash, prompt_version, service),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def check_drift(service: str, prompt_builders: dict[str, PromptBuilder] | None = None) -> dict[str, object]:
    builders = prompt_builders or _default_prompt_builders()
    if service not in builders:
        raise KeyError(f"Unknown prompt service: {service}")
    prompt_version, system_prompt, user_prompt = builders[service]()
    current_hash = _hash_prompt(system_prompt, user_prompt)

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prompt_hash, acknowledged_hash, drift_status
                FROM research.prompt_hashes
                WHERE service = %s
                """,
                (service,),
            )
            row = cur.fetchone()
        if row is None:
            return {
                "service": service,
                "status": "unregistered",
                "current_hash": current_hash,
                "prompt_version": prompt_version,
                "acknowledged": False,
            }
        stored_hash, acknowledged_hash, drift_status = row
        status = "PROMPT_DRIFT" if current_hash != acknowledged_hash else "ok"
        if current_hash != stored_hash:
            status = "PROMPT_DRIFT"
        return {
            "service": service,
            "status": status if drift_status == "PROMPT_DRIFT" or status == "PROMPT_DRIFT" else "ok",
            "current_hash": current_hash,
            "stored_hash": stored_hash,
            "acknowledged_hash": acknowledged_hash,
            "prompt_version": prompt_version,
            "acknowledged": current_hash == acknowledged_hash,
        }
    finally:
        release_pg_connection(conn)


def acknowledge_drift(service: str) -> None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE research.prompt_hashes
                SET acknowledged_hash = prompt_hash,
                    drift_status = 'ok',
                    acknowledged_at = now()
                WHERE service = %s
                """,
                (service,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_pg_connection(conn)


def get_prompt_hash(service: str) -> str | None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prompt_hash FROM research.prompt_hashes WHERE service = %s",
                (service,),
            )
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        release_pg_connection(conn)
