"""
Automated Idea Research Engine — runs 4 steps when an idea is promoted to review:

  2a: Hypothesis Refinement (Grok — cheap, real-time knowledge)
  2b: Evidence Gathering (parallel data fetch, no LLM)
  2c: Critical Review (Claude Opus — best reasoning)
  2d: Strategy Specification (GPT-5.4 — strong structured output)

Cost per idea: ~$0.15-0.50.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import config
from data.trade_db import (
    DB_PATH,
    create_job,
    create_research_step,
    get_research_steps,
    get_trade_idea,
    update_job,
    update_research_step,
    update_trade_idea,
)
from intelligence.intel_pipeline import (
    FUND_CONTEXT,
    _calc_cost,
    _query_anthropic,
    _query_grok,
    _query_openai,
    log_council_cost,
)
from strategies.dynamic_strategy import validate_strategy_spec

logger = logging.getLogger(__name__)


# ── Prompts ──────────────────────────────────────────────────────────────────

HYPOTHESIS_PROMPT = """\
You are refining a raw trade idea into a specific, testable trading hypothesis.

RAW IDEA:
Ticker: {ticker}
Direction: {direction}
Thesis: {thesis}
Entry trigger: {entry_trigger}
Invalidation: {invalidation}
Timeframe: {timeframe}
Conviction: {conviction}
Confidence: {confidence}

Turn this vague thesis into SPECIFIC, TESTABLE trading rules. Consider:
1. What exact entry conditions can be mechanically checked? (price levels, indicator values)
2. What exact exit conditions? (target, stop, time-based)
3. What is the holding period in trading days?
4. What specific data would confirm or invalidate this thesis?
5. What is the ideal universe of instruments?

Respond with ONLY a JSON object:
{{"refined_thesis": "<specific testable hypothesis>",
"entry_conditions": ["<specific condition 1>", "<specific condition 2>"],
"exit_conditions": ["<specific condition 1>", "<specific condition 2>"],
"holding_period_days": <int>,
"universe": ["<TICKER1>"],
"invalidation_criteria": ["<specific criteria>"],
"testable_predictions": ["<prediction 1>"],
"data_needed": ["<data type 1>"],
"key_indicators": ["<indicator with params>"]}}"""

CRITICAL_REVIEW_PROMPT = """\
You are conducting a rigorous critical review of a trade hypothesis.

HYPOTHESIS:
{hypothesis_json}

EVIDENCE GATHERED:
{evidence_json}

Evaluate this trade idea with brutal honesty. Consider:
1. Does the quantitative evidence SUPPORT or CONTRADICT the thesis?
2. What risks are NOT captured in the data?
3. Is this edge likely to be novel, or already priced in?
4. What is the base rate for similar trades — do they actually make money?
5. Are the entry/exit conditions specific enough to backtest?
6. Does the market regime (VIX, macro) favor or hinder this trade?

Score on 4 axes (0-10 each):
- edge_clarity: How clear and specific is the supposed edge?
- timing: Is the timing/entry logic sound?
- risk_reward: Is the risk/reward attractive?
- data_support: Does available data support the thesis?

VERDICT must be "proceed" or "reject".
"proceed" = average score >= {min_score} AND no critical red flags.
"reject" = weak evidence, unclear edge, or significant red flags.

Respond with ONLY a JSON object:
{{"scores": {{
    "edge_clarity": <0-10>,
    "timing": <0-10>,
    "risk_reward": <0-10>,
    "data_support": <0-10>
}},
"average_score": <float>,
"verdict": "proceed|reject",
"verdict_reasoning": "<2-3 sentence explanation>",
"strengths": ["<strength 1>"],
"weaknesses": ["<weakness 1>"],
"risks_not_captured": ["<risk 1>"],
"recommendation": "<specific recommendation for next steps>"}}"""

STRATEGY_SPEC_PROMPT = """\
You are generating a formal, executable strategy specification for backtesting.

