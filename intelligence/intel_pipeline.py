"""Human-in-the-loop intelligence pipeline.

Processes raw content (SA articles, X/Twitter threads, ad-hoc research)
through the LLM council to extract actionable trade candidates.

Sources:
  - SA bookmarklet: user clicks while browsing Seeking Alpha
  - X/Twitter: user forwards tweets/threads via Telegram or webhook
  - Manual paste: user submits raw text via the UI
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from intelligence.event_store import EventStore, EventRecord
from data.trade_db import create_job, update_job

logger = logging.getLogger(__name__)

INTEL_ANALYSIS_PROMPT = """\
You are a professional trading analyst for a systematic fund. Analyze the \
following content and extract actionable trading intelligence.

Source: {source}
Title: {title}
URL: {url}
Content:
---
{content}
---
{tickers_hint}

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON) \
with these exact fields:
{{"tickers_identified": ["<TICKER1>", "<TICKER2>"],
"trade_ideas": [
  {{"ticker": "<TICKER>", "direction": "long|short", "conviction": "high|medium|low", \
"timeframe": "days|weeks|months", "thesis": "<1-2 sentence thesis>", \
"entry_trigger": "<what should trigger entry>", "invalidation": "<what would invalidate>"}}
],
"summary": "<2-3 sentence summary of the key intelligence>",
"risk_factors": ["<risk 1>", "<risk 2>"],
"confidence": <float 0.0-1.0 how actionable this intel is>,
"sentiment": "<bullish|bearish|neutral|mixed>"}}

Rules:
- Extract ALL tickers mentioned, even if only referenced in passing
- Only include trade ideas where there is a clear directional thesis
- Be specific about entry triggers and invalidation levels
- confidence reflects how tradeable/actionable the content is (news = lower, deep analysis = higher)
- If content is noise/opinion without substance, set confidence low and trade_ideas empty"""


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
        return {
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


def _build_prompt(submission: IntelSubmission) -> str:
    tickers_hint = ""
    if submission.tickers:
        tickers_hint = f"Tickers already identified by submitter: {', '.join(submission.tickers)}"
    # Truncate content to avoid token limits
    content = submission.content[:12000]
    return INTEL_ANALYSIS_PROMPT.format(
        source=submission.source,
        title=submission.title or "N/A",
        url=submission.url or "N/A",
        content=content,
        tickers_hint=tickers_hint,
    )


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    """Extract JSON object from LLM response text."""
    import re
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    match = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.DOTALL)
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


def _query_anthropic(prompt: str, api_key: str) -> Dict[str, Any]:
    """Call Anthropic Claude API."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "temperature": 0.1,
            "system": "You are a professional equity analyst for a systematic trading fund.",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"]
    return {"model": "claude", "raw": text, "parsed": _extract_json_from_text(text)}


def _query_openai(prompt: str, api_key: str) -> Dict[str, Any]:
    """Call OpenAI ChatGPT API."""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "max_tokens": 2048,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": "You are a professional equity analyst for a systematic trading fund."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return {"model": "chatgpt", "raw": text, "parsed": _extract_json_from_text(text)}


def _query_grok(prompt: str, api_key: str) -> Dict[str, Any]:
    """Call xAI Grok API."""
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "grok-3",
            "max_tokens": 2048,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": "You are a professional equity analyst for a systematic trading fund."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return {"model": "grok", "raw": text, "parsed": _extract_json_from_text(text)}


def _query_gemini(prompt: str, api_key: str) -> Dict[str, Any]:
    """Call Google Gemini API."""
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return {"model": "gemini", "raw": text, "parsed": _extract_json_from_text(text)}


def analyze_intel(submission: IntelSubmission) -> IntelAnalysis:
    """Run an intel submission through all available LLM models.

    Each model analyzes independently, results are aggregated into
    deduplicated trade ideas with confidence scores.
    """
    prompt = _build_prompt(submission)

    # Query all available models
    models = []
    api_keys = {
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "xai": os.getenv("XAI_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", ""),
    }
    query_fns = {
        "anthropic": _query_anthropic,
        "openai": _query_openai,
        "xai": _query_grok,
        "google": _query_gemini,
    }

    results: List[Dict[str, Any]] = []
    for provider, key in api_keys.items():
        if not key:
            continue
        try:
            result = query_fns[provider](prompt, key)
            results.append(result)
            logger.info("Intel analysis from %s succeeded", result["model"])
        except Exception as exc:
            logger.warning("Intel analysis from %s failed: %s", provider, exc)

    if not results:
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

    # Aggregate across models
    all_tickers = set(submission.tickers)
    all_trade_ideas = []
    all_risk_factors = []
    summaries = []
    confidences = []

    for r in results:
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

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Deduplicate trade ideas by (ticker, direction)
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
        models_used=len(results),
        raw_verdicts=[{"model": r["model"], "raw": r["raw"][:2000]} for r in results],
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
                notifier.send(
                    f"Intel analyzed ({result.submission.source})\n"
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
