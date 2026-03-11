"""Advisory API routes and HTMX fragments."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config
from app.api.shared import TEMPLATES, _telegram_reply_long

logger = logging.getLogger(__name__)

router = APIRouter(tags=["advisory"])

# ── Module-level singleton for AdvisoryEngine ────────────────────────────────
_advisory_engine: Optional[Any] = None


def _get_advisory_engine():
    """Lazily create or reuse a single AdvisoryEngine instance."""
    global _advisory_engine
    if _advisory_engine is None:
        from intelligence.advisor import AdvisoryEngine
        _advisory_engine = AdvisoryEngine()
    return _advisory_engine


# ── JSON API routes ──────────────────────────────────────────────────────────

@router.get("/api/advisory/holdings")
def advisory_holdings_api(wrapper: str = None):
    """Current holdings by wrapper."""
    try:
        from intelligence.advisory_holdings import calculate_portfolio_snapshot
        snapshot = calculate_portfolio_snapshot()
        if wrapper:
            wrapper_data = snapshot.get("wrappers", {}).get(wrapper.upper())
            if not wrapper_data:
                return {"ok": False, "error": f"No holdings in {wrapper}"}
            return {"ok": True, "wrapper": wrapper.upper(), **wrapper_data}
        return {"ok": True, **snapshot}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/api/advisory/performance")
def advisory_performance_api():
    """P&L + benchmark comparison."""
    try:
        from intelligence.advisory_holdings import calculate_portfolio_snapshot
        snapshot = calculate_portfolio_snapshot()
        return {"ok": True, **snapshot}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/api/advisory/conversations")
def advisory_conversations_api(limit: int = 10):
    """Recent advisory sessions."""
    try:
        from intelligence.advisor import get_conn
        from data.trade_db import DB_PATH
        conn = get_conn(DB_PATH)
        rows = conn.execute(
            "SELECT * FROM advisor_sessions ORDER BY last_active_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        sessions = [dict(r) for r in rows]
        return {"ok": True, "sessions": sessions}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/api/advisory/memories")
def advisory_memories_api(topic: str = "", limit: int = 20):
    """Search advisory memories."""
    try:
        from intelligence.advisor import search_advisor_memories
        from data.trade_db import DB_PATH
        memories = search_advisor_memories(DB_PATH, topic or "", limit=limit)
        return {"ok": True, "memories": memories}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/api/advisory/generate")
async def advisory_generate_api():
    """Trigger proactive advisory brief."""
    if not config.ADVISOR_ENABLED:
        return {"ok": False, "error": "Advisory module disabled"}
    try:
        engine = _get_advisory_engine()
        chat_id = config.NOTIFICATIONS.get("telegram_chat_id", "")
        response = engine.process_message(
            int(chat_id) if chat_id else 0,
            "Generate a proactive weekly strategy review. Summarise market moves, portfolio performance, "
            "news themes, and any actions you recommend this week.",
        )
        if chat_id:
            _telegram_reply_long(int(chat_id), response)
        return {"ok": True, "response": response[:500]}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ─── Advisory HTMX fragments ────────────────────────────────────────────────

@router.get("/fragments/advisory-holdings", response_class=HTMLResponse)
def advisory_holdings_fragment(request: Request):
    """HTMX fragment: wrapper cards with holdings."""
    wrappers = []
    try:
        from intelligence.advisory_holdings import calculate_portfolio_snapshot, get_wrapper_summary
        snapshot = calculate_portfolio_snapshot()
        summaries = get_wrapper_summary()
        for name in ["ISA", "SIPP", "GIA"]:
            w_data = snapshot.get("wrappers", {}).get(name, {})
            allow = summaries.get(name, {})
            holdings_list = w_data.get("holdings", [])
            wrappers.append({
                "name": name,
                "nav": w_data.get("value", 0),
                "pnl": w_data.get("pnl", 0),
                "pnl_pct": w_data.get("pnl_pct", 0),
                "allowance_limit": allow.get("limit") if name in ("ISA", "SIPP") else None,
                "allowance_used": allow.get("used", 0),
                "top_holdings": [
                    {
                        "ticker": h.get("ticker", ""),
                        "pnl": h.get("pnl", 0),
                        "weight": round(h.get("value", 0) / max(w_data.get("value", 1), 1) * 100, 1) if w_data.get("value") else 0,
                    }
                    for h in holdings_list[:5]
                ],
            })
    except Exception as exc:
        logger.warning("Advisory holdings fragment error: %s", exc)
    return TEMPLATES.TemplateResponse(
        request, "_advisory.html", {"request": request, "wrappers": wrappers},
    )


@router.get("/fragments/advisory-sessions", response_class=HTMLResponse)
def advisory_sessions_fragment(request: Request):
    """HTMX fragment: recent advisory conversations."""
    conversations = []
    try:
        from data.trade_db import get_conn, DB_PATH
        conn = get_conn(DB_PATH)
        rows = conn.execute(
            "SELECT id, topic, last_active_at, message_count, status "
            "FROM advisor_sessions ORDER BY last_active_at DESC LIMIT 10"
        ).fetchall()
        for r in rows:
            conversations.append({
                "id": r["id"],
                "topic": r["topic"] or "General",
                "date": (r["last_active_at"] or "")[:16],
                "message_count": r["message_count"] or 0,
                "status": r["status"] or "active",
            })
    except Exception as exc:
        logger.debug("Advisory sessions fragment: %s", exc)
    return TEMPLATES.TemplateResponse(
        request, "_advisory_sessions.html",
        {"request": request, "conversations": conversations},
    )


@router.get("/fragments/advisory-chat", response_class=HTMLResponse)
def advisory_chat_fragment(request: Request, session_id: str = ""):
    """HTMX fragment: chat message history for current/latest session."""
    messages = []
    active_session_id = ""
    try:
        from intelligence.advisor import get_active_session, get_advisor_messages
        from data.trade_db import DB_PATH
        timeout = getattr(config, "ADVISOR_SESSION_TIMEOUT_HOURS", 4)
        session = get_active_session(DB_PATH, timeout_hours=timeout)
        if session_id:
            active_session_id = session_id
        elif session:
            active_session_id = session["id"]
        if active_session_id:
            raw_msgs = get_advisor_messages(DB_PATH, active_session_id, limit=50)
            for m in raw_msgs:
                messages.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                    "created_at": (m.get("created_at", ""))[:16],
                })
    except Exception as exc:
        logger.debug("Advisory chat fragment: %s", exc)
    return TEMPLATES.TemplateResponse(
        request, "_advisory_chat.html",
        {"request": request, "messages": messages, "session_id": active_session_id},
    )


@router.get("/fragments/advisory-memories", response_class=HTMLResponse)
def advisory_memories_fragment(request: Request, topic: str = ""):
    """HTMX fragment: memory search results."""
    memories = []
    try:
        from intelligence.advisor import search_advisor_memories
        from data.trade_db import DB_PATH
        memories = search_advisor_memories(DB_PATH, topic, limit=15)
    except Exception as exc:
        logger.debug("Advisory memories fragment: %s", exc)
    return TEMPLATES.TemplateResponse(
        request, "_advisory_memories.html",
        {"request": request, "memories": memories, "query": topic},
    )


@router.post("/api/advisory/chat")
async def advisory_chat_api(request: Request):
    """Web chat endpoint — processes message and returns updated chat HTML fragment."""
    if not config.ADVISOR_ENABLED:
        return HTMLResponse(
            '<div class="text-[10px] text-red-500 py-2 text-center">'
            'Advisory module disabled. Set ADVISOR_ENABLED=true</div>'
        )
    try:
        form = await request.form()
        message = str(form.get("message", "")).strip()
        if not message:
            return HTMLResponse("")

        from intelligence.advisor import get_active_session, get_advisor_messages
        from data.trade_db import DB_PATH

        engine = _get_advisory_engine()
        chat_id = int(config.NOTIFICATIONS.get("telegram_chat_id", "0") or "0")
        _response = engine.process_message(chat_id, message)

        # Return full updated chat
        timeout = getattr(config, "ADVISOR_SESSION_TIMEOUT_HOURS", 4)
        session = get_active_session(DB_PATH, timeout_hours=timeout)
        messages = []
        session_id = ""
        if session:
            session_id = session["id"]
            raw_msgs = get_advisor_messages(DB_PATH, session_id, limit=50)
            for m in raw_msgs:
                messages.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                    "created_at": (m.get("created_at", ""))[:16],
                })
        return TEMPLATES.TemplateResponse(
            request, "_advisory_chat.html",
            {"request": request, "messages": messages, "session_id": session_id},
        )
    except Exception as exc:
        logger.error("Advisory chat error: %s", exc, exc_info=True)
        return HTMLResponse(
            f'<div class="text-[10px] text-red-500 py-2 text-center">Error: {html.escape(str(exc))}</div>'
        )


# ── Advisory: transaction recording ────────────────────────────────────
@router.post("/api/advisory/transaction")
async def advisory_transaction_api(request: Request):
    """Record a transaction (buy/sell/deposit/withdrawal/dividend)."""
    try:
        form = await request.form()
        tx_type = str(form.get("tx_type", "")).strip().lower()
        wrapper = str(form.get("wrapper", "")).strip().upper()
        ticker = str(form.get("ticker", "")).strip().upper() or None
        quantity = float(form.get("quantity") or 0)
        price = float(form.get("price") or 0)
        amount = float(form.get("amount") or 0)
        notes = str(form.get("notes", "")).strip() or None

        from intelligence.advisory_holdings import (
            record_buy, record_sell, record_cash, record_dividend,
        )

        if tx_type == "buy":
            if not ticker or quantity <= 0 or price <= 0:
                return HTMLResponse(
                    '<span class="text-red-500">Buy requires ticker, quantity > 0, price > 0</span>'
                )
            record_buy(wrapper=wrapper, ticker=ticker, quantity=quantity, price=price, notes=notes)
            msg = f"Recorded buy: {ticker} x{quantity:.2f} @ \u00a3{price:.2f} in {wrapper}"

        elif tx_type == "sell":
            if not ticker or quantity <= 0 or price <= 0:
                return HTMLResponse(
                    '<span class="text-red-500">Sell requires ticker, quantity > 0, price > 0</span>'
                )
            result = record_sell(wrapper=wrapper, ticker=ticker, quantity=quantity, price=price, notes=notes)
            pnl = result.get("realized_pnl", 0)
            msg = f"Recorded sell: {ticker} x{quantity:.2f} @ \u00a3{price:.2f} in {wrapper} (P&L: \u00a3{pnl:.2f})"

        elif tx_type in ("deposit", "withdrawal"):
            if amount <= 0:
                return HTMLResponse(
                    '<span class="text-red-500">Deposit/withdrawal requires amount > 0</span>'
                )
            record_cash(wrapper=wrapper, tx_type=tx_type, amount=amount, notes=notes)
            msg = f"Recorded {tx_type}: \u00a3{amount:.2f} in {wrapper}"

        elif tx_type == "dividend":
            if amount <= 0:
                return HTMLResponse(
                    '<span class="text-red-500">Dividend requires amount > 0</span>'
                )
            record_dividend(wrapper=wrapper, ticker=ticker or "", amount=amount, notes=notes)
            msg = f"Recorded dividend: \u00a3{amount:.2f} for {ticker or 'cash'} in {wrapper}"

        else:
            return HTMLResponse(
                f'<span class="text-red-500">Unknown tx_type: {html.escape(tx_type)}</span>'
            )

        return HTMLResponse(f'<span class="text-emerald-600">{html.escape(msg)}</span>')

    except Exception as exc:
        logger.error("Transaction record error: %s", exc, exc_info=True)
        return HTMLResponse(
            f'<span class="text-red-500">Error: {html.escape(str(exc))}</span>'
        )


# ── Advisory: transaction history fragment ────────────────────────────
@router.get("/fragments/advisory-transactions", response_class=HTMLResponse)
def advisory_transactions_fragment(request: Request):
    """HTMX fragment: recent transactions."""
    try:
        from intelligence.advisory_holdings import get_transactions, get_transaction_summary

        transactions = get_transactions(limit=100)
        summary = get_transaction_summary()

        return TEMPLATES.TemplateResponse(
            request, "_advisory_transactions.html",
            {"request": request, "transactions": transactions, "summary": summary},
        )
    except Exception as exc:
        logger.error("Transactions fragment error: %s", exc, exc_info=True)
        return HTMLResponse(
            f'<div class="text-[10px] text-red-500 py-2 text-center">Error: {html.escape(str(exc))}</div>'
        )


# ── Advisory: RSS news fragment ───────────────────────────────────────
@router.get("/fragments/advisory-news", response_class=HTMLResponse)
def advisory_news_fragment(request: Request, refresh: str = ""):
    """HTMX fragment: recent RSS headlines."""
    try:
        from intelligence.advisor import get_recent_rss_headlines
        from data.trade_db import DB_PATH

        headlines = get_recent_rss_headlines(DB_PATH, hours=48, limit=30)

        return TEMPLATES.TemplateResponse(
            request, "_advisory_news.html",
            {"request": request, "headlines": headlines},
        )
    except Exception as exc:
        logger.error("News fragment error: %s", exc, exc_info=True)
        return HTMLResponse(
            f'<div class="text-[10px] text-red-500 py-2 text-center">Error: {html.escape(str(exc))}</div>'
        )


# ── Advisory: feed aggregator intel fragment ──────────────────────────
@router.get("/fragments/advisory-intel", response_class=HTMLResponse)
def advisory_intel_fragment(request: Request):
    """HTMX fragment: recent feed aggregator events."""
    try:
        from data.trade_db import DB_PATH, get_conn
        from datetime import timedelta

        conn = get_conn(DB_PATH)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        rows = conn.execute(
            """SELECT event_type, source, symbol, headline, detail, created_at
               FROM research_events
               WHERE created_at > ?
               ORDER BY created_at DESC
               LIMIT 30""",
            (cutoff,),
        ).fetchall()
        events = [dict(r) for r in rows]

        return TEMPLATES.TemplateResponse(
            request, "_advisory_intel.html",
            {"request": request, "events": events},
        )
    except Exception as exc:
        logger.error("Intel fragment error: %s", exc, exc_info=True)
        return HTMLResponse(
            f'<div class="text-[10px] text-red-500 py-2 text-center">Error: {html.escape(str(exc))}</div>'
        )
