"""Human-in-the-loop intelligence pipeline.

Processes raw content (SA articles, X/Twitter threads, ad-hoc research)
through the LLM council to extract actionable trade candidates.

Sources:
  - SA bookmarklet: user clicks while browsing Seeking Alpha
  - X/Twitter: user forwards tweets/threads via Telegram or webhook
  - Manual paste: user submits raw text via the UI

Council process:
  1. Round 1 — each model analyzes independently
  2. Round 2 — each model sees the other models' verdicts and can challenge/revise
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re as _re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

import config
from intelligence.event_store import EventStore, EventRecord
from data.trade_db import create_job, update_job

logger = logging.getLogger(__name__)

# ─── Cost tracking ─────────────────────────────────────────────────────────
# Pricing per million tokens (USD) — update when pricing changes
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "gpt-5.4": {"input": 10.0, "output": 40.0},
    "o3": {"input": 10.0, "output": 40.0},
    "grok-3": {"input": 3.0, "output": 15.0},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
}


def _calc_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a single API call."""
    pricing = MODEL_PRICING.get(model_key, {"input": 5.0, "output": 20.0})
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _extract_usage(provider: str, resp_json: dict) -> Dict[str, int]:
    """Extract token usage from API response JSON."""
    if provider == "anthropic":
        usage = resp_json.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }
    elif provider in ("openai", "xai"):
        usage = resp_json.get("usage", {})
        return {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
    elif provider == "google":
        metadata = resp_json.get("usageMetadata", {})
        return {
            "input_tokens": metadata.get("promptTokenCount", 0),
            "output_tokens": metadata.get("candidatesTokenCount", 0),
        }
    return {"input_tokens": 0, "output_tokens": 0}


def log_council_cost(
    analysis_id: str,
    model: str,
    model_key: str,
    round_num: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    duration_s: float,
) -> None:
    """Persist a council cost record to the DB."""
    try:
        from data.trade_db import get_conn as get_connection
        conn = get_connection()
        conn.execute(
            """INSERT INTO council_costs
               (timestamp, analysis_id, model, model_key, round, input_tokens, output_tokens, cost_usd, duration_s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                analysis_id,
                model,
                model_key,
                round_num,
                input_tokens,
                output_tokens,
                round(cost_usd, 6),
                round(duration_s, 2),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Failed to log council cost: %s", e)


def get_council_cost_summary() -> Dict[str, Any]:
    """Get aggregate cost stats for the council."""
    try:
        from data.trade_db import get_conn as get_connection
        conn = get_connection()

        # Total costs
        row = conn.execute(
            "SELECT COUNT(*), SUM(cost_usd), SUM(input_tokens), SUM(output_tokens) FROM council_costs"
        ).fetchone()
        total_calls = row[0] or 0
        total_cost = row[1] or 0.0
        total_input = row[2] or 0
        total_output = row[3] or 0

        # Per-model breakdown
        models = conn.execute(
            """SELECT model, COUNT(*), SUM(cost_usd), SUM(input_tokens), SUM(output_tokens),
                      AVG(duration_s)
               FROM council_costs GROUP BY model ORDER BY SUM(cost_usd) DESC"""
        ).fetchall()
        model_breakdown = [
            {
                "model": m[0], "calls": m[1], "cost_usd": round(m[2] or 0, 4),
                "input_tokens": m[3] or 0, "output_tokens": m[4] or 0,
                "avg_duration_s": round(m[5] or 0, 1),
            }
            for m in models
        ]

        # Today's cost
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_row = conn.execute(
            "SELECT SUM(cost_usd), COUNT(*) FROM council_costs WHERE timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()
        today_cost = today_row[0] or 0.0
        today_calls = today_row[1] or 0

        # Last 24h by hour
        hourly = conn.execute(
            """SELECT substr(timestamp, 1, 13) as hour, SUM(cost_usd), COUNT(*)
               FROM council_costs
               WHERE timestamp > datetime('now', '-24 hours')
               GROUP BY hour ORDER BY hour"""
        ).fetchall()

        # Per-analysis cost
        analyses = conn.execute(
            """SELECT analysis_id, SUM(cost_usd), COUNT(*), SUM(input_tokens + output_tokens),
                      MIN(timestamp)
               FROM council_costs GROUP BY analysis_id ORDER BY MIN(timestamp) DESC LIMIT 10"""
        ).fetchall()
        recent_analyses = [
            {
                "analysis_id": a[0], "cost_usd": round(a[1] or 0, 4),
                "calls": a[2], "total_tokens": a[3] or 0,
                "timestamp": a[4],
            }
            for a in analyses
        ]

        conn.close()
        return {
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 4),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "today_cost_usd": round(today_cost, 4),
            "today_calls": today_calls,
            "model_breakdown": model_breakdown,
            "recent_analyses": recent_analyses,
        }
    except Exception as e:
        logger.warning("Failed to get council cost summary: %s", e)
        return {"total_calls": 0, "total_cost_usd": 0, "today_cost_usd": 0,
                "model_breakdown": [], "recent_analyses": []}

# ─── Fund context for the council ──────────────────────────────────────────
FUND_CONTEXT = """\
You are a senior analyst on the investment committee of BoxRoomCapital, \
a systematic multi-asset trading fund run by a single operator with 35 years \
of investment banking technology experience and £100k+ in capital.

FUND PROFILE:
- Strategies: momentum, mean-reversion (IBS), macro overlay (GTAA, dual momentum), \
insider signals, fundamental/quant screening
- Assets: equities, commodities, crypto, FX, fixed income
- Timeframes: intraday to monthly rebalancing
- Execution: primarily UK spread betting (IG, CityIndex) for tax-free gains; \
also IBKR for ISA equities and Kraken for crypto
- Edge: signal aggregation across 8 layers (technicals, fundamentals, sentiment, \
macro regime, insider activity, options flow, SA quant scores, news/LLM analysis)
- Risk: max 2% portfolio risk per position, correlation-aware sizing, \
promotion pipeline (shadow → staged_live → live)

YOUR ROLE:
- Identify actionable trade opportunities with specific tickers, direction, and thesis
- Be brutally honest about conviction level — low-quality noise should score near 0
- Think about WHAT INSTRUMENT to trade (specific futures, ETFs, spread bet markets)
- Consider how this fits with existing strategy sleeves
- Flag if the opportunity is time-sensitive or can wait for better entry
- Consider UK spread betting availability (IG/CityIndex market coverage)"""

# ─── Round 1 prompt ────────────────────────────────────────────────────────
ROUND1_PROMPT = """\
Analyze the following content and extract actionable trading intelligence.

Source: {source}
Title: {title}
URL: {url}
Content:
---
{content}
---
{tickers_hint}

Think deeply about this content. Consider:
1. What is the core thesis or insight?
2. What are the tradeable implications — specific instruments, direction, timeframe?
3. What would confirm or invalidate this thesis?
4. How confident should we be — is this noise, opinion, or genuinely actionable intelligence?
5. What risks or counter-arguments exist?

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON) \
with these exact fields:
{{"tickers_identified": ["<TICKER1>", "<TICKER2>"],
"trade_ideas": [
  {{"ticker": "<TICKER>", "direction": "long|short", "conviction": "high|medium|low", \
"timeframe": "days|weeks|months", "thesis": "<2-3 sentence thesis>", \
"entry_trigger": "<specific trigger>", "invalidation": "<specific invalidation level/event>", \
"instrument": "<how to trade it: ETF, future, spread bet, equity>"}}
],
"summary": "<3-4 sentence analysis of the key intelligence and its market implications>",
"risk_factors": ["<specific risk 1>", "<specific risk 2>"],
"confidence": <float 0.0-1.0>,
"sentiment": "<bullish|bearish|neutral|mixed>"}}

Rules:
- Extract ALL tickers mentioned, mapping to tradeable instruments where possible
- Only include trade ideas where there is a clear directional thesis with specific entry/exit
- confidence reflects how tradeable/actionable this is for OUR fund specifically
- Noise, vague opinion, or content without substance = confidence < 0.1, empty trade_ideas
- High conviction = the content contains specific, verifiable, time-sensitive intelligence
- Consider UK spread betting availability for suggested instruments"""

# ─── Round 2 debate prompt ─────────────────────────────────────────────────
ROUND2_PROMPT = """\
You previously analyzed some trading intelligence. Now review what the other \
council members concluded and provide your REVISED assessment.

ORIGINAL CONTENT:
---
{content_summary}
---

YOUR ORIGINAL ANALYSIS:
{own_verdict}

OTHER COUNCIL MEMBERS' ANALYSES:
{other_verdicts}

INSTRUCTIONS:
1. Where do you AGREE with the other analysts? What consensus is emerging?
2. Where do you DISAGREE? Challenge specific points with reasoning.
3. Are there trade ideas from others that you missed or that change your view?
4. Has your confidence changed after seeing the other perspectives?
5. Are there risks the others identified that you overlooked?

Respond with ONLY a JSON object with these fields:
{{"revised_confidence": <float 0.0-1.0>,
"revised_trade_ideas": [same format as round 1],
"agreements": ["<point of agreement 1>"],
"disagreements": ["<specific disagreement with reasoning>"],
"revised_summary": "<2-3 sentence revised view incorporating the debate>",
"key_debate_points": ["<most important point of contention>"]}}"""


@dataclass
class IntelSubmission:
    """Raw intelligence submission from any source."""

    source: str  # "seeking_alpha", "x_twitter", "manual"
    content: str
    url: str = ""
    title: str = ""
    author: str = ""
    tickers: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    submitted_at: str = ""

    def __post_init__(self):
        if not self.submitted_at:
            self.submitted_at = datetime.now(timezone.utc).isoformat()
        self.tickers = [t.strip().upper() for t in self.tickers if t.strip()]


@dataclass
class IntelAnalysis:
    """LLM council analysis result for an intel submission."""

    submission: IntelSubmission
    tickers_identified: List[str]
    trade_ideas: List[Dict[str, Any]]
    summary: str
    risk_factors: List[str]
    confidence: float
    models_used: int
    raw_verdicts: List[Dict[str, Any]]
    debate_summary: str = ""
    analysis_id: str = ""
    analyzed_at: str = ""

    def __post_init__(self):
        if not self.analyzed_at:
            self.analyzed_at = datetime.now(timezone.utc).isoformat()
        if not self.analysis_id:
            h = hashlib.sha256(
                f"{self.submission.source}|{self.submission.url}|{self.submission.submitted_at}".encode()
            ).hexdigest()[:12]
            self.analysis_id = f"intel_{h}"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "analysis_id": self.analysis_id,
            "analyzed_at": self.analyzed_at,
            "source": self.submission.source,
            "url": self.submission.url,
            "title": self.submission.title,
            "tickers_identified": self.tickers_identified,
            "trade_ideas": self.trade_ideas,
            "summary": self.summary,
            "risk_factors": self.risk_factors,
            "confidence": self.confidence,
            "models_used": self.models_used,
        }
        if self.debate_summary:
            d["debate_summary"] = self.debate_summary
        return d


def _build_round1_prompt(submission: IntelSubmission) -> str:
    tickers_hint = ""
    if submission.tickers:
        tickers_hint = f"Tickers already identified by submitter: {', '.join(submission.tickers)}"
    content = submission.content[:16000]
    return ROUND1_PROMPT.format(
        source=submission.source,
        title=submission.title or "N/A",
        url=submission.url or "N/A",
        content=content,
        tickers_hint=tickers_hint,
    )


def _build_round2_prompt(
    submission: IntelSubmission,
    own_verdict: Dict[str, Any],
    other_verdicts: List[Dict[str, Any]],
) -> str:
    content_summary = submission.content[:4000]
    own_str = json.dumps(own_verdict.get("parsed", {}), indent=2, default=str)[:3000]
    others_str = ""
    for v in other_verdicts:
        model = v.get("model", "unknown")
        parsed = json.dumps(v.get("parsed", {}), indent=2, default=str)[:2000]
        others_str += f"\n--- {model.upper()} ---\n{parsed}\n"
    return ROUND2_PROMPT.format(
        content_summary=content_summary,
        own_verdict=own_str,
        other_verdicts=others_str,
    )


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """Extract JSON object from LLM response text."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    match = _re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, _re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _fetch_linked_content(text: str) -> str:
    """Try to fetch article content from URLs embedded in the text."""
    urls = _re.findall(r'https?://\S+', text)
    extra = []
    for url in urls[:3]:  # Max 3 links
        # Skip X/Twitter links (we already have the tweet text)
        if any(d in url for d in ["twitter.com", "x.com", "t.co"]):
            continue
        try:
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; BoxRoomCapital/1.0)"
            })
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                # Extract text content from HTML
                html = resp.text[:50000]
                # Strip tags crudely
                clean = _re.sub(r'<script[^>]*>.*?</script>', '', html, flags=_re.DOTALL)
                clean = _re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=_re.DOTALL)
                clean = _re.sub(r'<[^>]+>', ' ', clean)
                clean = _re.sub(r'\s+', ' ', clean).strip()
                if len(clean) > 200:
                    extra.append(f"\n\n[Linked article from {url[:60]}]:\n{clean[:5000]}")
        except Exception:
            pass
    return "".join(extra)