HYPOTHESIS:
{hypothesis_json}

EVIDENCE SUMMARY:
{evidence_summary}

REVIEW VERDICT:
{review_json}

Generate a JSON strategy specification using ONLY these available indicators:
- ibs (no period needed)
- rsi (period: 2-14)
- ema (period: 10-200)
- sma (period: 10-200)
- atr (period: 7-20)
- adx (period: 10-20)
- donchian_upper (period: 5-50)
- donchian_lower (period: 5-50)
- consecutive_down_days (no period needed)
- close, open, high, low, volume (no period needed)

Available operators: <, >, <=, >=, crosses_above, crosses_below

Rules format — entry_rules use AND logic (all must pass), exit_rules use OR logic (any triggers):
Entry rule example: {{"indicator": "rsi", "period": 2, "operator": "<", "value": 30}}
Reference example: {{"indicator": "close", "operator": ">", "reference": "ema", "ref_period": 200}}
Max hold example: {{"type": "max_hold", "bars": 5}}

{retry_context}

Respond with ONLY a JSON object:
{{"name": "<descriptive strategy name>",
"direction": "long|short",
"entry_rules": [<rule objects>],
"exit_rules": [<rule objects>],
"stop_loss_atr_multiple": <float or null>,
"universe": ["<TICKER>"],
"vix_filter": {{"enabled": true|false, "max_level": <float>}}}}"""


# ── Research Engine ──────────────────────────────────────────────────────────

class IdeaResearcher:
    """Runs the 4-step automated research pipeline for a trade idea."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def run(self, idea_id: str) -> dict[str, Any]:
        """Run all 4 research steps synchronously. Returns summary dict."""
        idea = get_trade_idea(idea_id, db_path=self.db_path)
        if not idea:
            return {"success": False, "error": "Idea not found"}

        logger.info("Starting research for idea %s (%s %s)",
                     idea_id[:12], idea["direction"], idea["ticker"])

        results = {}
        total_cost = 0.0

        # Step 2a: Hypothesis refinement
        try:
            step_result = self._refine_hypothesis(idea)
            results["hypothesis"] = step_result
            total_cost += step_result.get("cost_usd", 0)
        except Exception as exc:
            logger.error("Step 2a failed for %s: %s", idea_id[:12], exc)
            return {"success": False, "error": f"Hypothesis refinement failed: {exc}",
                    "step_failed": "hypothesis", "results": results}

        # Step 2b: Evidence gathering
        try:
            step_result = self._gather_evidence(idea, results["hypothesis"])
            results["evidence"] = step_result
        except Exception as exc:
            logger.error("Step 2b failed for %s: %s", idea_id[:12], exc)
            return {"success": False, "error": f"Evidence gathering failed: {exc}",
                    "step_failed": "evidence", "results": results}

        # Step 2c: Critical review
        try:
            step_result = self._critical_review(idea, results["hypothesis"], results["evidence"])
            results["review"] = step_result
            total_cost += step_result.get("cost_usd", 0)

            # Update idea with review results
            avg = step_result.get("output", {}).get("average_score", 0)
            verdict = step_result.get("output", {}).get("verdict", "reject")
            update_trade_idea(
                idea_id, db_path=self.db_path,
                review_score=avg,
                review_verdict=verdict,
            )
        except Exception as exc:
            logger.error("Step 2c failed for %s: %s", idea_id[:12], exc)
            return {"success": False, "error": f"Critical review failed: {exc}",
                    "step_failed": "review", "results": results}

        # Step 2d: Strategy spec (only if verdict=proceed)
        verdict = results["review"].get("output", {}).get("verdict", "reject")
        if verdict == "proceed":
            try:
                step_result = self._generate_strategy_spec(
                    idea, results["hypothesis"], results["evidence"], results["review"]
                )
                results["strategy_spec"] = step_result
                total_cost += step_result.get("cost_usd", 0)

                spec = step_result.get("output")
                if spec:
                    update_trade_idea(
                        idea_id, db_path=self.db_path,
                        strategy_spec_json=json.dumps(spec),
                    )
            except Exception as exc:
                logger.error("Step 2d failed for %s: %s", idea_id[:12], exc)
                results["strategy_spec"] = {"error": str(exc)}
        else:
            logger.info("Idea %s rejected at review (score=%.1f). Skipping strategy spec.",
                        idea_id[:12], results["review"].get("output", {}).get("average_score", 0))

        logger.info("Research complete for %s. Verdict=%s, total cost=$%.4f",
                     idea_id[:12], verdict, total_cost)

        return {
            "success": True,
            "idea_id": idea_id,
            "verdict": verdict,
            "review_score": results["review"].get("output", {}).get("average_score", 0),
            "total_cost_usd": round(total_cost, 4),
            "results": results,
        }

    def run_async(self, idea_id: str, job_id: str | None = None) -> str:
        """Launch research in a background thread. Returns job_id."""
        import uuid
        if not job_id:
            job_id = f"research_{uuid.uuid4().hex[:12]}"

        create_job(
            job_id, job_type="idea_research", status="queued",
            detail=json.dumps({"idea_id": idea_id}),
            db_path=self.db_path,
        )
        update_trade_idea(idea_id, db_path=self.db_path, research_job_id=job_id)

        def _worker():
            try:
                update_job(job_id, status="running", db_path=self.db_path)
                result = self.run(idea_id)
                status = "completed" if result.get("success") else "failed"
                update_job(
                    job_id, status=status,
                    result=json.dumps({
                        "verdict": result.get("verdict"),
                        "review_score": result.get("review_score"),
                        "total_cost_usd": result.get("total_cost_usd"),
                    }),
                    error=result.get("error"),
                    db_path=self.db_path,
                )

                # Auto-promote or auto-reject based on verdict
                if result.get("success"):
                    self._auto_transition(idea_id, result)

            except Exception as exc:
                logger.error("Research job %s failed: %s", job_id, exc)
                update_job(job_id, status="failed", error=str(exc), db_path=self.db_path)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"research-{idea_id[:8]}",
        ).start()

        return job_id

    # ── Step 2a: Hypothesis Refinement ───────────────────────────────────

    def _refine_hypothesis(self, idea: dict) -> dict:
        step_id = create_research_step(
            idea["id"], "hypothesis", status="running",
            input_json=json.dumps({"ticker": idea["ticker"], "thesis": idea.get("thesis")}),
            db_path=self.db_path,
        )
        started = datetime.now(timezone.utc).isoformat()
        update_research_step(step_id, started_at=started, db_path=self.db_path)

        try:
            prompt = HYPOTHESIS_PROMPT.format(
                ticker=idea["ticker"],
                direction=idea["direction"],
                thesis=idea.get("thesis") or "No thesis provided",
                entry_trigger=idea.get("entry_trigger") or "Not specified",
                invalidation=idea.get("invalidation") or "Not specified",
                timeframe=idea.get("timeframe") or "Not specified",
                conviction=idea.get("conviction", "low"),
                confidence=idea.get("confidence", 0),
            )

            model_choice = config.IDEA_RESEARCH_MODEL_HYPOTHESIS
            result = self._call_model(model_choice, prompt, "hypothesis")

            output = result.get("parsed", {})
            cost = result.get("cost_usd", 0)

            update_research_step(
                step_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                model_used=result.get("model_key", model_choice),
                cost_usd=cost,
                output_json=json.dumps(output),
                db_path=self.db_path,
            )

            log_council_cost(
                idea["id"], result.get("model", model_choice),
                result.get("model_key", model_choice), 0,
                result.get("input_tokens", 0), result.get("output_tokens", 0),
                cost, result.get("duration_s", 0),
            )

            return {"output": output, "cost_usd": cost, "model": result.get("model_key")}

        except Exception as exc:
            update_research_step(
                step_id, status="failed", error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
                db_path=self.db_path,
            )
            raise

    # ── Step 2b: Evidence Gathering ──────────────────────────────────────

    def _gather_evidence(self, idea: dict, hypothesis: dict) -> dict:
        step_id = create_research_step(
            idea["id"], "evidence", status="running",
            db_path=self.db_path,
        )
        started = datetime.now(timezone.utc).isoformat()
        update_research_step(step_id, started_at=started, db_path=self.db_path)

        try:
            ticker = idea["ticker"]
            evidence: Dict[str, Any] = {"ticker": ticker}

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {
                    pool.submit(self._fetch_price_data, ticker): "price_data",
                    pool.submit(self._fetch_macro_snapshot): "macro",
                }

                for future in as_completed(futures, timeout=60):
                    key = futures[future]
                    try:
                        evidence[key] = future.result()
                    except Exception as exc:
                        logger.warning("Evidence fetch '%s' failed: %s", key, exc)
                        evidence[key] = {"error": str(exc)}

            update_research_step(
                step_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                output_json=json.dumps(evidence, default=str),
                db_path=self.db_path,
            )
            return {"output": evidence}

        except Exception as exc:
            update_research_step(
                step_id, status="failed", error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
                db_path=self.db_path,
            )
            raise

    def _fetch_price_data(self, ticker: str) -> dict:
        """Fetch price data and compute key indicators."""
        try:
            from data.provider import (
                DataProvider,
                calc_ibs,
                calc_rsi,
                calc_ema,
                calc_sma,
                calc_atr,
                calc_adx,
            )
            dp = DataProvider(lookback_days=750)
            bars = dp.get_daily_bars(ticker)

            if bars is None or len(bars) < 50:
                return {"error": f"Insufficient data for {ticker}"}

            import numpy as np
            close = bars["Close"]
            returns = close.pct_change().dropna()

            result = {
                "bars_count": len(bars),
                "last_close": float(close.iloc[-1]),
                "last_date": str(bars.index[-1].date()) if hasattr(bars.index[-1], "date") else str(bars.index[-1]),
                "return_1w": float(returns.tail(5).sum()) if len(returns) >= 5 else 0,
                "return_1m": float(returns.tail(21).sum()) if len(returns) >= 21 else 0,
                "return_3m": float(returns.tail(63).sum()) if len(returns) >= 63 else 0,
                "volatility_21d": float(returns.tail(21).std() * np.sqrt(252)) if len(returns) >= 21 else 0,
                "current_ibs": float(calc_ibs(bars).iloc[-1]),
                "current_rsi2": float(calc_rsi(close, 2).iloc[-1]),
                "current_rsi14": float(calc_rsi(close, 14).iloc[-1]),
                "ema200": float(calc_ema(close, 200).iloc[-1]) if len(bars) >= 210 else 0,
                "sma50": float(calc_sma(close, 50).iloc[-1]) if len(bars) >= 60 else 0,
                "atr14": float(calc_atr(bars, 14).iloc[-1]),
                "adx14": float(calc_adx(bars, 14).iloc[-1]),
                "above_ema200": bool(close.iloc[-1] > calc_ema(close, 200).iloc[-1]) if len(bars) >= 210 else None,
                "52w_high": float(close.tail(252).max()) if len(close) >= 252 else float(close.max()),
                "52w_low": float(close.tail(252).min()) if len(close) >= 252 else float(close.min()),
                "pct_from_52w_high": float((close.iloc[-1] / close.tail(252).max() - 1) * 100) if len(close) >= 252 else 0,
            }
            return result
        except Exception as exc:
            return {"error": str(exc)}

    def _fetch_macro_snapshot(self) -> dict:
        """Fetch basic macro context."""
        try:
            from data.provider import DataProvider
            dp = DataProvider(lookback_days=30)

            macro: Dict[str, Any] = {}

            # VIX
            try:
                vix = dp.get_daily_bars("^VIX")
                if vix is not None and len(vix) > 0:
                    macro["vix_close"] = float(vix["Close"].iloc[-1])
                    macro["vix_5d_avg"] = float(vix["Close"].tail(5).mean())
            except Exception:
                pass

            # Treasury proxy
            try:
                tlt = dp.get_daily_bars("TLT")
                if tlt is not None and len(tlt) > 0:
                    macro["tlt_close"] = float(tlt["Close"].iloc[-1])
                    macro["tlt_1w_return"] = float(tlt["Close"].pct_change().tail(5).sum())
            except Exception:
                pass

            return macro
        except Exception as exc:
            return {"error": str(exc)}

    # ── Step 2c: Critical Review ─────────────────────────────────────────

    def _critical_review(self, idea: dict, hypothesis: dict, evidence: dict) -> dict:
        step_id = create_research_step(
            idea["id"], "critical_review", status="running",
            db_path=self.db_path,
        )
        started = datetime.now(timezone.utc).isoformat()
        update_research_step(step_id, started_at=started, db_path=self.db_path)

        try:
            prompt = CRITICAL_REVIEW_PROMPT.format(
                hypothesis_json=json.dumps(hypothesis.get("output", {}), indent=2),
                evidence_json=json.dumps(evidence.get("output", {}), indent=2, default=str),
                min_score=config.IDEA_REVIEW_MIN_SCORE,
            )

            model_choice = config.IDEA_RESEARCH_MODEL_REVIEW
            result = self._call_model(model_choice, prompt, "critical_review")

            output = result.get("parsed", {})
            cost = result.get("cost_usd", 0)

            update_research_step(
                step_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                model_used=result.get("model_key", model_choice),
                cost_usd=cost,
                output_json=json.dumps(output),
                db_path=self.db_path,
            )

            log_council_cost(
                idea["id"], result.get("model", model_choice),
                result.get("model_key", model_choice), 0,
                result.get("input_tokens", 0), result.get("output_tokens", 0),
                cost, result.get("duration_s", 0),
            )

            return {"output": output, "cost_usd": cost, "model": result.get("model_key")}

        except Exception as exc:
            update_research_step(
                step_id, status="failed", error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
                db_path=self.db_path,
            )
            raise

    # ── Step 2d: Strategy Specification ──────────────────────────────────

    def _generate_strategy_spec(
        self, idea: dict, hypothesis: dict, evidence: dict, review: dict,
    ) -> dict:
        step_id = create_research_step(
            idea["id"], "strategy_spec", status="running",
            db_path=self.db_path,
        )
        started = datetime.now(timezone.utc).isoformat()
        update_research_step(step_id, started_at=started, db_path=self.db_path)

        try:
            # Summarize evidence (trim for token efficiency)
            ev = evidence.get("output", {})
            evidence_summary = json.dumps({
                k: v for k, v in ev.items()
                if k in ("ticker", "price_data", "macro")
            }, indent=2, default=str)

            spec = None
            cost_total = 0.0
            last_model_key = ""
            last_error = ""

            for attempt in range(2):
                retry_ctx = ""
                if attempt > 0 and last_error:
                    retry_ctx = f"PREVIOUS ATTEMPT FAILED VALIDATION:\n{last_error}\nPlease fix these issues."

                prompt = STRATEGY_SPEC_PROMPT.format(
                    hypothesis_json=json.dumps(hypothesis.get("output", {}), indent=2),
                    evidence_summary=evidence_summary,
                    review_json=json.dumps(review.get("output", {}), indent=2),
                    retry_context=retry_ctx,
                )

                model_choice = config.IDEA_RESEARCH_MODEL_STRATEGY
                result = self._call_model(model_choice, prompt, "strategy_spec")
                cost_total += result.get("cost_usd", 0)
                last_model_key = result.get("model_key", model_choice)

                log_council_cost(
                    idea["id"], result.get("model", model_choice),
                    result.get("model_key", model_choice), 0,
                    result.get("input_tokens", 0), result.get("output_tokens", 0),
                    result.get("cost_usd", 0), result.get("duration_s", 0),
                )

                parsed = result.get("parsed", {})
                if not parsed:
                    last_error = "Failed to parse JSON from model response"
                    continue

                errors = validate_strategy_spec(parsed)
                if errors:
                    last_error = "; ".join(errors)
                    logger.warning("Strategy spec validation failed (attempt %d): %s", attempt + 1, last_error)
                    continue

                spec = parsed
                break

            if spec is None:
                update_research_step(
                    step_id, status="failed",
                    error=f"Validation failed after 2 attempts: {last_error}",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    model_used=last_model_key,
                    cost_usd=cost_total,
                    db_path=self.db_path,
                )
                raise ValueError(f"Strategy spec generation failed: {last_error}")

            update_research_step(
                step_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                model_used=last_model_key,
                cost_usd=cost_total,
                output_json=json.dumps(spec),
                db_path=self.db_path,
            )

            return {"output": spec, "cost_usd": cost_total, "model": last_model_key}

        except Exception as exc:
            update_research_step(
                step_id, status="failed", error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
                db_path=self.db_path,
            )
            raise

    # ── Model dispatcher ─────────────────────────────────────────────────

    def _call_model(self, model_choice: str, prompt: str, step_name: str) -> dict:
        """Call the appropriate LLM and return standardized result dict."""
        t0 = time.time()

        if model_choice == "grok":
            api_key = os.getenv("XAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("XAI_API_KEY not set")
            raw = _query_grok(prompt, api_key)
        elif model_choice == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            raw = _query_anthropic(prompt, api_key)
        elif model_choice == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY not set")
            raw = _query_openai(prompt, api_key)
        else:
            raise ValueError(f"Unknown model choice: {model_choice}")

        duration = time.time() - t0
        usage = raw.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        model_key = raw.get("model_key", model_choice)
        cost = _calc_cost(model_key, input_tokens, output_tokens)

        return {
            "model": raw.get("model", model_choice),
            "model_key": model_key,
            "parsed": raw.get("parsed", {}),
            "raw": raw.get("raw", ""),
            "cost_usd": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_s": round(duration, 2),
        }

    # ── Auto-transition after research ───────────────────────────────────

    def _auto_transition(self, idea_id: str, result: dict):
        """Auto-promote to backtest or auto-reject based on research verdict."""
        from intelligence.idea_pipeline import IdeaPipelineManager

        verdict = result.get("verdict", "reject")
        score = result.get("review_score", 0)
        mgr = IdeaPipelineManager(db_path=self.db_path)

        if verdict == "reject":
            reason = f"Research rejected (score {score:.1f}/10)"
            rej = mgr.reject_idea(idea_id, reason=reason, actor="research_pipeline")
            if rej.get("success"):
                logger.info("Auto-rejected idea %s: %s", idea_id[:12], reason)
            else:
                logger.warning("Auto-reject failed for %s: %s", idea_id[:12], rej.get("reasons"))

        elif verdict == "proceed" and config.IDEA_AUTO_PROMOTE_BACKTEST:
            # Promote review -> backtest, then trigger backtest
            promo = mgr.promote_idea(idea_id, "backtest", actor="research_pipeline",
                                     reason=f"Research passed (score {score:.1f}/10)")
            if promo.get("success"):
                logger.info("Auto-promoted idea %s to backtest", idea_id[:12])
                bt = mgr.trigger_backtest(idea_id, actor="research_pipeline")
                if bt.get("success"):
                    logger.info("Auto-triggered backtest for idea %s (job %s)",
                                idea_id[:12], bt.get("job_id"))
                else:
                    logger.warning("Auto-backtest trigger failed for %s: %s",
                                   idea_id[:12], bt.get("reasons"))
            else:
                logger.warning("Auto-promote to backtest failed for %s: %s",
                               idea_id[:12], promo.get("reasons"))
