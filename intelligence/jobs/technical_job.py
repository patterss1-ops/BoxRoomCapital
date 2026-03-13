"""L7 Technical Overlay job runner.

Uses yfinance daily bars to compute technical indicators and score
using the existing technical overlay scorer.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.event_store import EventRecord, EventStore

logger = logging.getLogger(__name__)


from utils.datetime_utils import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class TechnicalJobConfig:
    job_type: str = "technical_ingest"
    event_type: str = "signal_layer"
    source: str = "yfinance-technical"


def _fetch_technical_snapshot(ticker: str) -> Optional[dict]:
    """Fetch price data from yfinance and compute technical indicators."""
    try:
        import yfinance as yf
        import numpy as np

        data = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
        if data.empty or len(data) < 200:
            data = yf.download(ticker, period="1y", progress=False)
        if data.empty or len(data) < 50:
            return None

        # yfinance >=0.2.31 returns MultiIndex columns for single tickers;
        # flatten to simple column names so downstream .iloc[-1] yields scalars.
        if isinstance(data.columns, __import__("pandas").MultiIndex):
            data.columns = data.columns.get_level_values(0)

        close = data["Close"].iloc[-1]
        if hasattr(close, "item"):
            close = close.item()

        # SMA 50 and 200
        sma_50 = float(data["Close"].rolling(50).mean().iloc[-1])
        sma_200 = float(data["Close"].rolling(200).mean().iloc[-1]) if len(data) >= 200 else float(sma_50)

        # EMA 20
        ema_20 = float(data["Close"].ewm(span=20).mean().iloc[-1])

        # RSI 14
        delta = data["Close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean().iloc[-1]
        if hasattr(gain, "item"):
            gain = gain.item()
        if hasattr(loss, "item"):
            loss = loss.item()
        rsi_14 = 100.0 - (100.0 / (1.0 + gain / loss)) if loss > 0 else 100.0

        # Volume
        volume = float(data["Volume"].iloc[-1])
        avg_volume_20d = float(data["Volume"].rolling(20).mean().iloc[-1])

        # ATR 14
        high = data["High"]
        low = data["Low"]
        prev_close = data["Close"].shift(1)
        tr = (high - low).combine(abs(high - prev_close), max).combine(abs(low - prev_close), max)
        atr_14 = float(tr.rolling(14).mean().iloc[-1])

        return {
            "close": float(close),
            "sma_50": sma_50,
            "sma_200": sma_200,
            "rsi_14": rsi_14,
            "volume": volume,
            "avg_volume_20d": avg_volume_20d,
            "ema_20": ema_20,
            "atr_14": atr_14,
        }
    except Exception as exc:
        logger.warning("Technical snapshot failed for %s: %s", ticker, exc)
        return None


class TechnicalJobRunner:
    """Batch job runner for L7 Technical Overlay signal."""

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: TechnicalJobConfig = TechnicalJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run technical overlay scoring for a ticker batch."""
        from app.signal.layers.technical_overlay import TechnicalSnapshot, score_technical

        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        log_event(category="RESEARCH", headline="Technical job started",
                  detail=f"job_id={run_id}, tickers={len(deduped)}", strategy="signal_engine",
                  db_path=self.db_path)

        for ticker in deduped:
            try:
                snap_data = _fetch_technical_snapshot(ticker)
                if not snap_data:
                    scores[ticker] = {"score": 0.0}
                    successes += 1
                    continue

                snapshot = TechnicalSnapshot(
                    ticker=ticker,
                    snapshot_date=snap_data.get("snapshot_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                    close=snap_data["close"],
                    sma_50=snap_data["sma_50"],
                    sma_200=snap_data["sma_200"],
                    rsi_14=snap_data["rsi_14"],
                    volume=snap_data["volume"],
                    avg_volume_20d=snap_data["avg_volume_20d"],
                    ema_20=snap_data["ema_20"],
                    atr_14=snap_data["atr_14"],
                )

                layer_score = score_technical(
                    ticker=ticker, snapshots=[snapshot], as_of=run_at,
                )

                self.event_store.write_event(EventRecord(
                    event_type=self.config.event_type,
                    source=self.config.source,
                    source_ref=layer_score.provenance_ref or "",
                    retrieved_at=run_at,
                    event_timestamp=run_at,
                    symbol=ticker,
                    headline="L7 Technical score",
                    detail=f"ticker={ticker}, score={layer_score.score}",
                    confidence=layer_score.confidence,
                    provenance_descriptor={"layer_id": "l7_technical", "ticker": ticker, "as_of": run_at},
                    payload=layer_score.to_dict(),
                ))
                scores[ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:
                failures[ticker] = str(exc)
                log_event(category="ERROR", headline="Technical ticker failed",
                          detail=f"job_id={run_id}, ticker={ticker}, error={exc}",
                          strategy="signal_engine", db_path=self.db_path)

        summary = {
            "job_id": run_id, "as_of": run_at, "tickers_total": len(deduped),
            "tickers_success": successes, "tickers_failed": len(failures),
            "scores": scores, "failures": failures,
        }
        status = "completed" if successes > 0 or not deduped else "failed"
        update_job(job_id=run_id, status=status, detail=f"success={successes}, failed={len(failures)}",
                   result=json.dumps(summary, sort_keys=True),
                   error=json.dumps(failures) if failures and not successes else None,
                   db_path=self.db_path)
        log_event(category="RESEARCH", headline="Technical job completed",
                  detail=f"job_id={run_id}, success={successes}, failed={len(failures)}",
                  strategy="signal_engine", db_path=self.db_path)
        return summary