# ─── Model query functions ─────────────────────────────────────────────────

def _query_anthropic(prompt: str, api_key: str, system: str = FUND_CONTEXT) -> Dict[str, Any]:
    """Call Anthropic Claude API — using Claude Opus 4.6 (latest, best reasoning)."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 16000,
            "temperature": 1,  # Required for extended thinking
            "thinking": {
                "type": "enabled",
                "budget_tokens": 10000,
            },
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    resp_json = resp.json()
    content_blocks = resp_json.get("content", [])
    text = ""
    thinking = ""
    for block in content_blocks:
        if block.get("type") == "thinking":
            thinking = block.get("thinking", "")
        elif block.get("type") == "text":
            text = block.get("text", "")
    usage = _extract_usage("anthropic", resp_json)
    return {"model": "claude", "model_key": "claude-opus-4-6", "raw": text,
            "thinking": thinking[:3000], "parsed": _extract_json_from_text(text), "usage": usage}


def _query_openai(prompt: str, api_key: str, system: str = FUND_CONTEXT) -> Dict[str, Any]:
    """Call OpenAI API — using GPT-5.4 (latest, best reasoning model)."""
    # Try GPT-5.4 first, fall back to o3 if not available
    for model in ["gpt-5.4", "o3"]:
        try:
            payload: Dict[str, Any] = {
                "model": model,
                "max_completion_tokens": 16000,
                "messages": [
                    {"role": "developer", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
            # o-series and GPT-5+ use reasoning_effort instead of temperature
            if model.startswith("o") or model.startswith("gpt-5"):
                payload["reasoning_effort"] = "high"
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if resp.status_code == 404:
                logger.info("Model %s not available, trying next", model)
                continue
            resp.raise_for_status()
            resp_json = resp.json()
            text = resp_json["choices"][0]["message"]["content"]
            usage = _extract_usage("openai", resp_json)
            return {"model": f"chatgpt ({model})", "model_key": model, "raw": text,
                    "parsed": _extract_json_from_text(text), "usage": usage}
        except requests.exceptions.HTTPError as exc:
            if "404" in str(exc) or "model_not_found" in str(exc):
                continue
            raise
    raise RuntimeError("No OpenAI model available")


def _query_grok(prompt: str, api_key: str, system: str = FUND_CONTEXT) -> Dict[str, Any]:
    """Call xAI Grok API — using grok-3 (best available)."""
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "grok-3",
            "max_tokens": 8192,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    resp_json = resp.json()
    text = resp_json["choices"][0]["message"]["content"]
    usage = _extract_usage("xai", resp_json)
    return {"model": "grok", "model_key": "grok-3", "raw": text,
            "parsed": _extract_json_from_text(text), "usage": usage}


def _query_gemini(prompt: str, api_key: str, system: str = FUND_CONTEXT) -> Dict[str, Any]:
    """Call Google Gemini API — using gemini-2.5-pro (best thinking model)."""
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192,
                                 "thinkingConfig": {"thinkingBudget": 8000}},
        },
        timeout=120,
    )
    resp.raise_for_status()
    resp_json = resp.json()
    text = resp_json["candidates"][0]["content"]["parts"][-1]["text"]
    usage = _extract_usage("google", resp_json)
    return {"model": "gemini", "model_key": "gemini-2.5-pro", "raw": text,
            "parsed": _extract_json_from_text(text), "usage": usage}


# ─── Main analysis function ────────────────────────────────────────────────

def analyze_intel(submission: IntelSubmission) -> IntelAnalysis:
    """Run an intel submission through the LLM council.

    Round 1: each model analyzes independently.
    Round 2: each model sees others' verdicts and debates/revises.
    """
    # Try to enrich content with linked articles
    if submission.content:
        linked = _fetch_linked_content(submission.content)
        if linked:
            submission.content += linked
            logger.info("Enriched submission with %d chars of linked content", len(linked))

    prompt = _build_round1_prompt(submission)

    # ── Round 1: Independent analysis ──
    api_keys = {
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "xai": os.getenv("XAI_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", "") or os.getenv("GOOGLE_AI_API_KEY", ""),
    }
    query_fns = {
        "anthropic": _query_anthropic,
        "openai": _query_openai,
        "xai": _query_grok,
        "google": _query_gemini,
    }

    # Generate analysis_id early so we can tag cost records
    h = hashlib.sha256(
        f"{submission.source}|{submission.url}|{submission.submitted_at}".encode()
    ).hexdigest()[:12]
    analysis_id = f"intel_{h}"

    # Run all models in parallel with per-model timeout
    def _run_round1(provider: str, key: str) -> Optional[Dict[str, Any]]:
        try:
            t0 = time.monotonic()
            result = query_fns[provider](prompt, key)
            duration = time.monotonic() - t0
            usage = result.get("usage", {})
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            model_key = result.get("model_key", "unknown")
            cost = _calc_cost(model_key, inp, out)
            result["cost_usd"] = cost
            result["duration_s"] = duration
            log_council_cost(analysis_id, result["model"], model_key, 1, inp, out, cost, duration)
            logger.info("Round 1 from %s: %d in/%d out tokens, $%.4f, %.1fs (confidence: %s)",
                        result["model"], inp, out, cost, duration,
                        result.get("parsed", {}).get("confidence", "?"))
            return result
        except Exception as exc:
            logger.warning("Round 1 from %s failed: %s", provider, exc)
            return None

    round1_results: List[Dict[str, Any]] = []
    active_providers = {p: k for p, k in api_keys.items() if k}
    with ThreadPoolExecutor(max_workers=len(active_providers) or 1) as pool:
        futures = {pool.submit(_run_round1, p, k): p for p, k in active_providers.items()}
        try:
            for future in as_completed(futures, timeout=config.COUNCIL_ROUND_TIMEOUT):
                provider = futures[future]
                try:
                    result = future.result(timeout=5)
                    if result:
                        round1_results.append(result)
                except FuturesTimeoutError:
                    logger.warning("Round 1 from %s timed out", provider)
                except Exception as exc:
                    logger.warning("Round 1 from %s raised: %s", provider, exc)
        except FuturesTimeoutError:
            done = {futures[f] for f in futures if f.done()}
            missing = set(active_providers) - done
            logger.warning("Round 1 overall timeout — still waiting on: %s", missing)

    if not round1_results:
        logger.error("No AI models available for intel analysis")
        return IntelAnalysis(
            submission=submission,
            tickers_identified=submission.tickers,
            trade_ideas=[],
            summary="No AI models available for analysis.",
            risk_factors=[],
            confidence=0.0,
            models_used=0,
            raw_verdicts=[],
        )

    # ── Round 2: Debate (only if 2+ models responded) ──
    round2_results: List[Dict[str, Any]] = []
    debate_summary = ""
    if len(round1_results) >= 2:
        logger.info("Starting Round 2 debate with %d models", len(round1_results))

        def _run_round2(own: Dict, others: List[Dict]) -> Optional[Dict[str, Any]]:
            model_name = own["model"]
            provider_map = {"claude": "anthropic", "chatgpt": "openai", "grok": "xai", "gemini": "google"}
            base_name = model_name.split("(")[0].strip().split()[0].lower()
            pkey = provider_map.get(base_name, "")
            api_key = api_keys.get(pkey, "")
            query_fn = query_fns.get(pkey)
            if not api_key or not query_fn:
                return None
            try:
                r2_prompt = _build_round2_prompt(submission, own, others)
                t0 = time.monotonic()
                r2 = query_fn(r2_prompt, api_key)
                duration = time.monotonic() - t0
                r2["round"] = 2
                usage = r2.get("usage", {})
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                model_key = r2.get("model_key", "unknown")
                cost = _calc_cost(model_key, inp, out)
                log_council_cost(analysis_id, r2["model"], model_key, 2, inp, out, cost, duration)
                logger.info("Round 2 from %s: %d in/%d out tokens, $%.4f, %.1fs",
                            model_name, inp, out, cost, duration)
                return r2
            except Exception as exc:
                logger.warning("Round 2 from %s failed: %s", model_name, exc)
                return None

        r2_tasks = []
        for i, own in enumerate(round1_results):
            others = [r for j, r in enumerate(round1_results) if j != i]
            r2_tasks.append((own, others))

        with ThreadPoolExecutor(max_workers=len(r2_tasks) or 1) as pool:
            futures = {pool.submit(_run_round2, own, others): own["model"] for own, others in r2_tasks}
            try:
                for future in as_completed(futures, timeout=config.COUNCIL_ROUND_TIMEOUT):
                    model_name = futures[future]
                    try:
                        r2 = future.result(timeout=5)
                        if r2:
                            round2_results.append(r2)
                    except FuturesTimeoutError:
                        logger.warning("Round 2 from %s timed out", model_name)
                    except Exception as exc:
                        logger.warning("Round 2 from %s raised: %s", model_name, exc)
            except FuturesTimeoutError:
                done = {futures[f] for f in futures if f.done()}
                missing = set(f"{own['model']}" for own, _ in r2_tasks) - done
                logger.warning("Round 2 overall timeout — still waiting on: %s", missing)

        # Build debate summary from round 2
        debate_parts = []
        for r2 in round2_results:
            model = r2.get("model", "unknown")
            parsed = r2.get("parsed", {})
            agreements = parsed.get("agreements", [])
            disagreements = parsed.get("disagreements", [])
            key_points = parsed.get("key_debate_points", [])
            revised_conf = parsed.get("revised_confidence")
            revised_summary = parsed.get("revised_summary", "")
            part = f"[{model}] "
            if revised_summary:
                part += revised_summary
            if disagreements:
                part += f" Challenges: {'; '.join(disagreements[:2])}"
            if revised_conf is not None:
                part += f" (revised confidence: {revised_conf})"
            debate_parts.append(part)
        debate_summary = "\n\n".join(debate_parts)

    # ── Aggregate results (prefer round 2 if available) ──
    all_tickers = set(submission.tickers)
    all_trade_ideas = []
    all_risk_factors = []
    summaries = []
    confidences = []

    # Use round 1 for base analysis
    for r in round1_results:
        parsed = r.get("parsed", {})
        model = r.get("model", "unknown")

        tickers = parsed.get("tickers_identified", [])
        if isinstance(tickers, list):
            all_tickers.update(t.upper() for t in tickers if isinstance(t, str) and t)

        ideas = parsed.get("trade_ideas", [])
        if isinstance(ideas, list):
            for idea in ideas:
                if isinstance(idea, dict):
                    idea["source_model"] = model
                    idea["round"] = 1
                    all_trade_ideas.append(idea)

        risks = parsed.get("risk_factors", [])
        if isinstance(risks, list):
            all_risk_factors.extend(str(r) for r in risks)

        summary = parsed.get("summary", "")
        if summary:
            summaries.append(f"[{model}] {summary}")

        conf = parsed.get("confidence")
        if conf is not None:
            try:
                confidences.append(float(conf))
            except (ValueError, TypeError):
                pass

    # Incorporate round 2 revised ideas and confidence
    revised_confidences = []
    for r2 in round2_results:
        parsed = r2.get("parsed", {})
        model = r2.get("model", "unknown")

        revised_ideas = parsed.get("revised_trade_ideas", [])
        if isinstance(revised_ideas, list):
            for idea in revised_ideas:
                if isinstance(idea, dict):
                    idea["source_model"] = model
                    idea["round"] = 2
                    all_trade_ideas.append(idea)

        revised_conf = parsed.get("revised_confidence")
        if revised_conf is not None:
            try:
                revised_confidences.append(float(revised_conf))
            except (ValueError, TypeError):
                pass

    # Final confidence: weight round 2 more heavily if available
    if revised_confidences:
        avg_confidence = (
            0.3 * (sum(confidences) / len(confidences) if confidences else 0) +
            0.7 * (sum(revised_confidences) / len(revised_confidences))
        )
    else:
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Deduplicate trade ideas by (ticker, direction), prefer round 2 over round 1
    all_trade_ideas.sort(key=lambda x: x.get("round", 1), reverse=True)
    seen = set()
    unique_ideas = []
    for idea in all_trade_ideas:
        key = (idea.get("ticker", "").upper(), idea.get("direction", "").lower())
        if key[0] and key not in seen:
            seen.add(key)
            unique_ideas.append(idea)

    analysis = IntelAnalysis(
        submission=submission,
        tickers_identified=sorted(all_tickers),
        trade_ideas=unique_ideas,
        summary="\n\n".join(summaries),
        risk_factors=list(set(all_risk_factors)),
        confidence=round(avg_confidence, 4),
        models_used=len(round1_results),
        raw_verdicts=[{"model": r["model"], "raw": r["raw"][:2000]} for r in round1_results],
        debate_summary=debate_summary,
    )

    _persist_intel_event(analysis)
    return analysis


def _persist_intel_event(analysis: IntelAnalysis) -> None:
    """Write intel analysis to the research event store."""
    try:
        store = EventStore()
        store.write_event(EventRecord(
            event_type="intel_analysis",
            source=f"intel_{analysis.submission.source}",
            retrieved_at=analysis.analyzed_at,
            provenance_descriptor={
                "source": analysis.submission.source,
                "url": analysis.submission.url,
                "title": analysis.submission.title,
                "models_used": analysis.models_used,
            },
            source_ref=analysis.submission.url or analysis.analysis_id,
            event_timestamp=analysis.submission.submitted_at,
            symbol=",".join(analysis.tickers_identified[:10]),
            headline=analysis.submission.title or f"Intel from {analysis.submission.source}",
            detail=json.dumps(analysis.to_dict(), default=str),
            confidence=analysis.confidence,
            payload=analysis.to_dict(),
        ))
    except Exception as e:
        logger.error("Failed to persist intel event: %s", e)

    # Seed trade_ideas table from council output
    try:
        from data.trade_db import create_trade_idea as _create_idea
        import uuid as _uuid
        for idea in (analysis.trade_ideas or []):
            if not idea.get("ticker"):
                continue
            _create_idea(
                idea_id=str(_uuid.uuid4()),
                analysis_id=analysis.analysis_id,
                ticker=idea.get("ticker", ""),
                direction=idea.get("direction", "long"),
                conviction=idea.get("conviction", "low"),
                timeframe=idea.get("timeframe"),
                thesis=idea.get("thesis"),
                entry_trigger=idea.get("entry_trigger"),
                invalidation=idea.get("invalidation"),
                instrument=idea.get("instrument"),
                source_model=idea.get("source_model"),
                confidence=analysis.confidence,
            )
        if analysis.trade_ideas:
            logger.info("Seeded %d trade ideas for analysis %s",
                        len(analysis.trade_ideas), analysis.analysis_id[:12])
    except Exception as e:
        logger.error("Failed to seed trade ideas: %s", e)


def analyze_intel_async(submission: IntelSubmission, job_id: str) -> None:
    """Run intel analysis in a background thread with job tracking."""
    def _run():
        try:
            update_job(job_id, status="running")
            result = analyze_intel(submission)
            update_job(
                job_id,
                status="completed",
                result=json.dumps(result.to_dict(), default=str),
            )
            # Send Telegram notification
            try:
                from notifications import notifier
                ticker_str = ", ".join(result.tickers_identified[:5]) or "none"
                ideas_count = len(result.trade_ideas)
                debate_note = " (debated)" if result.debate_summary else ""
                notifier.send(
                    f"Intel analyzed ({result.submission.source}){debate_note}\n"
                    f"Title: {result.submission.title or 'N/A'}\n"
                    f"Tickers: {ticker_str}\n"
                    f"Trade ideas: {ideas_count}\n"
                    f"Confidence: {result.confidence:.0%}\n"
                    f"Models: {result.models_used}",
                    icon="🧠",
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error("Intel analysis job %s failed: %s", job_id, exc)
            update_job(job_id, status="failed", error=str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
