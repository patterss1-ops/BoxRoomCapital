"""Webhook and intel API routes extracted from server.py."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import re as _re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import config
from app.api import intel_intake_helpers as _intel_intake_helpers
from app.api import social_bookmarklet_helpers as _social_bookmarklet_helpers
from app.api import tradingview_helpers as _tradingview_helpers
from app.api.shared import (
    PROJECT_ROOT,
    TEMPLATES,
    _ENGINE_B_SOURCE_SCORING,
    _INTEL_ANALYSIS_STALE_SECONDS,
    _TRADINGVIEW_RISK_LIMITS,
    _telegram_reply,
    _telegram_reply_long,
    _utc_now_iso,
    control,
    logger as shared_logger,
    _get_cached_value,
    _invalidate_research_cached_values,
    _parse_iso_datetime,
)
from app.api.shared import (
    DB_PATH,
    EventRecord,
    EventStore,
    FeatureStore,
    IntelSubmission,
    NormalizedTradingViewAlert,
    OrderIntent,
    OrderSide,
    PromotionGateConfig,
    RiskContext,
    RiskOrderRequest,
    RouteAccountType,
    RouteConfigEntry,
    RouteIntent,
    RoutePolicyState,
    SA_BROWSER_CAPTURE_EVENT_TYPE,
    SA_BROWSER_CAPTURE_SOURCE,
    SA_NETWORK_CAPTURE_SOURCE,
    SA_SYMBOL_CAPTURE_EVENT_TYPE,
    StrategyRequirements,
    TradingViewStrategySpec,
    WebhookValidationError,
    AccountRouter,
    analyze_intel_async,
    build_audit_detail,
    compute_event_id,
    create_job,
    create_order_intent_envelope,
    default_broker_resolver,
    evaluate_pre_trade_risk,
    evaluate_promotion_gate,
    extract_auth_token,
    get_active_strategy_parameter_set,
    get_conn,
    get_tradingview_strategy_registry,
    log_event,
    normalize_factor_grades,
    normalize_sa_symbol_snapshot,
    normalize_tradingview_alert,
    parse_json_payload,
    parse_sa_browser_payload,
    score_sa_quant_snapshot,
    store_factor_grades,
    summarize_payload,
    update_job,
    validate_expected_token,
    PreTradeRiskLimits,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


# ── Helpers (module-level, only used by webhooks) ─────────────────────────


def _safe_log_event(**kwargs: Any) -> None:
    """Best-effort event logging for non-critical API paths."""
    try:
        log_event(**kwargs)
    except Exception:
        return


def _expire_stale_intel_analysis_jobs(now: datetime | None = None) -> int:
    return _intel_intake_helpers._expire_stale_intel_analysis_jobs(
        get_conn=get_conn,
        db_path=DB_PATH,
        parse_iso_datetime=_parse_iso_datetime,
        update_job=update_job,
        stale_seconds=_INTEL_ANALYSIS_STALE_SECONDS,
        now=now,
    )


def _build_engine_b_submission_content(submission: IntelSubmission) -> str:
    return _intel_intake_helpers._build_engine_b_submission_content(submission)


def _queue_council_analysis(submission: IntelSubmission, *, detail: str) -> str:
    return _intel_intake_helpers._queue_council_analysis(
        submission,
        detail=detail,
        create_job=create_job,
        analyze_intel_async=analyze_intel_async,
    )


def _queue_engine_b_intake(
    *,
    raw_content: str,
    source_class: str,
    source_ids: list[str],
    detail: str,
    job_type: str = "engine_b_intake",
    source_credibility: float | None = None,
    allow_ad_hoc: bool = True,
) -> dict[str, Any]:
    return _intel_intake_helpers._queue_engine_b_intake(
        raw_content=raw_content,
        source_class=source_class,
        source_ids=source_ids,
        detail=detail,
        score_source=_ENGINE_B_SOURCE_SCORING.score_source,
        create_job=create_job,
        update_job=update_job,
        submit_engine_b_event=control.submit_engine_b_event,
        invalidate_research_cached_values=_invalidate_research_cached_values,
        job_type=job_type,
        source_credibility=source_credibility,
        allow_ad_hoc=allow_ad_hoc,
    )


def _build_sa_quant_engine_b_content(payload: dict[str, Any]) -> str:
    return _intel_intake_helpers._build_sa_quant_engine_b_content(payload)


def _build_finnhub_engine_b_content(payload: dict[str, Any]) -> str:
    return _intel_intake_helpers._build_finnhub_engine_b_content(payload)


def _finnhub_source_class(payload: dict[str, Any]) -> str:
    return _intel_intake_helpers._finnhub_source_class(payload)


def _decode_json_payload(value: Any) -> dict[str, Any]:
    return _tradingview_helpers._decode_json_payload(value)


def _tradingview_event_descriptor(alert: NormalizedTradingViewAlert) -> dict[str, Any]:
    return _tradingview_helpers._tradingview_event_descriptor(alert)


def _tradingview_event_id(alert: NormalizedTradingViewAlert) -> str:
    return _tradingview_helpers._tradingview_event_id(
        alert,
        compute_event_id=compute_event_id,
    )


def _resolve_tradingview_lane(
    strategy_id: str,
    db_path: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    return _tradingview_helpers._resolve_tradingview_lane(
        strategy_id,
        db_path,
        get_active_strategy_parameter_set=get_active_strategy_parameter_set,
    )


def _tradingview_action_semantics(action: str) -> tuple[str, bool]:
    return _tradingview_helpers._tradingview_action_semantics(
        action,
        order_side_enum=OrderSide,
    )


def _build_tradingview_route_state(engine_status: dict[str, Any]) -> RoutePolicyState:
    return _tradingview_helpers._build_tradingview_route_state(
        engine_status,
        route_policy_state_cls=RoutePolicyState,
    )


def _build_tradingview_router(spec: TradingViewStrategySpec) -> AccountRouter:
    return _tradingview_helpers._build_tradingview_router(
        spec,
        account_router_cls=AccountRouter,
        route_config_entry_cls=RouteConfigEntry,
        route_account_type_cls=RouteAccountType,
        default_broker_resolver=default_broker_resolver,
    )


def _get_tradingview_equity(db_path: str) -> float:
    return _tradingview_helpers._get_tradingview_equity(
        db_path,
        get_conn=get_conn,
    )


def _build_tradingview_risk_context(
    engine_status: dict[str, Any],
    db_path: str,
) -> Optional[RiskContext]:
    return _tradingview_helpers._build_tradingview_risk_context(
        engine_status,
        db_path,
        get_tradingview_equity=_get_tradingview_equity,
        get_conn=get_conn,
        risk_context_cls=RiskContext,
    )


def _estimate_tradingview_notional(
    alert: NormalizedTradingViewAlert,
    spec: TradingViewStrategySpec,
) -> float:
    return _tradingview_helpers._estimate_tradingview_notional(alert, spec)


def _build_tradingview_event_record(
    alert: NormalizedTradingViewAlert,
    lane: str,
    client_ip: str,
    state: str,
    intent_id: str = "",
    rejection_code: str = "",
    rejection_detail: str = "",
    duplicate_count: int = 0,
) -> EventRecord:
    return _tradingview_helpers._build_tradingview_event_record(
        alert,
        lane,
        client_ip,
        state,
        event_record_cls=EventRecord,
        utc_now_iso=_utc_now_iso,
        compute_event_id=compute_event_id,
        intent_id=intent_id,
        rejection_code=rejection_code,
        rejection_detail=rejection_detail,
        duplicate_count=duplicate_count,
    )


def _build_bookmarklet_href(js_source: str, endpoint: str) -> str:
    return _social_bookmarklet_helpers._build_bookmarklet_href(
        js_source,
        endpoint,
        re_module=_re,
    )


def _extract_bookmarklet_version(js_source: str) -> str:
    return _social_bookmarklet_helpers._extract_bookmarklet_version(
        js_source,
        re_module=_re,
    )


# ── X/Twitter helpers ─────────────────────────────────────────────────────


def _get_x_oauth() -> "OAuth1Session | None":
    return _social_bookmarklet_helpers._get_x_oauth(
        config_module=config,
        logger=logger,
        env_path=PROJECT_ROOT / ".env",
    )


def _fetch_single_tweet(oauth, tweet_id: str) -> dict | None:
    return _social_bookmarklet_helpers._fetch_single_tweet(
        oauth,
        tweet_id,
        logger=logger,
    )


def _resolve_author(data: dict, author_id: str) -> str:
    return _social_bookmarklet_helpers._resolve_author(data, author_id)


def _get_tweet_text(data: dict) -> str:
    return _social_bookmarklet_helpers._get_tweet_text(data)


def _describe_media(data: dict) -> str:
    return _social_bookmarklet_helpers._describe_media(data)


def _fetch_thread(oauth, conversation_id: str, author_username: str) -> list[str]:
    return _social_bookmarklet_helpers._fetch_thread(
        oauth,
        conversation_id,
        author_username,
        logger=logger,
    )


def _fetch_tweet_from_url(url: str) -> dict | None:
    return _social_bookmarklet_helpers._fetch_tweet_from_url(
        url,
        get_x_oauth=_get_x_oauth,
        fetch_single_tweet=_fetch_single_tweet,
        resolve_author=_resolve_author,
        get_tweet_text=_get_tweet_text,
        describe_media=_describe_media,
        fetch_thread=_fetch_thread,
        logger=logger,
        re_module=_re,
    )


# ── SA capture helpers (formerly closure inside create_app) ──────────────


def _store_sa_browser_capture(
    capture,
    *,
    retrieved_at: str,
    capture_source: str,
    log_strategy: str,
) -> tuple[int, Optional[dict[str, Any]]]:
    capture_source = str(capture_source or SA_BROWSER_CAPTURE_SOURCE).strip() or SA_BROWSER_CAPTURE_SOURCE
    capture_payload = capture.to_payload()
    store = EventStore()
    store.write_event(
        EventRecord(
            event_type=SA_BROWSER_CAPTURE_EVENT_TYPE,
            source=capture_source,
            source_ref=capture.snapshot.source_ref,
            retrieved_at=retrieved_at,
            event_timestamp=capture.snapshot.updated_at or retrieved_at,
            symbol=capture.ticker,
            headline=f"Seeking Alpha browser capture: {capture.ticker}",
            detail=(
                f"page_type={capture.page_type or 'unknown'}, "
                f"rating={capture.snapshot.rating or ''}, "
                f"grades={len(capture.factor_grades)}"
            ),
            confidence=0.99,
            provenance_descriptor={
                "ticker": capture.ticker,
                "url": capture.url,
                "page_type": capture.page_type,
                "capture_source": capture_source,
            },
            payload=capture_payload,
        )
    )

    stored_feature_count = 0
    if capture.factor_grades:
        features = normalize_factor_grades(capture.ticker, capture.factor_grades)
        if features:
            fs = FeatureStore(db_path=DB_PATH)
            try:
                if store_factor_grades(capture.ticker, features, fs, as_of=retrieved_at):
                    stored_feature_count = len(features)
            finally:
                fs.close()

    layer_score_payload: Optional[dict[str, Any]] = None
    if capture.has_quant_signal:
        layer_score = score_sa_quant_snapshot(
            snapshot=capture.snapshot,
            as_of=retrieved_at,
            source="sa-browser-capture",
        )
        layer_score_payload = layer_score.to_dict()
        store.write_event(
            EventRecord(
                event_type="signal_layer",
                source="sa-browser-capture",
                source_ref=layer_score.provenance_ref or capture.snapshot.source_ref,
                retrieved_at=retrieved_at,
                event_timestamp=retrieved_at,
                symbol=capture.ticker,
                headline="L8 SA Quant score",
                detail=(
                    f"ticker={capture.ticker}, score={layer_score.score}, "
                    f"rating={layer_score.details.get('rating', '')}"
                ),
                confidence=layer_score.confidence,
                provenance_descriptor={
                    "layer_id": layer_score.layer_id.value,
                    "ticker": capture.ticker,
                    "as_of": retrieved_at,
                    "capture_source": capture_source,
                },
                payload=layer_score_payload,
            )
        )

    _safe_log_event(
        category="SIGNAL",
        headline=f"SA capture received: {capture.ticker}",
        detail=(
            f"source={capture_source}, "
            f"page_type={capture.page_type or 'unknown'}, "
            f"has_quant={capture.has_quant_signal}, "
            f"grades={len(capture.factor_grades)}"
        ),
        ticker=capture.ticker,
        strategy=log_strategy,
    )

    return stored_feature_count, layer_score_payload


# ── SA intel helpers (formerly closures inside create_app) ────────────────


async def _decode_json_request(request: Request, *, max_bytes: int) -> dict[str, Any] | JSONResponse:
    try:
        body = await request.body()
        if len(body) > max_bytes:
            return JSONResponse(
                {"ok": False, "error": "payload_too_large"},
                status_code=413,
            )
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            {"ok": False, "error": "invalid_payload"},
            status_code=400,
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"ok": False, "error": "invalid_capture", "detail": "payload must be an object"},
            status_code=422,
        )
    return payload


def _normalize_sa_ticker_list(raw_value: Any) -> list[str]:
    if isinstance(raw_value, str):
        raw_value = [part.strip() for part in raw_value.split(",") if part.strip()]
    if not isinstance(raw_value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        clean = _re.sub(r"[^A-Z0-9.=\-]", "", str(item or "").upper()).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _normalize_sa_capture_url(payload: dict[str, Any], raw_fields: dict[str, Any]) -> str:
    for candidate in (
        payload.get("canonical_url"),
        raw_fields.get("canonical_url"),
        payload.get("source_ref"),
        payload.get("url"),
    ):
        clean = str(candidate or "").strip()
        if clean:
            return clean
    return ""


def _normalize_sa_capture_path(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    try:
        path = urlparse(clean).path or ""
    except Exception:
        return ""
    path = "/" + path.lstrip("/")
    path = _re.sub(r"/{2,}", "/", path)
    if path != "/":
        path = path.rstrip("/")
    return path.lower()


def _classify_sa_intel_url(url: str) -> str:
    path = _normalize_sa_capture_path(url)
    if _re.match(r"^/symbol/[^/]+", path):
        return "symbol"
    if _re.match(r"^/article/[^/]+", path):
        return "article"
    if _re.match(r"^/news/[^/]+", path):
        return "news"
    return ""


def _sa_intel_ignore_reason(page_type: str, url: str) -> str:
    path = _normalize_sa_capture_path(url)
    if not path:
        return "missing_url"
    if _re.match(r"^/market-news(?:/|$)", path) or path in {"/news", "/latest-news"}:
        return "hub_page"
    if page_type not in {"article", "news"}:
        return "unsupported_page"
    if _classify_sa_intel_url(url) not in {"article", "news"}:
        return "unsupported_page"
    return ""


def _find_existing_sa_intel_result(source_ref: str) -> str:
    clean_ref = str(source_ref or "").strip()
    if not clean_ref:
        return ""
    conn = get_conn(DB_PATH)
    try:
        completed = conn.execute(
            """SELECT id FROM research_events
               WHERE event_type = 'intel_analysis'
                 AND source = 'intel_seeking_alpha'
                 AND source_ref = ?
               LIMIT 1""",
            (clean_ref,),
        ).fetchone()
        if completed:
            return "duplicate_url"

        active = conn.execute(
            """SELECT id FROM jobs
               WHERE job_type IN ('intel_analysis', 'engine_b_intake')
                 AND status IN ('queued', 'running')
                 AND detail LIKE ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (f"SA intel: {clean_ref}%",),
        ).fetchone()
        if active:
            return "duplicate_inflight"
    finally:
        conn.close()
    return ""


