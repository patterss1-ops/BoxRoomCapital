"""Unit tests for E-003: L8 SA Quant RapidAPI adapter."""

from __future__ import annotations

import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.signal.contracts import LayerScore
from app.signal.types import LayerId
from data import trade_db
from intelligence.jobs.sa_quant_job import SAQuantJobRunner
from intelligence.sa_quant_client import (
    SAQuantClient,
    SAQuantClientConfig,
    SAQuantClientError,
    SAQuantSnapshot,
    parse_sa_quant_payload,
    score_sa_quant_payload,
    score_sa_quant_snapshot,
)


AS_OF = "2026-03-02T00:00:00Z"


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        if not self._outcomes:
            raise AssertionError("No more fake outcomes configured")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _init_test_db(tmp_path):
    db_path = tmp_path / "sa_quant.db"
    trade_db.init_db(str(db_path))
    return str(db_path)


def test_parse_payload_with_attributes_shape():
    payload = {
        "data": [
            {
                "id": "evt-1",
                "attributes": {
                    "quantRating": "Bullish",
                    "quantScore": 4.5,
                    "sectorRank": 78,
                    "industryRank": 82,
                    "updatedAt": "2026-03-01T00:00:00Z",
                },
            }
        ]
    }
    snap = parse_sa_quant_payload("aapl", payload)
    assert snap.ticker == "AAPL"
    assert snap.rating == "Bullish"
    assert snap.quant_score_raw == 4.5
    assert snap.sector_rank == 78
    assert snap.industry_rank == 82
    assert snap.source_ref == "evt-1"


def test_parse_payload_with_flat_shape():
    payload = {
        "quant_rating": "Very Bearish",
        "quant_score": 1.5,
        "updated_at": "2026-03-01",
    }
    snap = parse_sa_quant_payload("msft", payload)
    assert snap.ticker == "MSFT"
    assert snap.rating == "Very Bearish"
    assert snap.quant_score_raw == 1.5


def test_score_snapshot_from_text_and_numeric():
    snap = SAQuantSnapshot(ticker="SPY", rating="Very Bullish", quant_score_raw=4.8)
    score = score_sa_quant_snapshot(snap, as_of=AS_OF)
    assert isinstance(score, LayerScore)
    assert score.layer_id == LayerId.L8_SA_QUANT
    assert score.ticker == "SPY"
    assert 0.0 <= score.score <= 100.0
    assert score.score > 80.0
    assert score.confidence == pytest.approx(0.95)


def test_score_payload_handles_rating_only():
    payload = {"data": [{"attributes": {"quantRating": "Neutral"}}]}
    score = score_sa_quant_payload("QQQ", payload=payload, as_of=AS_OF)
    assert score.score == pytest.approx(50.0)
    assert score.details["numeric_score"] is None


def test_client_retries_on_transient_http_error():
    session = FakeSession(
        outcomes=[
            DummyResponse(503, {"error": "busy"}),
            DummyResponse(
                200,
                {"data": [{"attributes": {"quantRating": "Bullish", "quantScore": 4.2}}]},
            ),
        ]
    )
    sleep_calls = []
    client = SAQuantClient(
        config=SAQuantClientConfig(api_key="key", max_retries=2, backoff_seconds=0.01),
        session=session,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    score = client.fetch_layer_score("AAPL", as_of=AS_OF)
    assert score.layer_id == LayerId.L8_SA_QUANT
    assert len(session.calls) == 2
    assert sleep_calls == [0.01]


def test_client_retries_on_request_exception_then_succeeds():
    session = FakeSession(
        outcomes=[
            requests.Timeout("timeout"),
            DummyResponse(200, {"data": [{"attributes": {"quantRating": "Neutral"}}]}),
        ]
    )
    client = SAQuantClient(
        config=SAQuantClientConfig(api_key="key", max_retries=1, backoff_seconds=0.0),
        session=session,
        sleep_fn=lambda _: None,
    )
    score = client.fetch_layer_score("MSFT", as_of=AS_OF)
    assert score.score == pytest.approx(50.0)
    assert len(session.calls) == 2


def test_client_does_not_retry_on_http_400():
    session = FakeSession(outcomes=[DummyResponse(400, {"error": "bad request"})])
    client = SAQuantClient(
        config=SAQuantClientConfig(api_key="key", max_retries=3, backoff_seconds=0.0),
        session=session,
        sleep_fn=lambda _: None,
    )

    with pytest.raises(SAQuantClientError) as exc:
        client.fetch_payload("AAPL")

    assert exc.value.retryable is False
    assert exc.value.status_code == 400
    assert len(session.calls) == 1


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("SA_RAPIDAPI_KEY", raising=False)
    client = SAQuantClient(
        config=SAQuantClientConfig(api_key="", max_retries=0),
        session=FakeSession(outcomes=[]),
        sleep_fn=lambda _: None,
    )
    with pytest.raises(SAQuantClientError):
        client.fetch_payload("AAPL")


class _StubClient:
    def __init__(self, fail_tickers=None):
        self.fail_tickers = set(fail_tickers or [])

    def fetch_layer_score(self, ticker: str, as_of: str) -> LayerScore:
        if ticker in self.fail_tickers:
            raise RuntimeError(f"failed for {ticker}")
        return LayerScore(
            layer_id=LayerId.L8_SA_QUANT,
            ticker=ticker,
            score=84.0,
            as_of=as_of,
            source="sa-quant-rapidapi",
            provenance_ref=f"sa-quant-{ticker}-test",
            confidence=0.9,
            details={"rating": "Bullish", "quant_score_raw": 4.2},
        )


def test_job_runner_persists_scores_and_marks_completed(tmp_path):
    db_path = _init_test_db(tmp_path)
    runner = SAQuantJobRunner(
        client=_StubClient(),
        db_path=db_path,
        now_fn=lambda: AS_OF,
    )

    summary = runner.run(["aapl", "msft"], as_of=AS_OF, job_id="job123")

    assert summary["job_id"] == "job123"
    assert summary["tickers_total"] == 2
    assert summary["tickers_success"] == 2
    assert summary["tickers_failed"] == 0
    assert set(summary["scores"].keys()) == {"AAPL", "MSFT"}

    jobs = trade_db.get_jobs(limit=10, db_path=db_path)
    assert jobs[0]["id"] == "job123"
    assert jobs[0]["status"] == "completed"
    payload = json.loads(jobs[0]["result"])
    assert payload["tickers_success"] == 2

    events = trade_db.get_research_events(limit=10, db_path=db_path)
    assert len(events) == 2
    assert {row["symbol"] for row in events} == {"AAPL", "MSFT"}


def test_job_runner_marks_failed_when_all_tickers_fail(tmp_path):
    db_path = _init_test_db(tmp_path)
    runner = SAQuantJobRunner(
        client=_StubClient(fail_tickers={"AAPL", "MSFT"}),
        db_path=db_path,
        now_fn=lambda: AS_OF,
    )

    summary = runner.run(["AAPL", "MSFT"], as_of=AS_OF, job_id="job999")

    assert summary["tickers_success"] == 0
    assert summary["tickers_failed"] == 2
    assert set(summary["failures"].keys()) == {"AAPL", "MSFT"}

    jobs = trade_db.get_jobs(limit=10, db_path=db_path)
    assert jobs[0]["id"] == "job999"
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error"]
