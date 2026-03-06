"""Seeking Alpha fallback adapters.

Server-side SA scraping is routinely blocked by PerimeterX. This module keeps
three paths behind the same SA-quant interface:

1. `SABrowserCaptureAdapter` for authenticated browser/bookmarklet captures
2. `YFinnhubAdapter` for broad automated fallback coverage
3. shared payload normalization helpers for browser-captured SA pages
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from data.trade_db import DB_PATH, get_conn
from intelligence.sa_quant_client import SAQuantSnapshot, score_sa_quant_snapshot

logger = logging.getLogger(__name__)

SA_BROWSER_CAPTURE_EVENT_TYPE = "sa_browser_capture"
SA_BROWSER_CAPTURE_SOURCE = "sa-bookmarklet"
SA_NETWORK_CAPTURE_SOURCE = "sa-network-extension"
SA_SYMBOL_CAPTURE_EVENT_TYPE = "sa_symbol_capture"
SA_CAPTURE_SOURCES = (SA_BROWSER_CAPTURE_SOURCE, SA_NETWORK_CAPTURE_SOURCE)

# Yahoo recommendation key → normalized rating
_YF_RATING_MAP: Dict[str, str] = {
    "strong_buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "underperform": "sell",
    "sell": "sell",
}

_BROWSER_RATING_MAP: Dict[str, str] = {
    "strong buy": "strong buy",
    "buy": "buy",
    "hold": "hold",
    "neutral": "hold",
    "sell": "sell",
    "strong sell": "strong sell",
    "very bullish": "very bullish",
    "bullish": "bullish",
    "bearish": "bearish",
    "very bearish": "very bearish",
}

_BROWSER_GRADE_KEYS: Dict[str, str] = {
    "value": "value_grade",
    "value_grade": "value_grade",
    "growth": "growth_grade",
    "growth_grade": "growth_grade",
    "momentum": "momentum_grade",
    "momentum_grade": "momentum_grade",
    "profitability": "profitability_grade",
    "profitability_grade": "profitability_grade",
    "revisions": "revisions_grade",
    "revisions_grade": "revisions_grade",
}

_SA_NUMERIC_TO_GRADE: Dict[int, str] = {
    1: "F",
    2: "D-",
    3: "D",
    4: "D+",
    5: "C-",
    6: "C",
    7: "C+",
    8: "B-",
    9: "B",
    10: "B+",
    11: "A-",
    12: "A",
    13: "A+",
}

_SA_HISTORY_GRADE_KEYS: Dict[str, str] = {
    "valueGrade": "value_grade",
    "growthGrade": "growth_grade",
    "momentumGrade": "momentum_grade",
    "profitabilityGrade": "profitability_grade",
    "epsRevisionsGrade": "revisions_grade",
    "revisionsGrade": "revisions_grade",
}

_SA_VALUATION_FIELDS = {
    "dividend_yield",
    "ev_12m_sales_ratio",
    "ev_ebit",
    "ev_ebit_fy1",
    "ev_ebitda",
    "ev_ebitda_fy1",
    "ev_sales_fy1",
    "pb_fy1_ratio",
    "pb_ratio",
    "pe_gaap_fy1",
    "pe_nongaap",
    "pe_nongaap_fy1",
    "pe_ratio",
    "peg_gaap",
    "peg_nongaap_fy1",
    "price_cf_ratio",
    "price_cf_ratio_fy1",
    "ps_ratio",
    "ps_ratio_fy1",
}

_SA_CAPITAL_STRUCTURE_FIELDS = {
    "impliedmarketcap",
    "marketcap",
    "other_cap_struct",
    "tev",
    "total_cash",
    "total_debt",
}


@dataclass(frozen=True)
class SABrowserCapture:
    """Structured payload captured from an authenticated browser page."""

    ticker: str
    snapshot: SAQuantSnapshot
    factor_grades: Dict[str, str]
    page_type: str = ""
    url: str = ""
    title: str = ""

    @property
    def has_quant_signal(self) -> bool:
        return bool(self.snapshot.rating or self.snapshot.quant_score_raw is not None)

    def to_payload(self) -> Dict[str, Any]:
        payload = {
            "ticker": self.ticker,
            "title": self.title,
            "url": self.url,
            "page_type": self.page_type,
            "captured_at": self.snapshot.updated_at,
            "rating": self.snapshot.rating,
            "quant_score": self.snapshot.quant_score_raw,
            "grades": dict(self.factor_grades),
            "raw_fields": dict(self.snapshot.raw_fields),
        }
        normalized_sections = self.snapshot.raw_fields.get("normalized_sections")
        if isinstance(normalized_sections, Mapping):
            payload["normalized_sections"] = dict(normalized_sections)
        return payload


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_rating(value: Any) -> str:
    clean = " ".join(str(value or "").strip().lower().split())
    if not clean:
        return ""
    return _BROWSER_RATING_MAP.get(clean, clean)


def _normalize_grade_value(value: Any) -> str:
    numeric = _coerce_float(value)
    if numeric is not None:
        whole = int(round(numeric))
        if abs(numeric - whole) < 1e-9 and whole in _SA_NUMERIC_TO_GRADE:
            return _SA_NUMERIC_TO_GRADE[whole]
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if re.fullmatch(r"[A-F][+-]?", text):
        return text
    return ""


def _normalize_factor_grades(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        canonical = _BROWSER_GRADE_KEYS.get(str(key).strip().lower())
        if not canonical:
            continue
        grade = _normalize_grade_value(value)
        if grade:
            normalized[canonical] = grade
    return normalized


def _extract_ticker(payload: Mapping[str, Any]) -> str:
    candidates = [
        payload.get("ticker"),
        payload.get("symbol"),
        payload.get("primary_ticker"),
    ]
    tickers_raw = payload.get("tickers")
    if isinstance(tickers_raw, list) and tickers_raw:
        candidates.extend(tickers_raw)
    elif isinstance(tickers_raw, str):
        candidates.extend(tickers_raw.split(","))
    for candidate in candidates:
        symbol = re.sub(r"[^A-Z.=\-]", "", str(candidate or "").upper()).strip()
        if 0 < len(symbol) <= 12:
            return symbol
    url = str(payload.get("url") or "")
    match = re.search(r"/symbol/([A-Z.=\-]+)", url, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


def _capture_timestamp(payload: Mapping[str, Any], fallback: str = "") -> str:
    raw = str(payload.get("captured_at") or payload.get("updated_at") or fallback or "").strip()
    if raw:
        return raw
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rating_from_score(value: Any) -> str:
    numeric = _coerce_float(value)
    if numeric is None:
        return ""
    if numeric >= 4.5:
        return "strong buy"
    if numeric >= 3.5:
        return "buy"
    if numeric >= 2.5:
        return "hold"
    if numeric >= 1.5:
        return "sell"
    return "strong sell"


def _extract_sa_history_entry(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    direct_entry = payload.get("sa_rating_history_entry")
    if isinstance(direct_entry, Mapping):
        return direct_entry

    history_payload = payload.get("sa_history") or payload.get("sa_rating_history") or payload
    if not isinstance(history_payload, Mapping):
        return None

    data = history_payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, Mapping):
                continue
            attrs = item.get("attributes")
            ratings = attrs.get("ratings") if isinstance(attrs, Mapping) else None
            if isinstance(ratings, Mapping):
                return item
    return None


def _extract_history_grades(ratings: Mapping[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, canonical in _SA_HISTORY_GRADE_KEYS.items():
        if key in ratings:
            normalized[canonical] = ratings.get(key)
    return normalized


def _extract_query_fields(url: str) -> List[str]:
    if not url:
        return []
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    fields: List[str] = []
    for raw in query.get("filter[fields]", []):
        for item in str(raw).split(","):
            clean = str(item or "").strip()
            if clean:
                fields.append(clean)
    for raw in query.get("filter[fields][]", []):
        clean = str(raw or "").strip()
        if clean:
            fields.append(clean)
    return fields


def _classify_symbol_response_url(url: str) -> str:
    clean = str(url or "").strip().lower()
    if not clean:
        return "symbol"
    if "/rating/periods" in clean:
        return "ratings_history"
    if "/relative_rankings" in clean:
        return "relative_rankings"
    if "/sector_metrics" in clean:
        return "sector_metrics"
    if "/ticker_metric_grades" in clean:
        return "metric_grades"
    if "/symbol_data/estimates" in clean:
        return "earnings_estimates"
    if "/historical_prices" in clean:
        return "price_history"
    if "/shares" in clean and "/symbols/" in clean:
        return "ownership"
    if "/metrics" in clean:
        fields = _extract_query_fields(clean)
        if fields == ["primary_price"]:
            return "price"
        if fields and all(field.endswith("_avg_5y") for field in fields):
            return "valuation_averages_5y"
        if any(field in _SA_CAPITAL_STRUCTURE_FIELDS for field in fields):
            return "capital_structure"
        if any(field in _SA_VALUATION_FIELDS for field in fields):
            return "valuation_metrics"
    return "symbol"


def _metric_type_field_map(payload: Mapping[str, Any]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    included = payload.get("included")
    if not isinstance(included, list):
        return mapping
    for item in included:
        if not isinstance(item, Mapping) or item.get("type") != "metric_type":
            continue
        item_id = str(item.get("id") or "").strip()
        attrs = item.get("attributes")
        field = str(attrs.get("field") or "").strip() if isinstance(attrs, Mapping) else ""
        if item_id and field:
            mapping[item_id] = field
    return mapping


def _metric_values_from_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list):
        merged: Dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            for key, value in item.items():
                if key in {"slug", "tickerId"} or value is None:
                    continue
                merged[str(key)] = value
        return merged

    if not isinstance(payload, Mapping):
        return {}

    data = payload.get("data")
    if not isinstance(data, list):
        return {}
    field_map = _metric_type_field_map(payload)
    values: Dict[str, Any] = {}
    for item in data:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attributes")
        rel = item.get("relationships")
        metric_data = rel.get("metric_type", {}).get("data") if isinstance(rel, Mapping) else None
        metric_id = str(metric_data.get("id") or "").strip() if isinstance(metric_data, Mapping) else ""
        field = field_map.get(metric_id, "")
        if not field or not isinstance(attrs, Mapping):
            continue
        value = attrs.get("value")
        if value is not None:
            values[field] = value
    return values


def _metric_grades_from_payload(payload: Mapping[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    if not isinstance(data, list):
        return {}
    field_map = _metric_type_field_map(payload)
    normalized: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in data:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attributes")
        rel = item.get("relationships")
        metric_data = rel.get("metric_type", {}).get("data") if isinstance(rel, Mapping) else None
        metric_id = str(metric_data.get("id") or "").strip() if isinstance(metric_data, Mapping) else ""
        field = field_map.get(metric_id, "")
        if not field or not isinstance(attrs, Mapping):
            continue
        algo = str(attrs.get("algo") or "default").strip() or "default"
        grade_numeric = attrs.get("grade")
        bucket = normalized.setdefault(algo, {})
        bucket[field] = {
            "grade_numeric": grade_numeric,
            "grade": _normalize_grade_value(grade_numeric),
        }
    return normalized


def _relative_rankings_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    attrs = data.get("attributes") if isinstance(data, Mapping) else None
    if not isinstance(attrs, Mapping):
        return {}
    return {
        "overall_rank": attrs.get("overallRank"),
        "sector_rank": attrs.get("sectorRank"),
        "industry_rank": attrs.get("industryRank"),
        "sector_name": attrs.get("sectorName"),
        "industry_name": attrs.get("primaryName"),
        "sector_total": attrs.get("totalTickersInSector"),
        "industry_total": attrs.get("totalTickersInPrimaryIndustry"),
        "overall_total": attrs.get("totalTickers"),
    }


def _compact_estimate_series(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}

    def _first_value(entry: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, Mapping):
            return None
        compact: Dict[str, Any] = {}
        for period, series in entry.items():
            if not isinstance(series, list) or not series:
                continue
            sample = series[0] if isinstance(series[0], Mapping) else None
            if not isinstance(sample, Mapping):
                continue
            compact[str(period)] = {
                "value": _coerce_float(sample.get("dataitemvalue")),
                "effective_date": sample.get("effectivedate"),
                "period": sample.get("period"),
            }
        return compact or None

    estimates = payload.get("estimates")
    if not isinstance(estimates, Mapping):
        return {}

    compact_estimates: Dict[str, Any] = {}
    for ticker_payload in estimates.values():
        if not isinstance(ticker_payload, Mapping):
            continue
        for metric_name, metric_payload in ticker_payload.items():
            compact = _first_value(metric_payload)
            if compact:
                compact_estimates[str(metric_name)] = compact
        if compact_estimates:
            break
    revisions = payload.get("revisions")
    return {
        "estimates": compact_estimates,
        "revisions": revisions if isinstance(revisions, Mapping) else {},
    }


def _price_history_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return {}
    first = data[0]
    attrs = first.get("attributes") if isinstance(first, Mapping) else None
    if not isinstance(attrs, Mapping):
        return {}
    return {
        "as_of_date": attrs.get("as_of_date"),
        "open": attrs.get("open"),
        "high": attrs.get("high"),
        "low": attrs.get("low"),
        "close": attrs.get("close"),
        "volume": attrs.get("volume"),
    }


def _ownership_from_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    if not isinstance(data, list):
        return {}
    top_holders: List[Dict[str, Any]] = []
    for item in data[:10]:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, Mapping):
            continue
        top_holders.append(
            {
                "owner_type_id": attrs.get("ownertypeid"),
                "percentage": attrs.get("percentage"),
                "shares_held": attrs.get("sharesheld"),
            }
        )
    return {"top_holders": top_holders}


def _merge_nested_dict(target: Dict[str, Any], source: Mapping[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge_nested_dict(target[key], value)
        else:
            target[key] = value
    return target


def normalize_sa_symbol_snapshot(payload: Mapping[str, Any]) -> Dict[str, Any]:
    summary = dict(payload.get("summary") or {}) if isinstance(payload.get("summary"), Mapping) else {}
    raw_responses = payload.get("raw_responses") if isinstance(payload.get("raw_responses"), list) else []
    normalized_sections: Dict[str, Any] = {}

    summary.setdefault("ticker", payload.get("ticker"))
    summary.setdefault("url", payload.get("url"))
    summary.setdefault("title", payload.get("title"))
    summary.setdefault("page_type", payload.get("page_type") or "symbol")
    summary.setdefault("captured_at", payload.get("captured_at"))
    summary.setdefault("source", payload.get("source"))
    summary.setdefault("source_ref", payload.get("source_ref"))
    summary.setdefault("bookmarklet_version", payload.get("bookmarklet_version"))

    for record in raw_responses:
        if not isinstance(record, Mapping):
            continue
        response_url = str(record.get("response_url") or "").strip()
        section = str(record.get("section") or "").strip() or _classify_symbol_response_url(response_url)
        canonical_section = _classify_symbol_response_url(response_url)
        if canonical_section != "symbol":
            section = canonical_section
        record_payload = record.get("payload")

        if section == "ratings_history":
            history_entry = _extract_sa_history_entry(record_payload if isinstance(record_payload, Mapping) else {})
            history_attrs = history_entry.get("attributes") if isinstance(history_entry, Mapping) else None
            ratings = history_attrs.get("ratings") if isinstance(history_attrs, Mapping) else None
            if isinstance(record_payload, Mapping):
                summary.setdefault("sa_history", record_payload)
            if isinstance(ratings, Mapping):
                summary.setdefault("quant_score", _coerce_float(ratings.get("quantRating")))
                summary.setdefault("rating", _rating_from_score(ratings.get("quantRating")))
                summary.setdefault("author_rating", _rating_from_score(ratings.get("authorsRating")))
                summary.setdefault("wall_st_rating", _rating_from_score(ratings.get("sellSideRating")))
                merged_grades = dict(summary.get("grades") or {})
                merged_grades.update(_normalize_factor_grades(_extract_history_grades(ratings)))
                summary["grades"] = merged_grades
                normalized_sections[section] = {
                    "as_date": history_attrs.get("asDate") if isinstance(history_attrs, Mapping) else "",
                    "ticker_id": history_attrs.get("tickerId") if isinstance(history_attrs, Mapping) else None,
                    "quant_score": _coerce_float(ratings.get("quantRating")),
                    "rating": _rating_from_score(ratings.get("quantRating")),
                    "author_rating": _rating_from_score(ratings.get("authorsRating")),
                    "wall_st_rating": _rating_from_score(ratings.get("sellSideRating")),
                    "grades": _normalize_factor_grades(_extract_history_grades(ratings)),
                }
            continue

        if not isinstance(record_payload, Mapping) and not isinstance(record_payload, list):
            continue

        section_payload: Dict[str, Any] = {}
        if section == "relative_rankings" and isinstance(record_payload, Mapping):
            section_payload = _relative_rankings_from_payload(record_payload)
            if section_payload:
                summary.setdefault("sector_rank", section_payload.get("sector_rank"))
                summary.setdefault("industry_rank", section_payload.get("industry_rank"))
        elif section == "price":
            section_payload = _metric_values_from_payload(record_payload)
        elif section in {"valuation_metrics", "valuation_averages_5y", "capital_structure", "sector_metrics"} and isinstance(record_payload, Mapping):
            section_payload = _metric_values_from_payload(record_payload)
        elif section == "metric_grades" and isinstance(record_payload, Mapping):
            section_payload = _metric_grades_from_payload(record_payload)
        elif section == "earnings_estimates" and isinstance(record_payload, Mapping):
            section_payload = _compact_estimate_series(record_payload)
        elif section == "price_history" and isinstance(record_payload, Mapping):
            section_payload = _price_history_from_payload(record_payload)
        elif section == "ownership" and isinstance(record_payload, Mapping):
            section_payload = _ownership_from_payload(record_payload)

        if not section_payload:
            continue
        if isinstance(normalized_sections.get(section), dict) and isinstance(section_payload, Mapping):
            _merge_nested_dict(normalized_sections[section], section_payload)
        else:
            normalized_sections[section] = section_payload

    raw_fields = dict(summary.get("raw_fields") or {}) if isinstance(summary.get("raw_fields"), Mapping) else {}
    merged_section_names = set(raw_fields.get("section_names") or [])
    merged_section_names.update(str(key) for key in normalized_sections.keys())
    raw_fields["section_names"] = sorted(merged_section_names)
    raw_fields["normalized_section_names"] = sorted(normalized_sections.keys())
    raw_fields["raw_response_count"] = len(raw_responses)

    rankings = normalized_sections.get("relative_rankings")
    if isinstance(rankings, Mapping):
        raw_fields.setdefault("overall_rank", rankings.get("overall_rank"))
        raw_fields.setdefault("sector_name", rankings.get("sector_name"))
        raw_fields.setdefault("industry_name", rankings.get("industry_name"))

    price = normalized_sections.get("price")
    if isinstance(price, Mapping) and price.get("primary_price") is not None:
        raw_fields.setdefault("primary_price", price.get("primary_price"))

    ratings_history = normalized_sections.get("ratings_history")
    if isinstance(ratings_history, Mapping):
        raw_fields.setdefault("ticker_id", ratings_history.get("ticker_id"))
        raw_fields.setdefault("as_date", ratings_history.get("as_date"))

    summary["raw_fields"] = raw_fields
    return {
        "summary": summary,
        "normalized_sections": normalized_sections,
    }


def parse_sa_browser_payload(
    payload: Mapping[str, Any],
    captured_at: str = "",
) -> SABrowserCapture:
    """Normalize bookmarklet/browser payload into an SA snapshot."""
    symbol = _extract_ticker(payload)
    if not symbol:
        raise ValueError("ticker is required")

    history_entry = _extract_sa_history_entry(payload)
    history_attrs = history_entry.get("attributes") if isinstance(history_entry, Mapping) else None
    history_ratings = history_attrs.get("ratings") if isinstance(history_attrs, Mapping) else None

    grades_input = payload.get("grades") or payload.get("factor_grades") or {}
    if not grades_input and isinstance(history_ratings, Mapping):
        grades_input = _extract_history_grades(history_ratings)
    grades = _normalize_factor_grades(grades_input)

    rating = _normalize_rating(
        payload.get("quant_rating")
        or payload.get("quantRating")
        or payload.get("rating")
        or payload.get("sa_rating")
        or (history_ratings.get("quantRating") if isinstance(history_ratings, Mapping) and isinstance(history_ratings.get("quantRating"), str) else "")
    )
    if not rating and isinstance(history_ratings, Mapping):
        rating = _rating_from_score(history_ratings.get("quantRating"))

    quant_score = _coerce_float(
        payload.get("quant_score")
        or payload.get("quantScore")
        or payload.get("quant_score_raw")
        or payload.get("sa_quant_score")
        or (history_ratings.get("quantRating") if isinstance(history_ratings, Mapping) else None)
    )
    if not rating and quant_score is None and not grades:
        raise ValueError("payload did not include SA quant fields")

    raw_fields = dict(payload.get("raw_fields") or {}) if isinstance(payload.get("raw_fields"), Mapping) else {}
    capture_source = str(
        payload.get("source")
        or raw_fields.get("source")
        or SA_BROWSER_CAPTURE_SOURCE
    ).strip() or SA_BROWSER_CAPTURE_SOURCE
    author_rating = _normalize_rating(
        payload.get("author_rating")
        or payload.get("sa_authors_rating")
        or (history_ratings.get("authorsRating") if isinstance(history_ratings, Mapping) and isinstance(history_ratings.get("authorsRating"), str) else "")
    )
    if not author_rating and isinstance(history_ratings, Mapping):
        author_rating = _rating_from_score(history_ratings.get("authorsRating"))

    wall_st_rating = _normalize_rating(
        payload.get("wall_st_rating")
        or payload.get("analyst_rating")
        or (history_ratings.get("sellSideRating") if isinstance(history_ratings, Mapping) and isinstance(history_ratings.get("sellSideRating"), str) else "")
    )
    if not wall_st_rating and isinstance(history_ratings, Mapping):
        wall_st_rating = _rating_from_score(history_ratings.get("sellSideRating"))

    history_as_date = (
        str(history_attrs.get("asDate") or "").strip()
        if isinstance(history_attrs, Mapping)
        else ""
    )
    raw_fields.update(
        {
            "rating": rating,
            "quant_score_raw": quant_score,
            "sa_authors_rating": author_rating,
            "wall_st_rating": wall_st_rating,
            "bookmarklet_version": str(payload.get("bookmarklet_version") or ""),
            "factor_grades": dict(grades),
            "tickers": list(payload.get("tickers") or []) if isinstance(payload.get("tickers"), list) else [],
            "scan_debug": payload.get("scan_debug"),
            "title": str(payload.get("title") or ""),
            "url": str(payload.get("url") or ""),
            "page_type": str(payload.get("page_type") or payload.get("pageType") or ""),
            "source": capture_source,
            "sa_rating_history_entry": dict(history_entry) if isinstance(history_entry, Mapping) else None,
            "as_date": history_as_date,
        }
    )
    normalized_sections = payload.get("normalized_sections")
    if isinstance(normalized_sections, Mapping):
        raw_fields["normalized_sections"] = dict(normalized_sections)
    normalized_section_names = payload.get("normalized_section_names")
    if isinstance(normalized_section_names, list):
        raw_fields["normalized_section_names"] = list(normalized_section_names)

    updated_at = _capture_timestamp(payload, fallback=history_as_date or captured_at)
    source_ref = str(payload.get("url") or payload.get("source_ref") or f"sa-browser-{symbol}").strip()
    snapshot = SAQuantSnapshot(
        ticker=symbol,
        rating=rating,
        quant_score_raw=quant_score,
        sector_rank=_coerce_float(payload.get("sector_rank")),
        industry_rank=_coerce_float(payload.get("industry_rank")),
        updated_at=updated_at,
        source_ref=source_ref,
        raw_fields=raw_fields,
    )
    return SABrowserCapture(
        ticker=symbol,
        snapshot=snapshot,
        factor_grades=grades,
        page_type=str(payload.get("page_type") or payload.get("pageType") or ""),
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
    )


class SABrowserCaptureAdapter:
    """Prefer recent browser-captured SA snapshots, then fall back to YF/Finnhub."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        max_age_seconds: int = 86400,
        fallback: Optional["YFinnhubAdapter"] = None,
    ):
        self.db_path = db_path
        self.max_age_seconds = max(300, int(max_age_seconds))
        self.fallback = fallback or YFinnhubAdapter()

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        capture = self._load_recent_capture(ticker)
        if capture and capture.has_quant_signal:
            return capture.snapshot
        return self.fallback.fetch_snapshot(ticker)

    def fetch_layer_score(self, ticker: str, as_of: str):
        capture = self._load_recent_capture(ticker)
        if capture and capture.has_quant_signal:
            return score_sa_quant_snapshot(
                snapshot=capture.snapshot,
                as_of=as_of,
                source="sa-browser-capture",
            )
        return self.fallback.fetch_layer_score(ticker, as_of)

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        capture = self._load_recent_capture(ticker)
        if capture and capture.factor_grades:
            return capture.factor_grades
        return self.fallback.fetch_factor_grades(ticker)

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        return self.fallback.fetch_news(ticker, count=count)

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        return self.fallback.fetch_analyst_recs(ticker)

    def close(self):
        self.fallback.close()

    def _load_recent_capture(self, ticker: str) -> Optional[SABrowserCapture]:
        symbol = ticker.strip().upper()
        if not symbol:
            return None

        conn = get_conn(self.db_path)
        rows = conn.execute(
            """SELECT payload, retrieved_at, source
               FROM research_events
               WHERE event_type=? AND symbol=?
               ORDER BY retrieved_at DESC, created_at DESC
               LIMIT 10""",
            (SA_BROWSER_CAPTURE_EVENT_TYPE, symbol),
        ).fetchall()
        conn.close()

        for row in rows:
            if str(row["source"] or "").strip() not in SA_CAPTURE_SOURCES:
                continue
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            try:
                capture = parse_sa_browser_payload(payload, captured_at=str(row["retrieved_at"] or ""))
            except ValueError:
                continue
            if self._is_fresh(capture.snapshot.updated_at):
                return capture
        return None

    def _is_fresh(self, timestamp: str) -> bool:
        raw = str(timestamp or "").strip()
        if not raw:
            return False
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= datetime.now(timezone.utc) - timedelta(seconds=self.max_age_seconds)