def _queue_sa_intel_payload(
    payload: dict[str, Any],
    *,
    capture_source: str,
    log_strategy: str,
    store_page_capture_event: bool,
):
    content = str(payload.get("content") or payload.get("text") or "").strip()
    if not content:
        return JSONResponse(
            {"ok": False, "error": "missing_content", "detail": "No content field in payload."},
            status_code=422,
        )

    page_type = str(payload.get("page_type") or "article").strip().lower() or "article"
    tickers = _normalize_sa_ticker_list(payload.get("tickers") or payload.get("ticker") or [])
    raw_fields = dict(payload.get("raw_fields")) if isinstance(payload.get("raw_fields"), dict) else {}
    url = _normalize_sa_capture_url(payload, raw_fields)
    classified_page_type = _classify_sa_intel_url(url)
    if classified_page_type in {"article", "news"}:
        page_type = classified_page_type
    title = str(payload.get("title") or "").strip()
    author = str(payload.get("author") or "").strip()
    ignore_reason = _sa_intel_ignore_reason(page_type, url)
    if ignore_reason:
        return {
            "ok": True,
            "ignored": True,
            "reason": ignore_reason,
            "page_type": page_type,
            "tickers": tickers,
            "message": f"SA page capture ignored ({ignore_reason}).",
        }

    metadata = {
        "sa_rating": payload.get("rating"),
        "sa_grades": payload.get("grades"),
        "sa_page_type": page_type,
        "sa_excerpt": payload.get("summary") or payload.get("excerpt") or payload.get("description"),
        "sa_published_at": payload.get("published_at") or raw_fields.get("published_at"),
        "sa_canonical_url": payload.get("canonical_url") or raw_fields.get("canonical_url"),
        "sa_capture_source": capture_source,
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}

    submission = IntelSubmission(
        source="seeking_alpha",
        content=content,
        url=url,
        title=title,
        author=author,
        tickers=tickers,
        metadata=metadata,
    )

    if store_page_capture_event:
        retrieved_at = _utc_now_iso()
        EventStore(db_path=DB_PATH).write_event(
            EventRecord(
                event_type="sa_page_capture",
                source=capture_source,
                source_ref=str(
                    payload.get("source_ref")
                    or raw_fields.get("canonical_url")
                    or url
                    or f"sa-page-{uuid.uuid4()}"
                ).strip(),
                retrieved_at=retrieved_at,
                event_timestamp=str(payload.get("captured_at") or retrieved_at).strip() or retrieved_at,
                symbol=tickers[0] if tickers else None,
                headline=f"Seeking Alpha {page_type} capture: {(title or url)[:80]}",
                detail=(
                    f"tickers={','.join(tickers[:5]) or '-'}, "
                    f"content_length={len(content)}, "
                    f"author={(author or '-')[:80]}"
                ),
                confidence=0.9,
                provenance_descriptor={
                    "page_type": page_type,
                    "url": url,
                    "tickers": tickers[:10],
                    "capture_source": capture_source,
                },
                payload=dict(payload),
            )
        )

    duplicate_reason = _find_existing_sa_intel_result(submission.url or url)
    if duplicate_reason:
        duplicate_message = (
            f"SA {page_type} already queued for LLM analysis."
            if duplicate_reason == "duplicate_inflight"
            else f"SA {page_type} already analyzed."
        )
        return {
            "ok": True,
            "ignored": True,
            "reason": duplicate_reason,
            "page_type": page_type,
            "tickers": tickers,
            "message": duplicate_message,
        }
    job_detail_ref = submission.url or url or title or f"sa-{page_type}"
    engine_b_content = _build_engine_b_submission_content(submission)

    if config.RESEARCH_SYSTEM_ACTIVE:
        engine_b_result = _queue_engine_b_intake(
            raw_content=engine_b_content,
            source_class="news_wire",
            source_ids=[
                submission.url or "",
                *submission.tickers,
                f"sa:{page_type}",
            ],
            detail=f"SA intel: {job_detail_ref} | {(title or url)[:80]}",
        )
        if not engine_b_result.get("ok"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": engine_b_result.get("error", "enqueue_failed"),
                    "detail": engine_b_result.get("detail") or "Engine B enqueue failed.",
                },
                status_code=503,
            )
        job_id = str(engine_b_result["job_id"])
        message = f"SA {page_type} queued for Engine B research."
    else:
        job_id = _queue_council_analysis(
            submission,
            detail=f"SA intel: {job_detail_ref} | {(title or url)[:80]}",
        )
        engine_b_result = _queue_engine_b_intake(
            raw_content=engine_b_content,
            source_class="news_wire",
            source_ids=[
                submission.url or "",
                *submission.tickers,
                f"sa:{page_type}",
            ],
            detail=f"SA intel: {job_detail_ref} | {(title or url)[:80]}",
        )
        if not engine_b_result.get("ok"):
            logger.warning(
                "Engine B mirror enqueue failed for SA intel %s: %s",
                job_id,
                engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
            )
        message = f"SA {page_type} queued for LLM analysis."

    _safe_log_event(
        category="SIGNAL",
        headline=f"SA intel received: {(title or url)[:60]}",
        detail=(
            f"page_type={page_type}, "
            f"url={url or '-'}, "
            f"tickers={','.join(tickers[:5]) or '-'}"
        )[:500],
        ticker=tickers[0] if tickers else None,
        strategy=log_strategy,
    )

    return {
        "ok": True,
        "job_id": job_id,
        "research_job_id": (
            str(engine_b_result["job_id"])
            if not config.RESEARCH_SYSTEM_ACTIVE and engine_b_result.get("ok")
            else None
        ),
        "page_type": page_type,
        "tickers": tickers,
        "message": message,
    }


