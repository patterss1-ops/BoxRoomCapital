"""
Fund and sleeve performance report generation.

B-003: Generates structured daily/weekly/monthly performance reports from
persisted fund_daily_report and sleeve_daily_report data. Reports are
deterministic and can be regenerated from stored data at any time.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from data.trade_db import (
    DB_PATH,
    get_fund_daily_reports,
    get_sleeve_daily_reports,
    get_risk_daily_snapshots,
)

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSummary:
    """Summarised performance metrics over a period."""

    period_label: str
    start_date: str
    end_date: str
    start_nav: float
    end_nav: float
    return_pct: float
    max_drawdown_pct: float
    high_water_mark: float
    trading_days: int
    positive_days: int
    negative_days: int
    best_day_pct: Optional[float] = None
    worst_day_pct: Optional[float] = None
    avg_daily_return_pct: Optional[float] = None
    volatility_ann_pct: Optional[float] = None


@dataclass
class SleevePerformance:
    """Performance summary for a single sleeve."""

    sleeve: str
    start_nav: float
    end_nav: float
    return_pct: float
    weight_pct: float
    contribution_pct: float


@dataclass
class FundReport:
    """Complete fund report combining performance, sleeves, and risk."""

    report_date: str
    period_label: str
    performance: PerformanceSummary
    sleeves: list[SleevePerformance]
    risk_snapshot: Optional[dict] = None


def generate_daily_report(
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> Optional[FundReport]:
    """
    Generate a daily fund report.

    Returns None if no data is available for the date.
    """
    target_date = report_date or date.today().isoformat()

    fund_reports = get_fund_daily_reports(days=2, db_path=db_path)
    if not fund_reports:
        return None

    # Find today's report
    today_report = None
    prev_report = None
    for r in fund_reports:
        if r["report_date"] == target_date:
            today_report = r
        elif r["report_date"] < target_date and prev_report is None:
            prev_report = r

    if today_report is None:
        return None

    start_nav = prev_report["total_nav"] if prev_report else today_report["total_nav"]
    end_nav = today_report["total_nav"]
    ret_pct = today_report.get("daily_return_pct") or 0.0

    perf = PerformanceSummary(
        period_label="daily",
        start_date=prev_report["report_date"] if prev_report else target_date,
        end_date=target_date,
        start_nav=start_nav,
        end_nav=end_nav,
        return_pct=ret_pct,
        max_drawdown_pct=today_report.get("drawdown_pct", 0.0),
        high_water_mark=today_report.get("high_water_mark", 0.0),
        trading_days=1,
        positive_days=1 if ret_pct > 0 else 0,
        negative_days=1 if ret_pct < 0 else 0,
        best_day_pct=ret_pct,
        worst_day_pct=ret_pct,
        avg_daily_return_pct=ret_pct,
    )

    sleeves = _build_sleeve_performance(target_date, end_nav, db_path)

    risk = _get_latest_risk_snapshot(target_date, db_path)

    return FundReport(
        report_date=target_date,
        period_label="daily",
        performance=perf,
        sleeves=sleeves,
        risk_snapshot=risk,
    )


def generate_period_report(
    days: int = 30,
    label: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> Optional[FundReport]:
    """
    Generate a performance report for a rolling period (e.g. 7d, 30d, 90d).

    Returns None if insufficient data is available.
    """
    target_end = end_date or date.today().isoformat()
    period_label = label or f"{days}d"

    fund_reports = get_fund_daily_reports(days=days + 5, db_path=db_path)
    if not fund_reports:
        return None

    # Sort chronologically
    sorted_reports = sorted(fund_reports, key=lambda r: r["report_date"])

    # Filter to period
    cutoff = (
        datetime.fromisoformat(target_end) - timedelta(days=days)
    ).date().isoformat()

    period_reports = [
        r for r in sorted_reports
        if cutoff <= r["report_date"] <= target_end
    ]

    if len(period_reports) < 2:
        return None

    first = period_reports[0]
    last = period_reports[-1]

    start_nav = first["total_nav"]
    end_nav = last["total_nav"]
    total_return = ((end_nav - start_nav) / start_nav * 100.0) if start_nav > 0 else 0.0

    # Daily return stats
    daily_returns = [
        r["daily_return_pct"]
        for r in period_reports
        if r.get("daily_return_pct") is not None
    ]

    positive_days = sum(1 for d in daily_returns if d > 0)
    negative_days = sum(1 for d in daily_returns if d < 0)
    best = max(daily_returns) if daily_returns else None
    worst = min(daily_returns) if daily_returns else None
    avg = (sum(daily_returns) / len(daily_returns)) if daily_returns else None

    # Annualised volatility
    vol = _calc_annualised_volatility(daily_returns)

    # Max drawdown in period
    max_dd = _calc_max_drawdown(period_reports)

    # High water mark
    hwm = max(r.get("high_water_mark", r["total_nav"]) for r in period_reports)

    perf = PerformanceSummary(
        period_label=period_label,
        start_date=first["report_date"],
        end_date=last["report_date"],
        start_nav=start_nav,
        end_nav=end_nav,
        return_pct=total_return,
        max_drawdown_pct=max_dd,
        high_water_mark=hwm,
        trading_days=len(period_reports),
        positive_days=positive_days,
        negative_days=negative_days,
        best_day_pct=best,
        worst_day_pct=worst,
        avg_daily_return_pct=avg,
        volatility_ann_pct=vol,
    )

    sleeves = _build_sleeve_performance(last["report_date"], end_nav, db_path)
    risk = _get_latest_risk_snapshot(last["report_date"], db_path)

    return FundReport(
        report_date=last["report_date"],
        period_label=period_label,
        performance=perf,
        sleeves=sleeves,
        risk_snapshot=risk,
    )


def format_report_text(report: FundReport) -> str:
    """
    Format a FundReport into human-readable text for Telegram / console.

    Deterministic output — same report data always produces same text.
    """
    p = report.performance
    lines = [
        f"📊 Fund Report — {report.period_label.upper()} ({report.report_date})",
        f"{'─' * 50}",
        f"NAV: £{p.end_nav:,.2f}  (from £{p.start_nav:,.2f})",
        f"Return: {p.return_pct:+.2f}%",
        f"Max Drawdown: {p.max_drawdown_pct:.2f}%",
        f"HWM: £{p.high_water_mark:,.2f}",
    ]

    if p.trading_days > 1:
        lines.append(f"Trading Days: {p.trading_days} (↑{p.positive_days} ↓{p.negative_days})")
        if p.best_day_pct is not None:
            lines.append(f"Best Day: {p.best_day_pct:+.2f}%  Worst Day: {p.worst_day_pct:+.2f}%")
        if p.avg_daily_return_pct is not None:
            lines.append(f"Avg Daily: {p.avg_daily_return_pct:+.4f}%")
        if p.volatility_ann_pct is not None:
            lines.append(f"Vol (ann): {p.volatility_ann_pct:.2f}%")

    if report.sleeves:
        lines.append(f"\n{'─' * 50}")
        lines.append("Sleeves:")
        for s in sorted(report.sleeves, key=lambda x: -x.weight_pct):
            lines.append(
                f"  {s.sleeve}: £{s.end_nav:,.2f} "
                f"({s.weight_pct:.1f}%, {s.return_pct:+.2f}%, "
                f"contrib {s.contribution_pct:+.2f}%)"
            )

    if report.risk_snapshot:
        r = report.risk_snapshot
        lines.append(f"\n{'─' * 50}")
        lines.append("Risk:")
        lines.append(f"  Heat: {r.get('total_heat_pct', 0):.1f}%")
        lines.append(f"  Positions: {r.get('open_position_count', 0)}")
        lines.append(f"  Max Position: {r.get('max_position_pct', 0):.1f}%")
        lines.append(f"  Leverage: {r.get('leverage_ratio', 0):.2f}x")
        if r.get("var_95_pct") is not None:
            lines.append(f"  VaR(95): {r['var_95_pct']:.2f}%")

    return "\n".join(lines)


# ─── Internal helpers ─────────────────────────────────────────────────────


def _build_sleeve_performance(
    report_date: str,
    fund_nav: float,
    db_path: str,
) -> list[SleevePerformance]:
    """Build sleeve performance summaries for a given date."""
    all_sleeve_reports = get_sleeve_daily_reports(days=2, db_path=db_path)
    if not all_sleeve_reports:
        return []

    # Group by sleeve, find today's and previous
    by_sleeve: dict[str, list[dict]] = {}
    for r in all_sleeve_reports:
        s = r["sleeve"]
        by_sleeve.setdefault(s, []).append(r)

    results = []
    for sleeve, reports in by_sleeve.items():
        today = None
        prev = None
        for r in sorted(reports, key=lambda x: x["report_date"], reverse=True):
            if r["report_date"] == report_date and today is None:
                today = r
            elif r["report_date"] < report_date and prev is None:
                prev = r

        if today is None:
            continue

        start_nav = prev["nav"] if prev else today["nav"]
        end_nav = today["nav"]
        ret_pct = today.get("daily_return_pct") or 0.0
        weight = today.get("weight_pct", 0.0)
        contribution = weight * ret_pct / 100.0 if weight > 0 else 0.0

        results.append(SleevePerformance(
            sleeve=sleeve,
            start_nav=start_nav,
            end_nav=end_nav,
            return_pct=ret_pct,
            weight_pct=weight,
            contribution_pct=contribution,
        ))

    return results


def _get_latest_risk_snapshot(
    report_date: str,
    db_path: str,
) -> Optional[dict]:
    """Get the risk snapshot for or before the report date."""
    snapshots = get_risk_daily_snapshots(days=5, db_path=db_path)
    for s in snapshots:
        if s["snapshot_date"] <= report_date:
            return s
    return None


def _calc_annualised_volatility(daily_returns: list[float]) -> Optional[float]:
    """Calculate annualised volatility from daily returns (in %)."""
    if len(daily_returns) < 2:
        return None

    n = len(daily_returns)
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    daily_vol = variance ** 0.5
    return daily_vol * (252 ** 0.5)  # Annualise


def _calc_max_drawdown(reports: list[dict]) -> float:
    """Calculate maximum drawdown percentage from chronologically sorted reports."""
    if not reports:
        return 0.0

    peak = reports[0]["total_nav"]
    max_dd = 0.0

    for r in reports:
        nav = r["total_nav"]
        if nav > peak:
            peak = nav
        if peak > 0:
            dd = (nav - peak) / peak * 100.0
            if dd < max_dd:
                max_dd = dd

    return max_dd
