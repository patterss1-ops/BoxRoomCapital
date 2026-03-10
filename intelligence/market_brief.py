"""Market brief service — AI-powered morning and evening market analyst.

Generates structured market briefs and delivers via Telegram and email.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ─── Delivery config ────────────────────────────────────────────────────
_EMAIL_TO = os.getenv("NOTIFY_EMAIL", "patterss1@gmail.com")


@dataclass
class MarketBrief:
    """A single market brief (morning or evening)."""
    brief_type: str  # "morning" or "evening"
    as_of: str
    generated_at: str
    sections: dict[str, str] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)
    model_used: str = ""
    cost_usd: float = 0.0
    delivered_telegram: bool = False
    delivered_email: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Data fetching ──────────────────────────────────────────────────────

_TICKER_LABELS = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow", "^RUT": "Russell 2000", "^VIX": "VIX",
    "ES=F": "ES (S&P futs)", "NQ=F": "NQ (Nasdaq futs)", "YM=F": "YM (Dow futs)",
    "CL=F": "Crude Oil", "GC=F": "Gold",
    "^TNX": "10Y Yield", "^TYX": "30Y Yield",
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY", "DX-Y.NYB": "DXY",
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
}


def _fetch_market_snapshot() -> dict[str, Any]:
    """Fetch current prices for key instruments via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not available"}

    tickers = {
        "indices": ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"],
        "futures": ["ES=F", "NQ=F", "YM=F", "CL=F", "GC=F"],
        "bonds": ["^TNX", "^TYX"],
        "fx": ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "DX-Y.NYB"],
        "crypto": ["BTC-USD", "ETH-USD"],
    }

    snapshot: dict[str, Any] = {}
    all_symbols = []
    symbol_category: dict[str, str] = {}
    for category, syms in tickers.items():
        all_symbols.extend(syms)
        for s in syms:
            symbol_category[s] = category

    try:
        data = yf.download(all_symbols, period="5d", progress=False, group_by="ticker")
        for symbol in all_symbols:
            category = symbol_category[symbol]
            if category not in snapshot:
                snapshot[category] = {}
            try:
                if len(all_symbols) > 1 and symbol in data.columns.get_level_values(0):
                    df = data[symbol]
                else:
                    df = data
                if df is None or df.empty:
                    continue
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                close = float(last["Close"].iloc[0]) if hasattr(last["Close"], "iloc") else float(last["Close"])
                prev_close = float(prev["Close"].iloc[0]) if hasattr(prev["Close"], "iloc") else float(prev["Close"])
                change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
                snapshot[category][symbol] = {
                    "label": _TICKER_LABELS.get(symbol, symbol),
                    "close": round(close, 4 if category == "fx" else 2),
                    "prev_close": round(prev_close, 4 if category == "fx" else 2),
                    "change_pct": round(change_pct, 2),
                }
            except Exception as exc:
                logger.debug("Failed to extract %s: %s", symbol, exc)
    except Exception as exc:
        logger.warning("yfinance download failed: %s", exc)
        snapshot["error"] = str(exc)

    return snapshot


def _fetch_news_headlines(max_items: int = 20) -> list[dict[str, str]]:
    """Fetch recent market news headlines via finnhub."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return [{"note": "FINNHUB_API_KEY not set — using market data only"}]

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        articles = resp.json()[:max_items]
        return [
            {
                "headline": a.get("headline", ""),
                "source": a.get("source", ""),
                "summary": (a.get("summary", "") or "")[:200],
            }
            for a in articles
        ]
    except Exception as exc:
        logger.warning("Finnhub news fetch failed: %s", exc)
        return []


# ─── Prompt builders ────────────────────────────────────────────────────

def _format_snapshot_for_prompt(snapshot: dict) -> str:
    """Format snapshot into a readable text table for the LLM prompt."""
    if not snapshot or "error" in snapshot:
        return "Market data unavailable"
    lines = []
    for category, instruments in snapshot.items():
        if not isinstance(instruments, dict):
            continue
        lines.append(f"\n{category.upper()}:")
        for symbol, info in instruments.items():
            if not isinstance(info, dict):
                continue
            label = info.get("label", symbol)
            close = info.get("close", 0)
            chg = info.get("change_pct", 0)
            sign = "+" if chg >= 0 else ""
            lines.append(f"  {label:<20s} {close:>10,.2f}  ({sign}{chg:.2f}%)")
    return "\n".join(lines)


def _build_morning_prompt(snapshot: dict, headlines: list[dict]) -> tuple[str, str]:
    """Build system + user prompt for morning brief."""
    system = """You are the BoxRoomCapital in-house macro economist and market analyst.