def _handle_sa_symbol_capture_payload(payload: dict[str, Any]):
    """Receive a full Seeking Alpha symbol snapshot captured in-browser."""
    summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    summary_payload = dict(summary_payload)
    for key in (
        "ticker",
        "url",
        "title",
        "page_type",
        "captured_at",
        "source",
        "source_ref",
        "bookmarklet_version",
        "rating",
        "quant_score",
        "author_rating",
        "wall_st_rating",
        "grades",
        "sa_history",
        "scan_debug",
    ):
        if key not in summary_payload and payload.get(key) not in (None, "", [], {}):
            summary_payload[key] = payload.get(key)

    capture_source = str(
        summary_payload.get("source")
        or payload.get("source")
        or SA_NETWORK_CAPTURE_SOURCE
    ).strip() or SA_NETWORK_CAPTURE_SOURCE
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    raw_responses = payload.get("raw_responses") if isinstance(payload.get("raw_responses"), list) else []
    raw_fields = (
        dict(summary_payload.get("raw_fields"))
        if isinstance(summary_payload.get("raw_fields"), dict)
        else {}
    )
    raw_fields.setdefault("source", capture_source)
    raw_fields.setdefault("section_names", sorted(str(key) for key in sections.keys()))
    raw_fields.setdefault("raw_response_count", len(raw_responses))
    summary_payload["raw_fields"] = raw_fields

    normalized_snapshot = normalize_sa_symbol_snapshot(
        {
            **payload,
            "summary": summary_payload,
            "sections": sections,
            "raw_responses": raw_responses,
        }
    )
    summary_payload = (
        dict(normalized_snapshot.get("summary"))
        if isinstance(normalized_snapshot.get("summary"), dict)
        else summary_payload
    )
    normalized_sections = (
        dict(normalized_snapshot.get("normalized_sections"))
        if isinstance(normalized_snapshot.get("normalized_sections"), dict)
        else {}
    )
    if normalized_sections:
        summary_payload["normalized_sections"] = normalized_sections
        raw_fields = (
            dict(summary_payload.get("raw_fields"))
            if isinstance(summary_payload.get("raw_fields"), dict)
            else {}
        )
        raw_fields["normalized_section_names"] = sorted(str(key) for key in normalized_sections.keys())
        summary_payload["raw_fields"] = raw_fields

    symbol = str(summary_payload.get("ticker") or payload.get("ticker") or "").strip().upper()
    if not symbol:
        match = _re.search(
            r"/symbol/([A-Z.=\-]+)",
            str(summary_payload.get("url") or payload.get("url") or ""),
            _re.IGNORECASE,
        )
        if match:
            symbol = match.group(1).upper()
    if not symbol:
        return JSONResponse(
            {"ok": False, "error": "invalid_capture", "detail": "ticker is required"},
            status_code=422,
        )

    retrieved_at = _utc_now_iso()
    symbol_payload = dict(payload)
    symbol_payload["summary"] = summary_payload
    symbol_payload["normalized_sections"] = normalized_sections
    store = EventStore()
    store.write_event(
        EventRecord(
            event_type=SA_SYMBOL_CAPTURE_EVENT_TYPE,
            source=capture_source,
            source_ref=str(
                payload.get("source_ref")
                or summary_payload.get("source_ref")
                or summary_payload.get("url")
                or payload.get("url")
                or f"sa-symbol-{symbol}"
            ).strip(),
            retrieved_at=retrieved_at,
            event_timestamp=str(
                payload.get("captured_at")
                or summary_payload.get("captured_at")
                or retrieved_at
            ).strip()
            or retrieved_at,
            symbol=symbol,
            headline=f"Seeking Alpha symbol capture: {symbol}",
            detail=(
                f"sections={len(sections)}, "
                f"normalized_sections={len(normalized_sections)}, "
                f"raw_responses={len(raw_responses)}, "
                f"has_summary={bool(summary_payload)}"
            ),
            confidence=0.99,
            provenance_descriptor={
                "ticker": symbol,
                "url": summary_payload.get("url") or payload.get("url") or "",
                "page_type": "symbol",
                "capture_source": capture_source,
                "section_names": sorted(
                    {*(str(key) for key in sections.keys()), *(str(key) for key in normalized_sections.keys())}
                )[:20],
            },
            payload=symbol_payload,
        )
    )

    capture = None
    stored_feature_count = 0
    layer_score_payload: Optional[dict[str, Any]] = None
    try:
        capture = parse_sa_browser_payload(summary_payload)
    except ValueError:
        capture = None

    if capture is not None:
        capture_source = str(
            capture.snapshot.raw_fields.get("source") or capture_source
        ).strip() or capture_source
        stored_feature_count, layer_score_payload = _store_sa_browser_capture(
            capture,
            retrieved_at=retrieved_at,
            capture_source=capture_source,
            log_strategy="sa_symbol_capture",
        )
    else:
        _safe_log_event(
            category="SIGNAL",
            headline=f"SA symbol snapshot received: {symbol}",
            detail=(
                f"source={capture_source}, "
                f"sections={len(sections)}, "
                f"normalized_sections={len(normalized_sections)}, "
                f"raw_responses={len(raw_responses)}"
            ),
            ticker=symbol,
            strategy="sa_symbol_capture",
        )

    return {
        "ok": True,
        "ticker": symbol,
        "section_count": len(sections),
        "normalized_section_count": len(normalized_sections),
        "raw_response_count": len(raw_responses),
        "rating": capture.snapshot.rating if capture is not None else "",
        "quant_score": capture.snapshot.quant_score_raw if capture is not None else None,
        "factor_grades": capture.factor_grades if capture is not None else {},
        "feature_count": stored_feature_count,
        "layer_score": layer_score_payload,
        "message": "SA symbol capture stored.",
    }


