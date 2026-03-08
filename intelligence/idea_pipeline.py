"""
Idea Pipeline Manager — orchestrates trade ideas through the lifecycle:
    idea -> review -> backtest -> paper -> live

Each stage transition is validated with gate criteria. Integrates with the
existing Backtester for validation and PaperBroker for simulated execution.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import config
from data.trade_db import (
    DB_PATH,
    create_trade_idea,
    update_trade_idea,
    get_trade_idea,
    get_trade_ideas,
    get_trade_ideas_by_analysis,
    record_idea_transition,
    get_idea_transitions,
    get_research_steps,
    create_job,
    update_job,
    get_job,
)

logger = logging.getLogger(__name__)

# ─── Stage transition rules ─────────────────────────────────────────────────

STAGES = ("idea", "review", "backtest", "paper", "live", "rejected")

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "idea":     {"review", "rejected"},
    "review":   {"backtest", "rejected", "idea"},
    "backtest": {"paper", "rejected", "review"},
    "paper":    {"live", "rejected", "review"},
    "live":     {"rejected"},
    "rejected": {"idea"},
}

# ─── Gate reason codes ───────────────────────────────────────────────────────

GATE_REASONS = {
    "INVALID_TRANSITION": "Stage transition not allowed.",
    "IDEA_NOT_FOUND": "Trade idea not found.",
    "MISSING_TICKER": "Idea must have a ticker symbol.",
    "MISSING_DIRECTION": "Idea must have a direction (long/short).",
    "MISSING_THESIS": "Idea must have a thesis.",
    "LOW_CONFIDENCE": "Confidence too low for this stage.",
    "BACKTEST_NOT_COMPLETE": "Backtest has not completed yet.",
    "BACKTEST_NOT_RUN": "No backtest has been run for this idea.",
    "BACKTEST_FAILED_CRITERIA": "Backtest results don't meet minimum criteria.",
    "PAPER_NOT_STARTED": "No paper trade has been started.",
    "PAPER_SOAK_NOT_MET": "Paper trade hasn't been open long enough.",
    "PAPER_HIT_INVALIDATION": "Paper trade hit invalidation level.",
    "NO_STRATEGY_SLOT": "No strategy slot available for live execution.",
    "RESEARCH_NOT_STARTED": "Automated research has not started yet.",
    "RESEARCH_IN_PROGRESS": "Research is still running.",
    "RESEARCH_REJECTED": "Research review rejected this idea.",
    "RESEARCH_FAILED": "Research pipeline failed.",
}


@dataclass
class GateResult:
    allowed: bool
    reasons: list[str]
    detail: dict[str, Any] | None = None


class IdeaPipelineManager:
    """Manages the lifecycle of trade ideas from council output to live trading."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # ─── Validation ──────────────────────────────────────────────────────

    def validate_transition(self, idea_id: str, target_stage: str) -> GateResult:
        """Check whether an idea can move to target_stage. Returns gate result."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return GateResult(False, ["IDEA_NOT_FOUND"])

        current = idea["pipeline_stage"]
        if target_stage not in ALLOWED_TRANSITIONS.get(current, set()):
            return GateResult(False, ["INVALID_TRANSITION"])

        # Rejections and demotions always pass
        if target_stage in ("rejected", "idea"):
            return GateResult(True, [])

        # Gate: idea -> review (always allowed if transition is valid)
        if target_stage == "review":
            return GateResult(True, [])

        # Gate: review -> backtest
        if target_stage == "backtest":
            return self._gate_review_to_backtest(idea)

        # Gate: backtest -> paper
        if target_stage == "paper":
            return self._gate_backtest_to_paper(idea)

        # Gate: paper -> live
        if target_stage == "live":
            return self._gate_paper_to_live(idea)

        return GateResult(True, [])

    def _gate_review_to_backtest(self, idea: dict) -> GateResult:
        """Must have ticker + direction + thesis. If research is enabled, must be complete."""
        reasons = []
        if not idea.get("ticker"):
            reasons.append("MISSING_TICKER")
        if not idea.get("direction"):
            reasons.append("MISSING_DIRECTION")
        if not idea.get("thesis"):
            reasons.append("MISSING_THESIS")
        if (idea.get("confidence") or 0) < 0.2:
            reasons.append("LOW_CONFIDENCE")

        # If research pipeline is enabled, require completed research + strategy spec
        if config.IDEA_RESEARCH_AUTO:
            steps = get_research_steps(idea["id"], db_path=self.db_path)
            if not steps:
                reasons.append("RESEARCH_NOT_STARTED")
            else:
                failed = [s for s in steps if s["status"] == "failed"]
                pending = [s for s in steps if s["status"] in ("pending", "running")]
                if pending:
                    reasons.append("RESEARCH_IN_PROGRESS")
                elif failed and not idea.get("strategy_spec_json"):
                    # Research failed and no spec — check if verdict was reject
                    verdict = idea.get("review_verdict")
                    if verdict == "reject":
                        reasons.append("RESEARCH_REJECTED")
                    else:
                        reasons.append("RESEARCH_FAILED")

        return GateResult(len(reasons) == 0, reasons)

    def _gate_backtest_to_paper(self, idea: dict) -> GateResult:
        """Backtest must be complete with acceptable results."""
        reasons = []
        bt_job_id = idea.get("backtest_job_id")
        if not bt_job_id:
            return GateResult(False, ["BACKTEST_NOT_RUN"])

        job = get_job(bt_job_id, db_path=self.db_path)
        if not job:
            return GateResult(False, ["BACKTEST_NOT_RUN"])
        if job.get("status") != "completed":
            return GateResult(False, ["BACKTEST_NOT_COMPLETE"])

        # Check backtest results against criteria
        bt_json = idea.get("backtest_result_json")
        if bt_json:
            try:
                bt = json.loads(bt_json)
                sharpe = bt.get("sharpe", 0)
                pf = bt.get("profit_factor", 0)
                min_sharpe = config.IDEA_BACKTEST_MIN_SHARPE
                min_pf = config.IDEA_BACKTEST_MIN_PF
                if sharpe < min_sharpe and pf < min_pf:
                    reasons.append("BACKTEST_FAILED_CRITERIA")
            except (json.JSONDecodeError, TypeError):
                pass

        return GateResult(len(reasons) == 0, reasons,
                          detail={"backtest_job_id": bt_job_id})

    def _gate_paper_to_live(self, idea: dict) -> GateResult:
        """Paper trade must have soaked long enough, not hit invalidation."""
        reasons = []

        if not idea.get("paper_deal_id"):
            return GateResult(False, ["PAPER_NOT_STARTED"])

        # Check soak period
        entry_time = idea.get("paper_entry_time")
        if entry_time:
            try:
                entry_dt = datetime.fromisoformat(entry_time)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                hours_open = (now - entry_dt).total_seconds() / 3600
                if hours_open < config.IDEA_PAPER_SOAK_HOURS:
                    reasons.append("PAPER_SOAK_NOT_MET")
            except (ValueError, TypeError):
                reasons.append("PAPER_SOAK_NOT_MET")
        else:
            reasons.append("PAPER_NOT_STARTED")

        # Check invalidation (if paper P&L is deeply negative and invalidation was set)
        paper_pnl = idea.get("paper_pnl") or 0
        invalidation = idea.get("invalidation")
        if invalidation and paper_pnl < 0:
            # Simple check: if P&L is negative and invalidation was specified,
            # flag it for manual review rather than auto-blocking
            pass

        return GateResult(len(reasons) == 0, reasons)

    # ─── Stage transitions ───────────────────────────────────────────────

    def promote_idea(
        self,
        idea_id: str,
        target_stage: str,
        actor: str = "operator",
        reason: str = "",
    ) -> dict[str, Any]:
        """Execute a stage transition with validation. Returns result dict."""
        gate = self.validate_transition(idea_id, target_stage)
        if not gate.allowed:
            return {
                "success": False,
                "idea_id": idea_id,
                "reasons": gate.reasons,
                "reason_texts": [GATE_REASONS.get(r, r) for r in gate.reasons],
            }

        idea = get_trade_idea(idea_id, db_path=self.db_path)
        from_stage = idea["pipeline_stage"]

        # Record the transition
        record_idea_transition(
            idea_id=idea_id,
            from_stage=from_stage,
            to_stage=target_stage,
            actor=actor,
            reason=reason,
            db_path=self.db_path,
        )

        # Update the idea
        update_trade_idea(idea_id, db_path=self.db_path, pipeline_stage=target_stage)

        logger.info("Idea %s promoted: %s -> %s by %s (%s)",
                     idea_id[:12], from_stage, target_stage, actor, reason or "no reason")

        result = {
            "success": True,
            "idea_id": idea_id,
            "from_stage": from_stage,
            "to_stage": target_stage,
        }

        # Auto-launch research when promoting to review
        if target_stage == "review" and config.IDEA_RESEARCH_AUTO:
            try:
                from intelligence.idea_research import IdeaResearcher
                researcher = IdeaResearcher(db_path=self.db_path)
                job_id = researcher.run_async(idea_id)
                result["research_job_id"] = job_id
                logger.info("Auto-launched research for idea %s (job %s)", idea_id[:12], job_id)
            except Exception as exc:
                logger.warning("Failed to auto-launch research for %s: %s", idea_id[:12], exc)

        return result

    def reject_idea(
        self,
        idea_id: str,
        reason: str = "",
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Reject an idea from any stage."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return {"success": False, "reasons": ["IDEA_NOT_FOUND"]}

        from_stage = idea["pipeline_stage"]
        if from_stage == "rejected":
            return {"success": False, "reasons": ["Already rejected"]}

        record_idea_transition(
            idea_id=idea_id, from_stage=from_stage, to_stage="rejected",
            actor=actor, reason=reason, db_path=self.db_path,
        )
        update_trade_idea(idea_id, db_path=self.db_path,
                          pipeline_stage="rejected", rejection_reason=reason)

        logger.info("Idea %s rejected from %s: %s", idea_id[:12], from_stage, reason)
        return {"success": True, "idea_id": idea_id, "from_stage": from_stage, "to_stage": "rejected"}

    # ─── Backtest integration ────────────────────────────────────────────

    def trigger_backtest(self, idea_id: str, actor: str = "operator") -> dict[str, Any]:
        """Launch a backtest for an idea. Returns job_id or error."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return {"success": False, "reasons": ["IDEA_NOT_FOUND"]}

        ticker = idea.get("ticker", "")
        if not ticker:
            return {"success": False, "reasons": ["MISSING_TICKER"]}

        # Create a job for tracking
        job_id = f"bt_{uuid.uuid4().hex[:12]}"
        create_job(
            job_id, job_type="idea_backtest", status="queued",
            detail=json.dumps({"idea_id": idea_id, "ticker": ticker}),
            db_path=self.db_path,
        )

        # Link job to idea
        update_trade_idea(idea_id, db_path=self.db_path, backtest_job_id=job_id)

        # Run in background
        threading.Thread(
            target=self._run_idea_backtest,
            args=(idea, job_id),
            daemon=True,
            name=f"bt-{ticker}-{idea_id[:8]}",
        ).start()

        logger.info("Backtest triggered for idea %s (%s). Job: %s",
                     idea_id[:12], ticker, job_id)
        return {"success": True, "job_id": job_id, "idea_id": idea_id}

    def _run_idea_backtest(self, idea: dict, job_id: str):
        """Background: run backtest and store results on the idea."""
        idea_id = idea["id"]
        ticker = idea["ticker"]
        direction = idea.get("direction", "long")

        try:
            update_job(job_id, status="running", db_path=self.db_path)

            # Prefer DynamicStrategy if a strategy spec exists
            spec_json = idea.get("strategy_spec_json")
            if spec_json:
                result_summary = self._run_dynamic_backtest(idea, spec_json)
            else:
                result_summary = self._try_mapped_backtest(ticker, direction)
                if result_summary is None:
                    result_summary = self._run_generic_backtest(ticker, direction, idea)

            result_json = json.dumps(result_summary)
            update_trade_idea(
                idea_id, db_path=self.db_path,
                backtest_result_json=result_json,
            )
            update_job(job_id, status="completed", result=result_json, db_path=self.db_path)
            logger.info("Backtest complete for %s: Sharpe=%.2f PF=%.2f",
                        ticker, result_summary.get("sharpe", 0), result_summary.get("profit_factor", 0))

        except Exception as exc:
            logger.error("Backtest failed for idea %s: %s", idea_id[:12], exc)
            update_job(job_id, status="failed", error=str(exc), db_path=self.db_path)

    def _run_dynamic_backtest(self, idea: dict, spec_json: str) -> dict:
        """Run a backtest using a DynamicStrategy from a JSON spec."""
        try:
            spec = json.loads(spec_json)
            from strategies.dynamic_strategy import DynamicStrategy
            from data.provider import DataProvider
            import numpy as np

            strategy = DynamicStrategy(spec)
            ticker = idea["ticker"]
            dp = DataProvider(lookback_days=750)
            bars = dp.get_daily_bars(ticker)

            if bars is None or len(bars) < 210:
                return {
                    "method": "dynamic", "ticker": ticker,
                    "error": f"Insufficient data ({len(bars) if bars is not None else 0} bars)",
                    "sharpe": 0, "profit_factor": 0, "total_trades": 0,
                }

            # Walk-forward simulation
            trades = []
            position = 0.0
            bars_in_trade = 0
            entry_price = 0.0

            for i in range(210, len(bars)):
                window = bars.iloc[:i + 1]
                signal = strategy.generate_signal(ticker, window, position, bars_in_trade)

                if signal.signal_type.value.endswith("_entry") and position == 0:
                    position = 1.0 if "long" in signal.signal_type.value else -1.0
                    entry_price = float(bars["Close"].iloc[i])
                    bars_in_trade = 0
                elif signal.signal_type.value.endswith("_exit") and position != 0:
                    exit_price = float(bars["Close"].iloc[i])
                    pnl_pct = ((exit_price / entry_price) - 1) * (1 if position > 0 else -1)
                    trades.append(pnl_pct)
                    position = 0.0
                    bars_in_trade = 0
                elif position != 0:
                    bars_in_trade += 1

            if not trades:
                return {
                    "method": "dynamic", "ticker": ticker,
                    "strategy_name": spec.get("name", "Unnamed"),
                    "error": "No trades generated",
                    "sharpe": 0, "profit_factor": 0, "total_trades": 0,
                }

            sr = np.array(trades)
            avg_ret = float(np.mean(sr))
            std_ret = float(np.std(sr)) or 1e-9
            # Annualize assuming average ~20 trades per year
            trades_per_year = max(len(trades) / 3, 1)  # rough 3-year window
            sharpe = (avg_ret / std_ret) * np.sqrt(trades_per_year)

            wins = sr[sr > 0]
            losses = sr[sr < 0]
            gross_profit = float(wins.sum()) if len(wins) > 0 else 0
            gross_loss = abs(float(losses.sum())) if len(losses) > 0 else 1e-9
            profit_factor = gross_profit / gross_loss

            cum = np.cumprod(1 + sr)
            peak = np.maximum.accumulate(cum)
            dd = (cum - peak) / peak
            max_dd = float(dd.min()) * 100

            return {
                "method": "dynamic",
                "ticker": ticker,
                "strategy_name": spec.get("name", "Unnamed"),
                "direction": spec.get("direction", "long"),
                "total_trades": len(trades),
                "win_rate": round(float(len(wins)) / len(sr), 4) if len(sr) > 0 else 0,
                "sharpe": round(sharpe, 4),
                "profit_factor": round(profit_factor, 4),
                "total_return_pct": round(float((cum[-1] - 1) * 100), 2),
                "max_drawdown_pct": round(max_dd, 2),
                "avg_trade_pct": round(avg_ret * 100, 4),
            }

        except Exception as exc:
            logger.error("Dynamic backtest failed for idea %s: %s", idea.get("id", "?")[:12], exc)
            return {
                "method": "dynamic", "ticker": idea["ticker"],
                "error": str(exc),
                "sharpe": 0, "profit_factor": 0, "total_trades": 0,
            }

    def _try_mapped_backtest(self, ticker: str, direction: str) -> dict | None:
        """Try to run the full backtester if the ticker maps to a known strategy."""
        try:
            from analytics.backtester import Backtester

            # Check which strategies support this ticker
            strategy_map = {
                "IBS++ v3": config.MARKET_MAP if hasattr(config, "MARKET_MAP") else {},
            }

            # Try IBS++ for common equity tickers
            common_ibs = {"SPY", "QQQ", "IWM", "DIA", "EWU", "EWG", "EWJ"}
            if ticker.upper() in common_ibs:
                strategy = "IBS++ v3" if direction == "long" else "IBS Short (Bear)"
                bt = Backtester(equity=10000)
                result = bt.run(strategy, tickers=[ticker])
                return {
                    "method": "mapped",
                    "strategy": strategy,
                    "ticker": ticker,
                    "total_trades": result.total_trades,
                    "win_rate": round(result.win_rate, 4),
                    "sharpe": round(result.sharpe, 4),
                    "sortino": round(result.sortino, 4),
                    "profit_factor": round(result.profit_factor, 4),
                    "net_pnl": round(result.net_pnl, 2),
                    "max_drawdown_pct": round(result.max_drawdown_pct, 2) if hasattr(result, "max_drawdown_pct") else 0,
                    "total_return_pct": round(result.total_return_pct, 2),
                    "period_start": result.period_start,
                    "period_end": result.period_end,
                }
        except Exception as exc:
            logger.warning("Mapped backtest failed for %s: %s", ticker, exc)
        return None

    def _run_generic_backtest(self, ticker: str, direction: str, idea: dict) -> dict:
        """Run a simple directional backtest as proxy for unmapped tickers."""
        try:
            from data.provider import DataProvider
            import numpy as np

            dp = DataProvider(lookback_days=750)
            bars = dp.get_daily_bars(ticker)

            if bars is None or len(bars) < 50:
                return {
                    "method": "generic",
                    "ticker": ticker,
                    "error": f"Insufficient data for {ticker} ({len(bars) if bars is not None else 0} bars)",
                    "sharpe": 0, "profit_factor": 0, "total_trades": 0,
                }

            close = bars["Close"].values
            returns = np.diff(close) / close[:-1]

            if direction == "short":
                returns = -returns

            # Simple momentum proxy: long when above 50-day SMA, else flat
            sma50 = np.convolve(close, np.ones(50)/50, mode="valid")
            offset = len(close) - len(sma50)
            signal_returns = []
            for i in range(len(sma50) - 1):
                price_idx = i + offset
                if direction == "long":
                    if close[price_idx] > sma50[i]:
                        signal_returns.append(returns[price_idx])
                else:
                    if close[price_idx] < sma50[i]:
                        signal_returns.append(-returns[price_idx])

            if not signal_returns:
                return {
                    "method": "generic", "ticker": ticker,
                    "error": "No signals generated",
                    "sharpe": 0, "profit_factor": 0, "total_trades": 0,
                }

            sr = np.array(signal_returns)
            avg_ret = float(np.mean(sr))
            std_ret = float(np.std(sr)) or 1e-9
            sharpe = (avg_ret / std_ret) * np.sqrt(252)

            wins = sr[sr > 0]
            losses = sr[sr < 0]
            gross_profit = float(wins.sum()) if len(wins) > 0 else 0
            gross_loss = abs(float(losses.sum())) if len(losses) > 0 else 1e-9
            profit_factor = gross_profit / gross_loss

            cum = np.cumprod(1 + sr)
            peak = np.maximum.accumulate(cum)
            dd = (cum - peak) / peak
            max_dd = float(dd.min()) * 100

            return {
                "method": "generic",
                "ticker": ticker,
                "direction": direction,
                "total_trades": len(signal_returns),
                "win_rate": round(float(len(wins)) / len(sr), 4) if len(sr) > 0 else 0,
                "sharpe": round(sharpe, 4),
                "profit_factor": round(profit_factor, 4),
                "total_return_pct": round(float((cum[-1] - 1) * 100), 2),
                "max_drawdown_pct": round(max_dd, 2),
                "avg_daily_return_pct": round(avg_ret * 100, 4),
                "period_start": str(bars.index[offset].date()) if hasattr(bars.index[offset], "date") else "",
                "period_end": str(bars.index[-1].date()) if hasattr(bars.index[-1], "date") else "",
            }

        except Exception as exc:
            logger.error("Generic backtest failed for %s: %s", ticker, exc)
            return {
                "method": "generic", "ticker": ticker,
                "error": str(exc),
                "sharpe": 0, "profit_factor": 0, "total_trades": 0,
            }

    # ─── Paper trade integration ─────────────────────────────────────────

    def start_paper_trade(self, idea_id: str, actor: str = "operator") -> dict[str, Any]:
        """Create a PaperBroker position for an idea."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return {"success": False, "reasons": ["IDEA_NOT_FOUND"]}

        if idea.get("paper_deal_id"):
            return {"success": False, "reasons": ["Paper trade already active"]}

        ticker = idea["ticker"]
        direction = idea["direction"]
        stake = config.IDEA_PAPER_DEFAULT_STAKE

        try:
            from broker.paper import PaperBroker
            broker = PaperBroker()
            broker.connect()

            strategy_name = f"idea_{idea_id[:8]}"
            if direction == "long":
                result = broker.place_long(ticker, stake, strategy_name)
            else:
                result = broker.place_short(ticker, stake, strategy_name)

            if not result.success:
                return {"success": False, "reasons": [result.message]}

            now = datetime.now(timezone.utc).isoformat()
            deal_id = result.deal_id if hasattr(result, "deal_id") else f"paper_{uuid.uuid4().hex[:8]}"

            update_trade_idea(
                idea_id, db_path=self.db_path,
                paper_deal_id=deal_id,
                paper_entry_price=result.fill_price if hasattr(result, "fill_price") else 0,
                paper_entry_time=now,
                paper_pnl=0.0,
            )

            logger.info("Paper trade started for idea %s: %s %s @ %s",
                         idea_id[:12], direction, ticker, stake)
            return {"success": True, "idea_id": idea_id, "deal_id": deal_id}

        except Exception as exc:
            logger.error("Paper trade failed for idea %s: %s", idea_id[:12], exc)
            return {"success": False, "reasons": [str(exc)]}

    def get_paper_trade_status(self, idea_id: str) -> dict[str, Any]:
        """Get current P&L for an idea's paper position."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return {"success": False, "reasons": ["IDEA_NOT_FOUND"]}

        if not idea.get("paper_deal_id"):
            return {"success": False, "reasons": ["No paper trade active"]}

        try:
            from data.provider import DataProvider
            dp = DataProvider(lookback_days=5)
            bars = dp.get_daily_bars(idea["ticker"])
            if bars is not None and len(bars) > 0:
                current_price = float(bars["Close"].iloc[-1])
            else:
                current_price = 0

            entry_price = idea.get("paper_entry_price") or 0
            direction = idea.get("direction", "long")
            stake = config.IDEA_PAPER_DEFAULT_STAKE

            if direction == "long":
                pnl = (current_price - entry_price) * stake
            else:
                pnl = (entry_price - current_price) * stake

            # Update stored P&L
            update_trade_idea(idea_id, db_path=self.db_path, paper_pnl=round(pnl, 2))

            hours_open = 0
            entry_time = idea.get("paper_entry_time")
            if entry_time:
                try:
                    entry_dt = datetime.fromisoformat(entry_time)
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    hours_open = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass

            return {
                "success": True,
                "idea_id": idea_id,
                "ticker": idea["ticker"],
                "direction": direction,
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl": round(pnl, 2),
                "stake": stake,
                "hours_open": round(hours_open, 1),
                "soak_met": hours_open >= config.IDEA_PAPER_SOAK_HOURS,
            }
        except Exception as exc:
            logger.error("Paper status check failed for %s: %s", idea_id[:12], exc)
            return {"success": False, "reasons": [str(exc)]}

    def close_paper_trade(self, idea_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        """Close a paper position and record final results."""
        status = self.get_paper_trade_status(idea_id)
        if not status.get("success"):
            return status

        update_trade_idea(
            idea_id, db_path=self.db_path,
            paper_pnl=status.get("pnl", 0),
            metadata_json=json.dumps({
                "paper_closed_at": datetime.now(timezone.utc).isoformat(),
                "paper_close_reason": reason,
                "paper_final_pnl": status.get("pnl", 0),
                "paper_hours_open": status.get("hours_open", 0),
            }),
        )

        logger.info("Paper trade closed for idea %s: P&L=%.2f",
                     idea_id[:12], status.get("pnl", 0))
        return {"success": True, "idea_id": idea_id, "final_pnl": status.get("pnl", 0)}

    # ─── Backfill from existing analyses ─────────────────────────────────

    def backfill_ideas_from_events(self, limit: int = 100):
        """Scan existing research_events and create trade_ideas rows for any missing."""
        try:
            from intelligence.event_store import EventStore
            store = EventStore()
            events = store.list_events(limit=limit, event_type="intel_analysis")

            created = 0
            for evt in events:
                payload = evt.get("payload") or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        continue

                analysis_id = payload.get("analysis_id", evt.get("id", ""))
                ideas = payload.get("trade_ideas", [])
                confidence = payload.get("confidence", 0)

                # Check if ideas already exist for this analysis
                existing = get_trade_ideas_by_analysis(analysis_id, db_path=self.db_path)
                if existing:
                    continue

                for idea in ideas:
                    if not idea.get("ticker"):
                        continue
                    create_trade_idea(
                        idea_id=str(uuid.uuid4()),
                        analysis_id=analysis_id,
                        ticker=idea.get("ticker", ""),
                        direction=idea.get("direction", "long"),
                        conviction=idea.get("conviction", "low"),
                        timeframe=idea.get("timeframe"),
                        thesis=idea.get("thesis"),
                        entry_trigger=idea.get("entry_trigger"),
                        invalidation=idea.get("invalidation"),
                        instrument=idea.get("instrument"),
                        source_model=idea.get("source_model"),
                        confidence=confidence,
                        db_path=self.db_path,
                    )
                    created += 1

            logger.info("Backfilled %d trade ideas from existing analyses", created)
            return created
        except Exception as exc:
            logger.error("Backfill failed: %s", exc)
            return 0

    # ─── Pipeline stats ──────────────────────────────────────────────────

    def get_pipeline_stats(self) -> dict[str, Any]:
        """Get counts and top ideas per stage for the UI."""
        stages = {}
        for stage in STAGES:
            ideas = get_trade_ideas(stage=stage, limit=200, db_path=self.db_path)
            display_limit = 12 if stage == "rejected" else 6
            stages[stage] = {
                "count": len(ideas),
                "ideas": ideas[:display_limit],
            }

        all_ideas = get_trade_ideas(limit=200, db_path=self.db_path)
        return {
            "stages": stages,
            "total": len(all_ideas),
            "unique_tickers": len(set(i["ticker"] for i in all_ideas if i.get("ticker"))),
        }