Write a concise pre-market opening brief for a proprietary trading desk.

STRUCTURE your response with these EXACT section headers in this order:

## TLDR
2-3 bullet points. The absolute must-know for today. A busy trader reads only this.

## Overnight Summary
Key moves in Asia/Europe sessions, futures positioning. 3-5 sentences max.

## Key Numbers
Present as a CLEAN list grouped by asset class. For each instrument show:
name: level (change%). Use arrow symbols for direction.
Example format:
INDICES
  S&P 500: 5,200 (+0.3%) ▲
  Nasdaq: 18,100 (-0.1%) ▼
Do NOT use markdown tables — they render badly. Use the list format above.

## Today's Catalysts
Economic data releases, earnings, Fed speakers, geopolitical events. Bullet list.

## Market Regime
One-liner regime call (risk-on / risk-off / transitional) then 2-3 sentences of context.

## Trading Outlook
Where markets are headed today. Key levels. Conviction: HIGH / MEDIUM / LOW.

## Risk Flags
Bullet list of tail risks. Keep it tight — 2-4 items max.

RULES:
- Lead with the TLDR. A trader should get the picture in 10 seconds.
- Be direct and opinionated. You are a senior macro strategist, not a news reader.
- Use specific numbers and levels, not vague qualifiers like "slightly" or "somewhat".
- Keep total length under 600 words."""

    market_text = _format_snapshot_for_prompt(snapshot)
    news_text = "\n".join(
        f"- {h.get('headline', '')} ({h.get('source', '')})"
        for h in headlines if h.get("headline")
    ) or "No news headlines available"

    user = f"""Generate the pre-market morning brief for {date.today().isoformat()}.

MARKET DATA (latest closes and changes):
{market_text}

RECENT NEWS HEADLINES:
{news_text}

Produce the brief now."""

    return system, user


def _build_evening_prompt(snapshot: dict, headlines: list[dict]) -> tuple[str, str]:
    """Build system + user prompt for evening brief."""
    system = """You are the BoxRoomCapital in-house macro economist and market analyst.
Write a concise end-of-day market review for a proprietary trading desk.

STRUCTURE your response with these EXACT section headers in this order:

## TLDR
2-3 bullet points. What happened today and what matters for tomorrow. A busy trader reads only this.

## Session Recap
What happened today. Key movers and why. Was price action clean or choppy? 4-5 sentences.

## Key Numbers
Present as a CLEAN list grouped by asset class. For each instrument show:
name: level (change%). Use arrow symbols for direction.
Example format:
INDICES
  S&P 500: 5,200 (+0.3%) ▲
  Nasdaq: 18,100 (-0.1%) ▼
Do NOT use markdown tables — they render badly. Use the list format above.

## Sector Performance
Which sectors led/lagged. Notable rotations. 3-4 sentences.

## Macro Read
What today's action says about the regime. Any transitions underway? 2-3 sentences.

## Tomorrow's Setup
Overnight catalysts (Asia open, data releases, earnings). Key levels.
Directional bias with conviction: HIGH / MEDIUM / LOW.

## Risk Watch
Tail risks that grew or shrank today. 2-4 bullet points max.

RULES:
- Lead with the TLDR. A trader should get the picture in 10 seconds.
- Be direct and opinionated. You are a senior macro strategist, not a news reader.
- Use specific numbers and levels, not vague qualifiers.
- Keep total length under 600 words."""

    market_text = _format_snapshot_for_prompt(snapshot)
    news_text = "\n".join(
        f"- {h.get('headline', '')} ({h.get('source', '')})"
        for h in headlines if h.get("headline")
    ) or "No news headlines available"

    user = f"""Generate the end-of-day market review for {date.today().isoformat()}.