# ── Route handlers ───────────────────────────────────────────────────────


@router.get("/api/tradingview/alerts")
def api_tradingview_alerts(limit: int = 50, state: str = "", strategy_id: str = ""):
    store = EventStore(db_path=DB_PATH)
    requested_state = str(state or "").strip().lower()
    requested_strategy = str(strategy_id or "").strip().lower()
    rows = store.list_events(limit=max(limit * 3, limit), source="tradingview")

    items: list[dict[str, Any]] = []
    for row in rows:
        payload = _decode_json_payload(row.get("payload"))
        payload_state = str(payload.get("state") or "").strip().lower()
        payload_strategy = str(payload.get("strategy_id") or "").strip().lower()
        if requested_state and payload_state != requested_state:
            continue
        if requested_strategy and payload_strategy != requested_strategy:
            continue
        row["payload"] = payload
        items.append(row)
        if len(items) >= limit:
            break
    return {"items": items}


@router.post("/api/webhooks/tradingview")
async def tradingview_webhook(request: Request, token: str = ""):
    payload: Optional[dict[str, Any]] = None
    alert: Optional[NormalizedTradingViewAlert] = None
    client_ip = request.client.host if request.client else "-"
    try:
        payload = parse_json_payload(
            raw_body=await request.body(),
            max_payload_bytes=config.TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES,
        )
        provided_token = extract_auth_token(
            payload=payload,
            header_token=request.headers.get("x-webhook-token", ""),
            query_token=token,
        )
        validate_expected_token(
            expected_token=config.TRADINGVIEW_WEBHOOK_TOKEN,
            provided_token=provided_token,
        )
        alert = normalize_tradingview_alert(
            payload=payload,
            registry=get_tradingview_strategy_registry(),
            max_age_seconds=config.TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS,
        )
    except WebhookValidationError as exc:
        _safe_log_event(
            category="REJECTION",
            headline="TradingView webhook rejected",
            detail=build_audit_detail(
                reason=exc.message,
                client_ip=client_ip,
                payload=payload,
            ),
            ticker=(str(payload.get("symbol") or payload.get("ticker")) if payload else None),
            strategy="tradingview_webhook",
        )
        return JSONResponse(
            {"ok": False, "error": exc.code, "detail": exc.message},
            status_code=exc.status_code,
        )

    spec = get_tradingview_strategy_registry()[alert.strategy_id]
    store = EventStore(db_path=DB_PATH)
    existing = store.get_event(_tradingview_event_id(alert))
    if existing:
        existing_payload = _decode_json_payload(existing.get("payload"))
        duplicate_count = int(existing_payload.get("duplicate_count") or 0) + 1
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=str(existing_payload.get("lane") or "unknown"),
                client_ip=client_ip,
                state=str(existing_payload.get("state") or "accepted"),
                intent_id=str(existing_payload.get("intent_id") or ""),
                rejection_code=str(existing_payload.get("rejection_code") or ""),
                rejection_detail=str(existing_payload.get("rejection_detail") or ""),
                duplicate_count=duplicate_count,
            )
        )
        _safe_log_event(
            category="SIGNAL",
            headline=f"TradingView alert duplicate: {alert.action} {alert.ticker}",
            detail=build_audit_detail(reason="duplicate", client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )
        return {
            "ok": True,
            "state": "duplicate",
            "ticker": alert.ticker,
            "action": alert.action,
            "strategy": alert.strategy_id,
            "timeframe": alert.timeframe,
            "duplicate_count": duplicate_count,
        }

    lane, _lane_item = _resolve_tradingview_lane(alert.strategy_id, db_path=DB_PATH)
    if lane == "missing":
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=lane,
                client_ip=client_ip,
                state="rejected",
                rejection_code="NO_LANE_DATA",
                rejection_detail="Strategy has no shadow/staged_live/live parameter set.",
            )
        )
        _safe_log_event(
            category="REJECTION",
            headline="TradingView webhook rejected",
            detail=build_audit_detail(reason="no lane data", client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )
        return JSONResponse(
            {"ok": False, "error": "NO_LANE_DATA", "detail": "Strategy has no configured promotion lane."},
            status_code=409,
        )

    if lane != "live":
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=lane,
                client_ip=client_ip,
                state="audit_only",
            )
        )
        _safe_log_event(
            category="SIGNAL",
            headline=f"TradingView alert audited ({lane}): {alert.action} {alert.ticker}",
            detail=build_audit_detail(reason=f"audit_only:{lane}", client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )
        return {
            "ok": True,
            "state": "audit_only",
            "lane": lane,
            "message": "TradingView alert accepted and stored for audit. No order intent created in this lane.",
            "ticker": alert.ticker,
            "action": alert.action,
            "strategy": alert.strategy_id,
            "timeframe": alert.timeframe,
        }

    engine_status = control.status()
    route_state = _build_tradingview_route_state(engine_status)
    requirements = StrategyRequirements(**dict(spec.requirements))
    route_decision = _build_tradingview_router(spec).resolve(
        RouteIntent(
            strategy_id=spec.strategy_id,
            sleeve=spec.sleeve,
            ticker=alert.ticker,
            requirements=requirements,
        ),
        policy_state=route_state,
    )
    if not route_decision.allowed:
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=lane,
                client_ip=client_ip,
                state="rejected",
                rejection_code=str(route_decision.reason_code).upper(),
                rejection_detail=str(route_decision.message),
            )
        )
        _safe_log_event(
            category="REJECTION",
            headline="TradingView webhook rejected",
            detail=build_audit_detail(reason=route_decision.message, client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )
        return JSONResponse(
            {"ok": False, "error": str(route_decision.reason_code).upper(), "detail": route_decision.message},
            status_code=403 if route_decision.reason_code in {"kill_switch_active", "market_cooldown_active"} else 422,
        )

    side_str, is_exit = _tradingview_action_semantics(alert.action)
    risk_context = _build_tradingview_risk_context(engine_status, db_path=DB_PATH)
    if risk_context is not None and not is_exit:
        notional = _estimate_tradingview_notional(alert, spec)
        risk_decision = evaluate_pre_trade_risk(
            request=RiskOrderRequest(
                ticker=alert.ticker,
                sleeve=spec.sleeve,
                order_exposure_notional=notional,
            ),
            context=risk_context,
            limits=_TRADINGVIEW_RISK_LIMITS,
        )
        if not risk_decision.approved:
            store.write_event(
                _build_tradingview_event_record(
                    alert=alert,
                    lane=lane,
                    client_ip=client_ip,
                    state="rejected",
                    rejection_code=risk_decision.rule_id,
                    rejection_detail=risk_decision.message,
                )
            )
            _safe_log_event(
                category="REJECTION",
                headline="TradingView webhook rejected",
                detail=build_audit_detail(reason=risk_decision.message, client_ip=client_ip, payload=payload),
                ticker=alert.ticker,
                strategy=alert.strategy_id,
            )
            return JSONResponse(
                {"ok": False, "error": risk_decision.rule_id, "detail": risk_decision.message},
                status_code=409,
            )

    promo_decision = evaluate_promotion_gate(
        strategy_key=spec.strategy_id,
        is_exit=is_exit,
        config=PromotionGateConfig(enabled=True, require_live_set=True),
        db_path=DB_PATH,
    )
    if not promo_decision.allowed:
        store.write_event(
            _build_tradingview_event_record(
                alert=alert,
                lane=lane,
                client_ip=client_ip,
                state="rejected",
                rejection_code=promo_decision.reason_code,
                rejection_detail=promo_decision.message,
            )
        )
        _safe_log_event(
            category="REJECTION",
            headline="TradingView webhook rejected",
            detail=build_audit_detail(reason=promo_decision.message, client_ip=client_ip, payload=payload),
            ticker=alert.ticker,
            strategy=alert.strategy_id,
        )
        return JSONResponse(
            {"ok": False, "error": promo_decision.reason_code, "detail": promo_decision.message},
            status_code=409,
        )

    resolved = route_decision.resolution
    metadata = {
        "source": "tradingview_webhook",
        "source_ref": alert.source_ref,
        "schema_version": alert.schema_version,
        "alert_id": alert.alert_id,
        "signal_timestamp": alert.event_timestamp,
        "signal_price": alert.signal_price,
        "indicators": dict(alert.indicators),
        "raw_payload": payload,
        "is_exit": is_exit,
    }
    intent = OrderIntent(
        strategy_id=spec.strategy_id,
        strategy_version=spec.strategy_version,
        sleeve=spec.sleeve,
        account_type=resolved.account_type if resolved is not None else spec.account_type,
        broker_target=resolved.broker_name if resolved is not None else spec.broker_target,
        instrument=alert.ticker,
        side=side_str,
        qty=spec.base_qty,
        order_type="MARKET",
        risk_tags=list(spec.risk_tags),
        metadata=metadata,
    )
    envelope = create_order_intent_envelope(
        intent=intent,
        action_type="tradingview_signal",
        actor="system",
        correlation_id=alert.correlation_id,
        db_path=DB_PATH,
    )
    intent_id = envelope.get("intent_id", "")
    store.write_event(
        _build_tradingview_event_record(
            alert=alert,
            lane=lane,
            client_ip=client_ip,
            state="intent_created",
            intent_id=intent_id,
        )
    )

    _safe_log_event(
        category="SIGNAL",
        headline=f"TradingView alert accepted: {alert.action} {alert.ticker}",
        detail=build_audit_detail(reason="intent_created", client_ip=client_ip, payload=payload),
        ticker=alert.ticker,
        strategy=alert.strategy_id,
    )

    return {
        "ok": True,
        "state": "intent_created",
        "message": "TradingView alert accepted and order intent created.",
        "ticker": alert.ticker,
        "action": alert.action,
        "strategy": alert.strategy_id,
        "timeframe": alert.timeframe,
        "intent_id": intent_id,
        "lane": lane,
    }


@router.get("/api/webhooks/sa_debug_ping")
async def sa_debug_ping(
    stage: str = "",
    v: str = "",
    href: str = "",
    host: str = "",
    page_type: str = "",
):
    """Lightweight debug beacon for bookmarklet execution tracing."""
    _safe_log_event(
        category="DEBUG",
        headline=f"SA bookmarklet ping: {stage or 'unknown'}",
        detail=(
            f"v={v or '-'}, host={host or '-'}, page_type={page_type or '-'}, "
            f"url={href or '-'}"
        )[:500],
        ticker=None,
        strategy="sa_debug_ping",
    )
    return Response(status_code=204)


@router.post("/api/webhooks/sa_intel")
async def sa_intel_webhook(request: Request):
    """Receive Seeking Alpha page data from browser bookmarklet.

    Expects JSON with: title, content, url, tickers (optional), author (optional).
    Runs LLM council analysis in background and returns job ID.
    Sync work (DB writes, job enqueue) runs in threadpool.
    """
    payload = await _decode_json_request(request, max_bytes=256_000)
    if isinstance(payload, JSONResponse):
        return payload
    return await asyncio.to_thread(
        _queue_sa_intel_payload,
        payload,
        capture_source=str(payload.get("source") or SA_BROWSER_CAPTURE_SOURCE).strip() or SA_BROWSER_CAPTURE_SOURCE,
        log_strategy="sa_intel",
        store_page_capture_event=False,
    )


@router.post("/api/webhooks/sa_quant_capture")
async def sa_quant_capture_webhook(request: Request):
    """Receive structured SA quant data captured from the user's browser."""
    payload = await _decode_json_request(request, max_bytes=128_000)
    if isinstance(payload, JSONResponse):
        return payload

    try:
        capture = parse_sa_browser_payload(payload)
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "error": "invalid_capture", "detail": str(exc)},
            status_code=422,
        )

    retrieved_at = _utc_now_iso()
    capture_source = str(
        capture.snapshot.raw_fields.get("source")
        or payload.get("source")
        or SA_BROWSER_CAPTURE_SOURCE
    ).strip() or SA_BROWSER_CAPTURE_SOURCE
    stored_feature_count, layer_score_payload = _store_sa_browser_capture(
        capture,
        retrieved_at=retrieved_at,
        capture_source=capture_source,
        log_strategy="sa_quant_capture",
    )
    engine_b_result = _queue_engine_b_intake(
        raw_content=_build_sa_quant_engine_b_content(payload),
        source_class="sa_quant",
        source_ids=[
            capture.url or str(payload.get("url") or ""),
            capture.ticker,
            str(payload.get("captured_at") or retrieved_at),
        ],
        detail=f"SA quant capture: {capture.ticker}",
    )
    if not engine_b_result.get("ok"):
        logger.warning(
            "Engine B enqueue failed for SA quant capture %s: %s",
            capture.ticker,
            engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
        )

    return {
        "ok": True,
        "ticker": capture.ticker,
        "rating": capture.snapshot.rating,
        "quant_score": capture.snapshot.quant_score_raw,
        "factor_grades": capture.factor_grades,
        "feature_count": stored_feature_count,
        "layer_score": layer_score_payload,
        "research_job_id": str(engine_b_result["job_id"]) if engine_b_result.get("ok") else None,
        "research_error": None if engine_b_result.get("ok") else engine_b_result.get("detail"),
        "message": "SA browser capture stored.",
    }


