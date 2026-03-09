"""Immutable PostgreSQL-backed artifact persistence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import config
from data.pg_connection import get_pg_connection, release_pg_connection
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactStatus,
    ArtifactType,
    EdgeFamily,
    Engine,
    validate_artifact_body,
)
from research.shared.sql import fetchall_dicts, fetchone_dict


class ArtifactStore:
    """PostgreSQL JSONB-backed immutable artifact persistence."""

    def __init__(self, dsn: str | None = None, connection_factory=get_pg_connection, release_factory=release_pg_connection):
        self.dsn = dsn or config.RESEARCH_DB_DSN
        self._get_connection = connection_factory
        self._release_connection = release_factory

    @staticmethod
    def _extract_links(body: dict[str, Any]) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        for key, value in body.items():
            if key.endswith("_ref") and isinstance(value, str):
                links.append((key, value))
            elif key.endswith("_refs") and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        links.append((key, item))
        return links

    @staticmethod
    def _row_to_envelope(row: dict[str, Any]) -> ArtifactEnvelope:
        return ArtifactEnvelope(
            artifact_id=str(row["artifact_id"]),
            artifact_type=row["artifact_type"],
            chain_id=str(row["chain_id"]),
            version=row["version"],
            parent_id=str(row["parent_id"]) if row.get("parent_id") else None,
            engine=row["engine"],
            ticker=row.get("ticker"),
            edge_family=row.get("edge_family"),
            status=row["status"],
            body=row["body"],
            created_at=row.get("created_at").isoformat().replace("+00:00", "Z") if row.get("created_at") else None,
            created_by=row["created_by"],
            tags=row.get("tags") or [],
        )

    def save(self, envelope: ArtifactEnvelope) -> str:
        validated_body = validate_artifact_body(envelope.artifact_type, envelope.body)
        envelope.body = validated_body.model_dump(mode="json")
        envelope.ensure_ids()

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                if envelope.parent_id:
                    cur.execute(
                        """
                        SELECT artifact_id, chain_id, version
                        FROM research.artifacts
                        WHERE artifact_id = %s
                        """,
                        (envelope.parent_id,),
                    )
                    parent_row = fetchone_dict(cur)
                    if parent_row:
                        envelope.chain_id = str(parent_row["chain_id"])
                        envelope.version = int(parent_row["version"]) + 1
                        cur.execute(
                            """
                            UPDATE research.artifacts
                            SET status = %s
                            WHERE artifact_id = %s
                            """,
                            (ArtifactStatus.SUPERSEDED.value, envelope.parent_id),
                        )

                if envelope.chain_id is None:
                    envelope.chain_id = str(uuid.uuid4())

                cur.execute(
                    """
                    INSERT INTO research.artifacts (
                        artifact_id, artifact_type, version, parent_id, chain_id, engine,
                        ticker, edge_family, status, body, scores, created_at, created_by, tags
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, NULL, %s, %s, %s
                    )
                    """,
                    (
                        envelope.artifact_id,
                        envelope.artifact_type.value,
                        envelope.version,
                        envelope.parent_id,
                        envelope.chain_id,
                        envelope.engine.value,
                        envelope.ticker,
                        envelope.edge_family.value if envelope.edge_family else None,
                        envelope.status.value,
                        json.dumps(envelope.body),
                        envelope.created_at,
                        envelope.created_by,
                        envelope.tags,
                    ),
                )

                for link_type, target_id in self._extract_links(envelope.body):
                    cur.execute(
                        """
                        INSERT INTO research.artifact_links (from_id, to_id, link_type)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (from_id, to_id, link_type) DO NOTHING
                        """,
                        (envelope.artifact_id, target_id, link_type),
                    )
            conn.commit()
            return envelope.artifact_id
        except Exception:
            conn.rollback()
            raise
        finally:
            self._release_connection(conn)

    def get(self, artifact_id: str) -> ArtifactEnvelope | None:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT artifact_id, artifact_type, version, parent_id, chain_id, engine,
                           ticker, edge_family, status, body, created_at, created_by, tags
                    FROM research.artifacts
                    WHERE artifact_id = %s
                    """,
                    (artifact_id,),
                )
                row = fetchone_dict(cur)
            return self._row_to_envelope(row) if row else None
        finally:
            self._release_connection(conn)

    def get_chain(self, chain_id: str) -> list[ArtifactEnvelope]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT artifact_id, artifact_type, version, parent_id, chain_id, engine,
                           ticker, edge_family, status, body, created_at, created_by, tags
                    FROM research.artifacts
                    WHERE chain_id = %s
                    ORDER BY version ASC
                    """,
                    (chain_id,),
                )
                rows = fetchall_dicts(cur)
            return [self._row_to_envelope(row) for row in rows]
        finally:
            self._release_connection(conn)

    def get_latest(self, chain_id: str) -> ArtifactEnvelope | None:
        chain = self.get_chain(chain_id)
        return chain[-1] if chain else None

    def query(
        self,
        artifact_type: ArtifactType | None = None,
        engine: Engine | None = None,
        ticker: str | None = None,
        edge_family: EdgeFamily | None = None,
        status: ArtifactStatus | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        tags: list[str] | None = None,
        search_text: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ArtifactEnvelope]:
        clauses = ["1=1"]
        params: list[Any] = []
        if artifact_type is not None:
            clauses.append("artifact_type = %s")
            params.append(artifact_type.value)
        if engine is not None:
            clauses.append("engine = %s")
            params.append(engine.value)
        if ticker is not None:
            clauses.append("ticker = %s")
            params.append(ticker)
        if edge_family is not None:
            clauses.append("edge_family = %s")
            params.append(edge_family.value)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        if created_after is not None:
            clauses.append("created_at >= %s")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at <= %s")
            params.append(created_before)
        if tags:
            clauses.append("tags @> %s")
            params.append(tags)
        if search_text:
            clauses.append("search_text @@ plainto_tsquery('english', %s)")
            params.append(search_text)
        params.extend([limit, offset])

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT artifact_id, artifact_type, version, parent_id, chain_id, engine,
                           ticker, edge_family, status, body, created_at, created_by, tags
                    FROM research.artifacts
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, version DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple(params),
                )
                rows = fetchall_dicts(cur)
            return [self._row_to_envelope(row) for row in rows]
        finally:
            self._release_connection(conn)

    def get_linked(self, artifact_id: str, link_type: str | None = None) -> list[ArtifactEnvelope]:
        clauses = ["links.from_id = %s"]
        params: list[Any] = [artifact_id]
        if link_type is not None:
            clauses.append("links.link_type = %s")
            params.append(link_type)

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT artifacts.artifact_id, artifacts.artifact_type, artifacts.version,
                           artifacts.parent_id, artifacts.chain_id, artifacts.engine,
                           artifacts.ticker, artifacts.edge_family, artifacts.status,
                           artifacts.body, artifacts.created_at, artifacts.created_by,
                           artifacts.tags
                    FROM research.artifact_links links
                    JOIN research.artifacts artifacts
                      ON artifacts.artifact_id = links.to_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY artifacts.created_at DESC, artifacts.version DESC
                    """,
                    tuple(params),
                )
                rows = fetchall_dicts(cur)
            return [self._row_to_envelope(row) for row in rows]
        finally:
            self._release_connection(conn)

    def count(
        self,
        artifact_type: ArtifactType | None = None,
        engine: Engine | None = None,
        status: ArtifactStatus | None = None,
    ) -> int:
        clauses = ["1=1"]
        params: list[Any] = []
        if artifact_type is not None:
            clauses.append("artifact_type = %s")
            params.append(artifact_type.value)
        if engine is not None:
            clauses.append("engine = %s")
            params.append(engine.value)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)

        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM research.artifacts WHERE {' AND '.join(clauses)}",
                    tuple(params),
                )
                row = cur.fetchone()
            return int(row[0] if row else 0)
        finally:
            self._release_connection(conn)