class YFinnhubAdapter:
    """Drop-in replacement for SAQuantClient using Yahoo Finance + Finnhub."""

    def __init__(self):
        self._finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()

    # ── Core interface (matches SAQuantClient) ────────────────────────

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        """Fetch Yahoo Finance info and return SAQuantSnapshot."""
        import yfinance as yf

        symbol = ticker.strip().upper()
        t = yf.Ticker(symbol)
        info = t.info or {}

        rating = _YF_RATING_MAP.get(
            info.get("recommendationKey", ""), ""
        )
        # Yahoo provides a recommendationMean on 1-5 scale (1=strong buy, 5=sell)
        raw_score = info.get("recommendationMean")
        quant_score = None
        if raw_score is not None:
            try:
                # Convert 1-5 to 0-100 (invert: 1=100, 5=0)
                quant_score = max(0.0, min(100.0, (5.0 - float(raw_score)) * 25.0))
            except (ValueError, TypeError):
                pass

        return SAQuantSnapshot(
            ticker=symbol,
            rating=rating,
            quant_score_raw=quant_score,
            sector_rank=None,
            industry_rank=None,
            updated_at=datetime.now(timezone.utc).isoformat(),
            source_ref=f"yf-finnhub-{symbol}",
            raw_fields={
                "rating": rating,
                "quant_score_raw": quant_score,
                "recommendation_key": info.get("recommendationKey", ""),
                "recommendation_mean": raw_score,
                "target_mean_price": info.get("targetMeanPrice"),
                "target_high_price": info.get("targetHighPrice"),
                "target_low_price": info.get("targetLowPrice"),
                "num_analysts": info.get("numberOfAnalystOpinions"),
                "source": "yahoo_finance+finnhub",
            },
        )

    def fetch_layer_score(self, ticker: str, as_of: str):
        """Fetch data and return L8 LayerScore."""
        snapshot = self.fetch_snapshot(ticker)

        # Enhance with Finnhub consensus if available
        if self._finnhub_key:
            fh = self._fetch_finnhub_consensus(ticker)
            if fh and not snapshot.rating:
                snapshot = SAQuantSnapshot(
                    ticker=snapshot.ticker,
                    rating=fh.get("rating", snapshot.rating),
                    quant_score_raw=fh.get("score", snapshot.quant_score_raw),
                    sector_rank=snapshot.sector_rank,
                    industry_rank=snapshot.industry_rank,
                    updated_at=snapshot.updated_at,
                    source_ref=snapshot.source_ref,
                    raw_fields={**snapshot.raw_fields, "finnhub": fh},
                )

        return score_sa_quant_snapshot(
            snapshot=snapshot, as_of=as_of, source="yf-finnhub",
        )

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Yahoo doesn't have SA-style factor grades — return empty."""
        return {}

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        """Fetch news via yfinance."""
        import yfinance as yf

        symbol = ticker.strip().upper()
        articles: List[Dict[str, Any]] = []
        try:
            t = yf.Ticker(symbol)
            news = t.news or []
            for item in news[:count]:
                content = item.get("content", item)
                if isinstance(content, dict):
                    title = content.get("title", "")
                    pub_date = content.get("pubDate", content.get("providerPublishTime", ""))
                    url = content.get("canonicalUrl", {})
                    link = url.get("url", "") if isinstance(url, dict) else str(url)
                else:
                    title = item.get("title", "")
                    pub_date = str(item.get("providerPublishTime", ""))
                    link = item.get("link", "")
                if title:
                    articles.append({
                        "headline": str(title),
                        "published_at": str(pub_date),
                        "source": "yahoo_finance",
                        "url": str(link),
                    })
        except Exception as exc:
            logger.warning("YF news fetch failed for %s: %s", ticker, exc)
        return articles

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch analyst recommendations from Finnhub."""
        if not self._finnhub_key:
            return self._yf_analyst_recs(ticker)

        import requests
        symbol = ticker.strip().upper()
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": symbol, "token": self._finnhub_key},
                timeout=10,
            )
            if r.status_code != 200:
                return self._yf_analyst_recs(ticker)

            data = r.json()
            recs: List[Dict[str, Any]] = []
            for period in data[:6]:
                total = sum(period.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                if total == 0:
                    continue
                # Derive consensus rating from majority
                best_key = max(("strongBuy", "buy", "hold", "sell", "strongSell"),
                               key=lambda k: period.get(k, 0))
                rating_map = {"strongBuy": "Strong Buy", "buy": "Buy",
                              "hold": "Hold", "sell": "Sell", "strongSell": "Strong Sell"}
                recs.append({
                    "analyst": "consensus",
                    "rating": rating_map.get(best_key, ""),
                    "target_price": None,
                    "date": period.get("period", ""),
                    "breakdown": {k: period.get(k, 0) for k in
                                  ("strongBuy", "buy", "hold", "sell", "strongSell")},
                })
            return recs
        except Exception as exc:
            logger.warning("Finnhub recs failed for %s: %s", ticker, exc)
            return self._yf_analyst_recs(ticker)

    # ── Helpers ───────────────────────────────────────────────────────

    def _fetch_finnhub_consensus(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch latest Finnhub consensus and convert to rating + score."""
        import requests

        symbol = ticker.strip().upper()
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": symbol, "token": self._finnhub_key},
                timeout=10,
            )
            if r.status_code != 200:
                return None

            data = r.json()
            if not data:
                return None

            latest = data[0]
            sb = latest.get("strongBuy", 0)
            b = latest.get("buy", 0)
            h = latest.get("hold", 0)
            s = latest.get("sell", 0)
            ss = latest.get("strongSell", 0)
            total = sb + b + h + s + ss
            if total == 0:
                return None

            # Weighted score: strongBuy=100, buy=75, hold=50, sell=25, strongSell=0
            weighted = (sb * 100 + b * 75 + h * 50 + s * 25 + ss * 0) / total

            if weighted >= 80:
                rating = "strong buy"
            elif weighted >= 65:
                rating = "buy"
            elif weighted >= 45:
                rating = "hold"
            elif weighted >= 30:
                rating = "sell"
            else:
                rating = "strong sell"

            return {
                "rating": rating,
                "score": round(weighted, 2),
                "period": latest.get("period", ""),
                "breakdown": {"strongBuy": sb, "buy": b, "hold": h, "sell": s, "strongSell": ss},
            }
        except Exception as exc:
            logger.warning("Finnhub consensus failed for %s: %s", ticker, exc)
            return None

    @staticmethod
    def _yf_analyst_recs(ticker: str) -> List[Dict[str, Any]]:
        """Fallback: get recommendations from yfinance."""
        import yfinance as yf

        recs: List[Dict[str, Any]] = []
        try:
            t = yf.Ticker(ticker.strip().upper())
            df = t.recommendations
            if df is not None and len(df) > 0:
                for _, row in df.tail(3).iterrows():
                    total = sum(row.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
                    if total == 0:
                        continue
                    best_key = max(("strongBuy", "buy", "hold", "sell", "strongSell"),
                                   key=lambda k: row.get(k, 0))
                    rating_map = {"strongBuy": "Strong Buy", "buy": "Buy",
                                  "hold": "Hold", "sell": "Sell", "strongSell": "Strong Sell"}
                    recs.append({
                        "analyst": "consensus",
                        "rating": rating_map.get(best_key, ""),
                        "target_price": None,
                        "date": str(row.get("period", row.name if hasattr(row, "name") else "")),
                    })
        except Exception:
            pass
        return recs

    def close(self):
        pass