@router.post("/api/webhooks/sa_symbol_capture")
async def sa_symbol_capture_webhook(request: Request):
    payload = await _decode_json_request(request, max_bytes=1_500_000)
    if isinstance(payload, JSONResponse):
        return payload
    return _handle_sa_symbol_capture_payload(payload)


@router.post("/api/webhooks/sa_page_capture")
async def sa_page_capture_webhook(request: Request):
    """Universal Seeking Alpha capture endpoint for symbols, analysis, and news pages."""
    payload = await _decode_json_request(request, max_bytes=1_500_000)
    if isinstance(payload, JSONResponse):
        return payload

    page_type = str(payload.get("page_type") or "").strip().lower()
    if page_type == "symbol" or payload.get("sections") or payload.get("raw_responses"):
        return _handle_sa_symbol_capture_payload(payload)

    capture_source = str(
        payload.get("source")
        or (
            payload.get("summary", {}).get("source")
            if isinstance(payload.get("summary"), dict)
            else ""
        )
        or SA_NETWORK_CAPTURE_SOURCE
    ).strip() or SA_NETWORK_CAPTURE_SOURCE
    return _queue_sa_intel_payload(
        payload,
        capture_source=capture_source,
        log_strategy="sa_page_capture",
        store_page_capture_event=True,
    )