MARKET DATA (today's closes and changes):
{market_text}

TODAY'S NEWS HEADLINES:
{news_text}

Produce the review now."""

    return system, user


# ─── LLM call ───────────────────────────────────────────────────────────

def _call_anthropic(system_prompt: str, user_prompt: str) -> tuple[str, str, float]:
    """Call Anthropic API directly via requests. Returns (text, model_id, cost_usd)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "", "", 0.0

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    resp_json = resp.json()
    text = ""
    for block in resp_json.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    usage = resp_json.get("usage", {})
    cost = (usage.get("input_tokens", 0) * 3 + usage.get("output_tokens", 0) * 15) / 1_000_000
    return text, "claude-sonnet-4-20250514", cost


# ─── Delivery ───────────────────────────────────────────────────────────

def _deliver_telegram(brief: MarketBrief) -> bool:
    """Send brief to Telegram as a formatted message."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.info("Telegram delivery skipped — TELEGRAM_TOKEN/CHAT_ID not set")
        return False

    # Build a compact Telegram message from TLDR + key sections
    icon = "🌅" if brief.brief_type == "morning" else "🌙"
    title = "Pre-Market Brief" if brief.brief_type == "morning" else "End-of-Day Review"
    tldr = brief.sections.get("tldr", "")
    key_numbers = brief.sections.get("key_numbers", "")
    outlook = brief.sections.get("trading_outlook", "") or brief.sections.get("tomorrows_setup", "")
    risk = brief.sections.get("risk_flags", "") or brief.sections.get("risk_watch", "")

    parts = [f"{icon} <b>BoxRoom {title}</b> — {date.today().isoformat()}"]
    if tldr:
        parts.append(f"\n<b>TLDR</b>\n{_html_escape(tldr)}")
    if key_numbers:
        parts.append(f"\n<b>Key Numbers</b>\n<pre>{_html_escape(key_numbers[:800])}</pre>")
    if outlook:
        parts.append(f"\n<b>Outlook</b>\n{_html_escape(outlook[:500])}")
    if risk:
        parts.append(f"\n<b>Risk</b>\n{_html_escape(risk[:300])}")
    parts.append(f"\n<i>Model: {brief.model_used} | Cost: ${brief.cost_usd:.4f}</i>")

    message = "\n".join(parts)
    # Telegram max is 4096 chars
    if len(message) > 4090:
        message = message[:4087] + "..."

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            logger.info("Market brief delivered to Telegram")
            return True
        else:
            logger.warning("Telegram delivery failed: %s — %s", r.status_code, r.text[:100])
            return False
    except Exception as exc:
        logger.warning("Telegram delivery error: %s", exc)
        return False


def _html_escape(text: str) -> str:
    """Escape HTML special chars for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_email_html(brief: MarketBrief) -> str:
    """Build a clean HTML email body from the brief."""
    title = "Pre-Market Brief" if brief.brief_type == "morning" else "End-of-Day Review"
    sections_html = []
    for key, content in brief.sections.items():
        if key == "_full_text" or not content:
            continue
        heading = key.replace("_", " ").title()
        # Use <pre> for key_numbers to preserve formatting
        if key == "key_numbers":
            body = f'<pre style="font-family:monospace;font-size:13px;background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;">{_html_escape(content)}</pre>'
        elif key == "tldr":
            body = f'<div style="background:#e8f4f8;padding:12px;border-left:4px solid #2196F3;border-radius:4px;font-weight:500;">{_html_escape(content).replace(chr(10), "<br>")}</div>'
        else:
            body = f'<div style="color:#333;">{_html_escape(content).replace(chr(10), "<br>")}</div>'
        sections_html.append(
            f'<div style="margin-bottom:16px;">'
            f'<h3 style="font-size:14px;font-weight:700;color:#1a1a1a;text-transform:uppercase;'
            f'letter-spacing:0.5px;border-bottom:1px solid #ddd;padding-bottom:4px;margin-bottom:8px;">{heading}</h3>'
            f'{body}</div>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:640px;margin:0 auto;padding:20px;color:#333;">
<div style="border-bottom:3px solid #1a1a1a;padding-bottom:8px;margin-bottom:20px;">
  <h1 style="font-size:20px;font-weight:700;margin:0;">BoxRoomCapital — {title}</h1>
  <p style="font-size:12px;color:#888;margin:4px 0 0;">{date.today().strftime('%A, %d %B %Y')} | {brief.model_used} | ${brief.cost_usd:.4f}</p>
</div>
{"".join(sections_html)}
<div style="border-top:1px solid #ddd;padding-top:8px;margin-top:20px;">
  <p style="font-size:11px;color:#aaa;">Generated by BoxRoomCapital AI Market Analyst</p>
</div>
</body></html>"""


# ─── Main entry point ───────────────────────────────────────────────────

def generate_brief(
    brief_type: str = "morning",
    model_router: Any = None,
    deliver: bool = True,
) -> MarketBrief:
    """Generate a market brief using LLM synthesis.

    Args:
        brief_type: "morning" or "evening"
        model_router: Optional ModelRouter instance. If None, uses direct API.
        deliver: If True, send via Telegram and create email draft.
    """
    now = datetime.now(timezone.utc)
    snapshot = _fetch_market_snapshot()
    headlines = _fetch_news_headlines()

    if brief_type == "evening":
        system_prompt, user_prompt = _build_evening_prompt(snapshot, headlines)
    else:
        system_prompt, user_prompt = _build_morning_prompt(snapshot, headlines)

    # Try model router first, fall back to direct API
    brief_text = ""
    model_used = ""
    cost_usd = 0.0

    if model_router is not None:
        try:
            from research.artifacts import Engine
            response = model_router.call(
                "market_brief",
                prompt=user_prompt,
                system_prompt=system_prompt,
                engine=Engine.ENGINE_A,
            )
            brief_text = response.raw_text if hasattr(response, "raw_text") else str(response.parsed or "")
            model_used = getattr(response, "model_id", "unknown")
            cost_usd = getattr(response, "cost_usd", 0.0)
        except Exception as exc:
            logger.warning("Model router call failed: %s", exc)

    if not brief_text:
        try:
            brief_text, model_used, cost_usd = _call_anthropic(system_prompt, user_prompt)
        except Exception as exc:
            logger.warning("Direct Anthropic API call failed: %s", exc)

    if not brief_text:
        brief_text = "## Brief Unavailable\nNo LLM API key configured. Set ANTHROPIC_API_KEY to enable market briefs."

    sections = _parse_sections(brief_text)

    brief = MarketBrief(
        brief_type=brief_type,
        as_of=now.isoformat().replace("+00:00", "Z"),
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        sections=sections,
        raw_data={"snapshot": snapshot, "headline_count": len(headlines)},
        model_used=model_used,
        cost_usd=cost_usd,
    )

    # Deliver
    if deliver and brief_text and "Brief Unavailable" not in brief_text:
        brief.delivered_telegram = _deliver_telegram(brief)
        # Email is handled by the caller via Gmail MCP or SMTP

    return brief


def get_email_draft_content(brief: MarketBrief) -> dict[str, str]:
    """Return email subject + HTML body for a brief. Caller sends via Gmail MCP or SMTP."""
    title = "Pre-Market Brief" if brief.brief_type == "morning" else "End-of-Day Review"
    return {
        "to": _EMAIL_TO,
        "subject": f"BoxRoom {title} — {date.today().strftime('%d %b %Y')}",
        "body": _build_email_html(brief),
        "content_type": "text/html",
    }


def _parse_sections(text: str) -> dict[str, str]:
    """Parse markdown sections from brief text."""
    sections: dict[str, str] = {}
    current_key = "summary"
    current_lines: list[str] = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip().lower().replace(" ", "_").replace("'", "").replace("&", "and")
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    sections["_full_text"] = text
    return sections