@router.post("/api/webhooks/finnhub")
async def finnhub_webhook(request: Request):
    """Receive structured Finnhub article/transcript payloads for Engine B."""
    payload = await _decode_json_request(request, max_bytes=256_000)
    if isinstance(payload, JSONResponse):
        return payload

    content = _build_finnhub_engine_b_content(payload)
    if not content:
        return JSONResponse(
            {"ok": False, "error": "missing_content", "detail": "No content or summary field in payload."},
            status_code=422,
        )

    ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip().upper()
    title = str(payload.get("title") or payload.get("headline") or "").strip()
    result = _queue_engine_b_intake(
        raw_content=content,
        source_class=_finnhub_source_class(payload),
        source_ids=[
            str(payload.get("url") or "").strip(),
            ticker,
            str(payload.get("published_at") or payload.get("datetime") or "").strip(),
        ],
        detail=f"Finnhub intake: {(title or ticker or 'event')[:80]}",
    )
    if not result.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "error": result.get("error", "enqueue_failed"),
                "detail": result.get("detail") or "Engine B enqueue failed.",
            },
            status_code=503,
        )

    _safe_log_event(
        category="SIGNAL",
        headline=f"Finnhub intake received: {(title or ticker or 'event')[:60]}",
        detail=f"ticker={ticker or '-'}, source_class={_finnhub_source_class(payload)}",
        ticker=ticker or None,
        strategy="finnhub_webhook",
    )

    return {
        "ok": True,
        "job_id": result["job_id"],
        "ticker": ticker,
        "message": "Finnhub event queued for Engine B research.",
    }


@router.post("/api/webhooks/x_intel")
async def x_intel_webhook(request: Request):
    """Receive X/Twitter content for LLM analysis.

    Expects JSON with: content (tweet/thread text), url (optional),
    author (optional), tickers (optional).
    """
    payload = await _decode_json_request(request, max_bytes=256_000)
    if isinstance(payload, JSONResponse):
        return payload

    content = str(payload.get("content") or payload.get("text") or "").strip()
    if not content:
        return JSONResponse(
            {"ok": False, "error": "missing_content", "detail": "No content field in payload."},
            status_code=422,
        )

    tickers_raw = payload.get("tickers") or []
    if isinstance(tickers_raw, str):
        tickers_raw = [t.strip() for t in tickers_raw.split(",") if t.strip()]

    submission = IntelSubmission(
        source="x_twitter",
        content=content,
        url=str(payload.get("url", "")),
        title=str(payload.get("title") or payload.get("author", "")),
        author=str(payload.get("author", "")),
        tickers=tickers_raw,
    )
    engine_b_content = _build_engine_b_submission_content(submission)

    if config.RESEARCH_SYSTEM_ACTIVE:
        engine_b_result = _queue_engine_b_intake(
            raw_content=engine_b_content,
            source_class="social_curated",
            source_ids=[
                submission.url or "",
                submission.author or "",
                *submission.tickers,
            ],
            detail=f"X intel: {submission.title[:80]}",
        )
        if not engine_b_result.get("ok"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": engine_b_result.get("error", "enqueue_failed"),
                    "detail": engine_b_result.get("detail") or "Engine B enqueue failed.",
                },
                status_code=503,
            )
        job_id = str(engine_b_result["job_id"])
        research_job_id = None
        message = "X intel queued for Engine B research."
    else:
        job_id = _queue_council_analysis(
            submission,
            detail=f"X intel: {submission.title[:80]}",
        )
        engine_b_result = _queue_engine_b_intake(
            raw_content=engine_b_content,
            source_class="social_curated",
            source_ids=[
                submission.url or "",
                submission.author or "",
                *submission.tickers,
            ],
            detail=f"X intel: {submission.title[:80]}",
        )
        if not engine_b_result.get("ok"):
            logger.warning(
                "Engine B mirror enqueue failed for X intel %s: %s",
                job_id,
                engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
            )
        research_job_id = str(engine_b_result["job_id"]) if engine_b_result.get("ok") else None
        message = "X intel queued for LLM analysis."

    _safe_log_event(
        category="SIGNAL",
        headline=f"X intel received: {submission.author or 'unknown'}",
        detail=f"url={submission.url}, content_len={len(content)}",
        strategy="x_intel",
    )

    return {
        "ok": True,
        "job_id": job_id,
        "research_job_id": research_job_id,
        "message": message,
    }


@router.post("/api/webhooks/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram bot updates (forwarded messages, commands).

    Set up via: POST https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_URL>/api/webhooks/telegram
    """
    try:
        payload = json.loads((await request.body()).decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"ok": False}, status_code=400)

    message = payload.get("message") or payload.get("channel_post") or {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return {"ok": True}  # Acknowledge but ignore non-text updates

    expected_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if expected_chat and str(chat_id) != expected_chat:
        return {"ok": True}  # Ignore messages from unknown chats

    # Check if it's an X/Twitter link or forwarded content
    is_x_content = any(domain in text for domain in [
        "twitter.com/", "x.com/", "nitter.", "vxtwitter.com/",
    ])

    # Extract URL if present
    urls = _re.findall(r'https?://\S+', text)
    url = urls[0] if urls else ""

    if is_x_content or text.startswith("/analyze"):
        # Strip /analyze command prefix if present
        content = text.replace("/analyze", "", 1).strip() if text.startswith("/analyze") else text

        # If it's an X link, fetch the full tweet via API
        if is_x_content and url:
            logger.info("Fetching tweet for URL: %s (X_CONSUMER_KEY set: %s)", url, bool(config.X_CONSUMER_KEY))
            try:
                tweet_data = _fetch_tweet_from_url(url)
                logger.info("Tweet fetch result: %s", "success" if tweet_data else "failed/None")
            except Exception as exc:
                logger.error("Tweet fetch EXCEPTION: %s", exc, exc_info=True)
                tweet_data = None
            if tweet_data:
                content = tweet_data["text"]
                if tweet_data.get("author"):
                    content = f"@{tweet_data['author']}: {content}"
                if tweet_data.get("created_at"):
                    content += f"\n\n[Posted: {tweet_data['created_at']}]"

        submission = IntelSubmission(
            source="x_twitter" if is_x_content else "telegram",
            content=content,
            url=url,
            title=f"Forwarded via Telegram",
        )
        engine_b_content = _build_engine_b_submission_content(submission)

        if config.RESEARCH_SYSTEM_ACTIVE:
            engine_b_result = _queue_engine_b_intake(
                raw_content=engine_b_content,
                source_class="social_curated",
                source_ids=[url, f"telegram:{chat_id}"],
                detail=f"Telegram intel: {content[:80]}",
            )
            if not engine_b_result.get("ok"):
                _telegram_reply(
                    chat_id,
                    f"Engine B enqueue failed: {engine_b_result.get('detail') or engine_b_result.get('error', 'unknown')}",
                )
                return JSONResponse({"ok": False}, status_code=503)
            _telegram_reply(chat_id, f"Queued for Engine B research (job {str(engine_b_result['job_id'])[:8]})")
        else:
            job_id = _queue_council_analysis(
                submission,
                detail=f"Telegram intel: {content[:80]}",
            )
            engine_b_result = _queue_engine_b_intake(
                raw_content=engine_b_content,
                source_class="social_curated",
                source_ids=[url, f"telegram:{chat_id}", f"legacy:{job_id[:8]}"],
                detail=f"Telegram intel: {content[:80]}",
            )
            if not engine_b_result.get("ok"):
                logger.warning(
                    "Engine B mirror enqueue failed for Telegram intel %s: %s",
                    job_id,
                    engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
                )
            _telegram_reply(chat_id, f"Queued for LLM analysis (job {job_id[:8]})")
    elif text == "/status":
        status = control.status()
        mode = status.get("mode", "unknown")
        _telegram_reply(chat_id, f"Bot: {mode}\nKill switch: {'ON' if status.get('kill_switch_active') else 'off'}")
    elif text == "/help":
        _telegram_reply(
            chat_id,
            "Commands:\n"
            "/analyze <text> - Analyze content for trade ideas\n"
            "/status - Bot status\n"
            "/advisor - Toggle advisory mode\n"
            "/recall <topic> - Search advisory memory\n"
            "/holdings - Show portfolio holdings\n"
            "/add <WRAPPER> <TICKER> <QTY> <COST> - Add holding\n"
            "/close <TICKER> <PRICE> - Close holding\n"
            "/performance - Portfolio performance report\n"
            "Forward X/Twitter links to auto-analyze\n"
            "Paste any text to analyze (or chat with advisor)",
        )
    elif text == "/advisor":
        if config.ADVISOR_ENABLED:
            _telegram_reply(chat_id, "Advisory mode is ON. Send any message to chat with your advisor.")
        else:
            _telegram_reply(chat_id, "Advisory module is disabled. Set ADVISOR_ENABLED=true in .env")
    elif text.startswith("/recall"):
        topic = text.replace("/recall", "", 1).strip()
        if not topic:
            _telegram_reply(chat_id, "Usage: /recall <topic>\nExample: /recall bonds")
        elif config.ADVISOR_ENABLED:
            try:
                from app.api.routes.advisory import _get_advisory_engine
                engine = _get_advisory_engine()
                result = engine.recall(topic)
                _telegram_reply_long(chat_id, result)
            except Exception as exc:
                logger.error("Recall error: %s", exc, exc_info=True)
                _telegram_reply(chat_id, f"Recall failed: {exc}")
        else:
            _telegram_reply(chat_id, "Advisory module is disabled.")
    elif text == "/holdings":
        try:
            from intelligence.advisory_holdings import format_holdings_telegram
            result = format_holdings_telegram()
            _telegram_reply_long(chat_id, result)
        except Exception as exc:
            logger.error("Holdings error: %s", exc, exc_info=True)
            _telegram_reply(chat_id, f"Holdings failed: {exc}")
    elif text.startswith("/add "):
        parts = text.split()
        if len(parts) < 5:
            _telegram_reply(chat_id, "Usage: /add <WRAPPER> <TICKER> <QTY> <AVG_COST>\nExample: /add ISA VWRL.L 100 85.50")
        else:
            try:
                from intelligence.advisory_holdings import add_holding
                wrapper = parts[1].upper()
                ticker = parts[2]
                qty = float(parts[3])
                cost = float(parts[4])
                holding_id = add_holding(wrapper=wrapper, ticker=ticker, quantity=qty, avg_cost=cost)
                _telegram_reply(chat_id, f"Added: {qty} {ticker} in {wrapper} @ {cost}\nID: {holding_id[:8]}")
            except Exception as exc:
                logger.error("Add holding error: %s", exc, exc_info=True)
                _telegram_reply(chat_id, f"Failed: {exc}")
    elif text.startswith("/close "):
        parts = text.split()
        if len(parts) < 3:
            _telegram_reply(chat_id, "Usage: /close <TICKER> <PRICE>\nExample: /close VWRL.L 91.20")
        else:
            try:
                from intelligence.advisory_holdings import get_holdings, close_holding
                ticker = parts[1]
                price = float(parts[2])
                holdings = get_holdings(status="open")
                matched = [h for h in holdings if h["ticker"] == ticker]
                if not matched:
                    _telegram_reply(chat_id, f"No open holding found for {ticker}")
                else:
                    result = close_holding(matched[0]["id"], price)
                    _telegram_reply(chat_id, f"Closed {ticker} @ {price}\nP&L: {result.get('realized_pnl', 0):+.2f}")
            except Exception as exc:
                logger.error("Close holding error: %s", exc, exc_info=True)
                _telegram_reply(chat_id, f"Failed: {exc}")
    elif text == "/performance":
        try:
            from intelligence.advisory_holdings import format_performance_telegram
            result = format_performance_telegram()
            _telegram_reply_long(chat_id, result)
        except Exception as exc:
            logger.error("Performance error: %s", exc, exc_info=True)
            _telegram_reply(chat_id, f"Performance failed: {exc}")
    elif config.ADVISOR_ENABLED and not is_x_content:
        # Route to advisory engine for conversational interaction
        try:
            from app.api.routes.advisory import _get_advisory_engine
            engine = _get_advisory_engine()
            response = engine.process_message(chat_id, text)
            _telegram_reply_long(chat_id, response)
        except Exception as exc:
            logger.error("Advisory engine error: %s", exc, exc_info=True)
            _telegram_reply(chat_id, f"Advisory error: {exc}")
    else:
        # Treat any other text as content to analyze
        submission = IntelSubmission(
            source="telegram",
            content=text,
            url=url,
            title="Telegram message",
        )
        engine_b_content = _build_engine_b_submission_content(submission)
        if config.RESEARCH_SYSTEM_ACTIVE:
            engine_b_result = _queue_engine_b_intake(
                raw_content=engine_b_content,
                source_class="social_curated",
                source_ids=[url, f"telegram:{chat_id}"],
                detail=f"Telegram intel: {text[:80]}",
            )
            if not engine_b_result.get("ok"):
                _telegram_reply(
                    chat_id,
                    f"Engine B enqueue failed: {engine_b_result.get('detail') or engine_b_result.get('error', 'unknown')}",
                )
                return JSONResponse({"ok": False}, status_code=503)
            _telegram_reply(chat_id, f"Queued for Engine B research (job {str(engine_b_result['job_id'])[:8]})")
        else:
            job_id = _queue_council_analysis(
                submission,
                detail=f"Telegram intel: {text[:80]}",
            )
            engine_b_result = _queue_engine_b_intake(
                raw_content=engine_b_content,
                source_class="social_curated",
                source_ids=[url, f"telegram:{chat_id}", f"legacy:{job_id[:8]}"],
                detail=f"Telegram intel: {text[:80]}",
            )
            if not engine_b_result.get("ok"):
                logger.warning(
                    "Engine B mirror enqueue failed for Telegram freeform %s: %s",
                    job_id,
                    engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
                )
            _telegram_reply(chat_id, f"Analyzing... (job {job_id[:8]})")

    return {"ok": True}


@router.get("/api/intel/history")
def intel_history(limit: int = 20):
    """List recent intel analysis results."""
    from intelligence.event_store import EventStore
    store = EventStore()
    events = store.list_events(limit=limit, event_type="intel_analysis")
    return {"ok": True, "count": len(events), "events": events}


@router.get("/intel/bookmarklet", response_class=HTMLResponse)
def bookmarklet_install(request: Request):
    """Page to install the SA bookmarklet."""
    host = request.headers.get("host", "localhost:8000")
    scheme = request.headers.get("x-forwarded-proto", "https")
    endpoint = f"{scheme}://{host}"
    js_path = PROJECT_ROOT / "app" / "web" / "static" / "sa_bookmarklet.js"
    try:
        js_src = js_path.read_text()
    except FileNotFoundError:
        js_src = "alert('Bookmarklet file not found');"
    bookmarklet_version = _extract_bookmarklet_version(js_src)
    bookmarklet_url = html.escape(_build_bookmarklet_href(js_src, endpoint), quote=True)
    html_body = (
        "<html><head><title>BoxRoomCapital Bookmarklet</title>"
        "<style>body{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:40px}"
        "a{color:#00ff88;font-size:18px;padding:12px 24px;border:2px solid #00ff88;"
        "text-decoration:none;border-radius:6px;display:inline-block}"
        "a:hover{background:#00ff8822}code{background:#161b22;padding:2px 6px;border-radius:3px}"
        "h1{color:#00ff88}h2{color:#888;margin-top:30px}.meta{color:#8b949e;margin:8px 0 20px 0}"
        ".instructions{max-width:600px;line-height:1.6}</style></head>"
        "<body><h1>BoxRoomCapital Seeking Alpha Bookmarklet</h1>"
        "<div class='instructions'>"
        f"<p class='meta'>Bookmarklet version: <code>{html.escape(bookmarklet_version)}</code></p>"
        "<h2>Install</h2>"
        f"<p>Drag this link to your bookmarks bar:</p>"
        f'<p><a href="{bookmarklet_url}">Send to BRC</a></p>'
        "<h2>Usage</h2>"
        "<ol><li>Browse to any Seeking Alpha article or stock page</li>"
        "<li>Click the <code>Send to BRC</code> bookmark</li>"
        "<li>Article pages are sent to the LLM council for analysis</li>"
        "<li>Stock pages with quant data are stored as SA browser captures for the signal engine</li>"
        "<li>Results appear in Intel History and the research event store</li></ol>"
        f"<h2>Server</h2><p>Endpoint: <code>{endpoint}</code></p>"
        "</div></body></html>"
    )
    return HTMLResponse(
        content=html_body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/api/intel/submit", response_class=HTMLResponse)
async def intel_submit(request: Request):
    """Submit content directly from the UI for council analysis.

    Blocking work (tweet fetch, DB writes, job enqueue) runs in threadpool
    to avoid stalling the async event loop.
    """
    form = await request.form()
    content = form.get("content", "").strip()
    if not content:
        return HTMLResponse('<span class="text-[11px] text-red-400">Please enter some content.</span>')

    def _do_submit(content_text: str):
        # Detect if it's an X link
        urls = _re.findall(r'https?://\S+', content_text)
        url = urls[0] if urls else ""
        is_x = any(d in content_text for d in ["twitter.com/", "x.com/", "nitter.", "vxtwitter.com/"])

        # If X link, try to fetch the tweet
        if is_x and url:
            tweet_data = _fetch_tweet_from_url(url)
            if tweet_data:
                content_text = tweet_data["text"]
                if tweet_data.get("author"):
                    content_text = f"@{tweet_data['author']}: {content_text}"
                if tweet_data.get("created_at"):
                    content_text += f"\n\n[Posted: {tweet_data['created_at']}]"

        submission = IntelSubmission(
            source="x_twitter" if is_x else "manual",
            content=content_text,
            url=url,
            title="Manual submission" if not is_x else "Forwarded via UI",
        )

        if config.RESEARCH_SYSTEM_ACTIVE:
            engine_b_result = _queue_engine_b_intake(
                raw_content=_build_engine_b_submission_content(submission),
                source_class="social_curated",
                source_ids=[
                    submission.url or "",
                    *submission.tickers,
                    f"ui:{uuid.uuid4().hex[:8]}",
                ],
                detail=f"UI research intake: {content_text[:80]}",
            )
            if not engine_b_result.get("ok"):
                return (
                    "error",
                    f'Engine B enqueue failed: '
                    f'{engine_b_result.get("detail") or engine_b_result.get("error", "unknown")}',
                )
            return (
                "ok",
                f'Queued for Engine B research '
                f'(job {str(engine_b_result["job_id"])[:8]}). Results will appear in /research.',
            )

        job_id = _queue_council_analysis(
            submission,
            detail=f"UI intel: {content_text[:80]}",
        )
        engine_b_result = _queue_engine_b_intake(
            raw_content=_build_engine_b_submission_content(submission),
            source_class="social_curated",
            source_ids=[
                submission.url or "",
                *submission.tickers,
                f"ui:{job_id[:8]}",
            ],
            detail=f"UI research intake mirror: {content_text[:80]}",
        )
        if not engine_b_result.get("ok"):
            logger.warning(
                "Engine B mirror enqueue failed for UI intel job %s: %s",
                job_id,
                engine_b_result.get("detail") or engine_b_result.get("error", "unknown"),
            )

        return (
            "ok",
            f'Queued for analysis (job {job_id[:8]}). Results will appear in the feed.',
        )

    status, msg = await asyncio.to_thread(_do_submit, content)
    if status == "error":
        return HTMLResponse(f'<span class="text-[11px] text-red-400">{html.escape(msg)}</span>')
    return HTMLResponse(f'<span class="text-[11px] text-emerald-400">{html.escape(msg)}</span>')


@router.post("/api/intel/challenge", response_class=HTMLResponse)
async def intel_challenge(request: Request):
    """User challenges/questions a council analysis -- re-runs through LLM council with context.

    Event store reads and council job enqueue run in threadpool
    to avoid blocking the async event loop.
    """
    form = await request.form()
    analysis_id = form.get("analysis_id", "")
    challenge_text = form.get("challenge_text", "").strip()
    if not challenge_text:
        return HTMLResponse('<span class="text-[11px] text-red-400">Please enter a challenge or question.</span>')

    def _do_challenge():
        from intelligence.event_store import EventStore
        es = EventStore()
        events = es.list_events(limit=50, event_type="intel_analysis")

        original = None
        for ev in events:
            payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    continue
            if payload.get("analysis_id") == analysis_id:
                original = payload
                break

        if not original:
            return None, None

        challenge_content = (
            f"ORIGINAL ANALYSIS:\n"
            f"Source: {original.get('source', '')}\n"
            f"Title: {original.get('title', '')}\n"
            f"Summary: {original.get('summary', '')}\n"
            f"Trade ideas: {json.dumps(original.get('trade_ideas', []))}\n"
            f"Risk factors: {json.dumps(original.get('risk_factors', []))}\n\n"
            f"USER CHALLENGE:\n{challenge_text}\n\n"
            f"Please respond to the user's challenge. Re-evaluate the original analysis considering their point. "
            f"If they raise valid concerns, adjust your assessment. Be specific about what changes and what doesn't."
        )

        submission = IntelSubmission(
            source="challenge",
            content=challenge_content,
            url=original.get("url", ""),
            title=f"Challenge: {original.get('title', '')[:60]}",
        )

        job_id = _queue_council_analysis(
            submission,
            detail=f"Challenge: {challenge_text[:80]}",
        )
        return job_id, None

    job_id, _ = await asyncio.to_thread(_do_challenge)

    if job_id is None:
        return HTMLResponse('<span class="text-[11px] text-red-400">Original analysis not found.</span>')

    return HTMLResponse(
        f'<span class="text-[11px] text-emerald-400">Challenge sent to council (job {job_id[:8]}). '
        f'Refresh in ~30s to see the response above.</span>'
    )
