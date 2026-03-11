"""Research dashboard and system-state context builders."""
from __future__ import annotations

import logging
from typing import Any, Callable


def _get_research_fragment_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    research_cache_ttl_seconds: float,
    get_calibration_runs: Callable[..., list[dict[str, Any]]],
    get_option_contract_summary: Callable[[], Any],
    get_option_contracts: Callable[..., Any],
    get_strategy_parameter_sets: Callable[..., Any],
    get_strategy_promotions: Callable[..., Any],
    get_active_strategy_parameter_set: Callable[..., Any],
    build_promotion_gate_report: Callable[[str], Any],
    default_strategy_key: str,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        calibration_runs = get_calibration_runs(limit=20)
        latest_calibration_run_id = calibration_runs[0]["id"] if calibration_runs else ""
        return {
            "option_summary": get_option_contract_summary(),
            "option_contracts": get_option_contracts(limit=40),
            "calibration_runs": calibration_runs,
            "latest_calibration_run_id": latest_calibration_run_id,
            "strategy_sets": get_strategy_parameter_sets(
                limit=20,
                strategy_key=default_strategy_key,
            ),
            "strategy_promotions": get_strategy_promotions(
                limit=20,
                strategy_key=default_strategy_key,
            ),
            "active_shadow_set": get_active_strategy_parameter_set(default_strategy_key, status="shadow"),
            "active_staged_set": get_active_strategy_parameter_set(default_strategy_key, status="staged_live"),
            "active_live_set": get_active_strategy_parameter_set(default_strategy_key, status="live"),
            "promotion_gate": build_promotion_gate_report(default_strategy_key),
        }

    return get_cached_value(
        "research-fragment",
        research_cache_ttl_seconds,
        _load,
        stale_on_error=True,
    )


def _get_research_pipeline_funnel_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    pipeline_funnel: Callable[[], list[dict[str, Any]]],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            stages = pipeline_funnel()
            return {
                "stages": stages,
                "total": sum(int(stage.get("total", 0)) for stage in stages),
                "error": "",
            }
        except Exception as exc:
            logger.debug("Research pipeline funnel unavailable: %s", exc)
            return {"stages": [], "total": 0, "error": str(exc)}

    return get_cached_value(
        "research-pipeline-funnel",
        15.0,
        _load,
        stale_on_error=True,
    )


def _get_research_readiness_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_research_readiness_report: Callable[..., dict[str, Any]],
    pipeline_status: Callable[[], dict[str, Any]],
    utc_now_iso: Callable[[], str],
    research_system_active: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            return build_research_readiness_report(
                pipeline_status=pipeline_status(),
            )
        except Exception as exc:
            logger.debug("Research readiness unavailable: %s", exc)
            return {
                "as_of": utc_now_iso()[:10],
                "generated_at": utc_now_iso(),
                "overall_status": "attention",
                "routing_mode": "research_primary" if research_system_active else "mirror",
                "checks": [],
                "issues": [str(exc)],
                "stage_counts": {},
                "review_pending_count": 0,
                "pilot_signoff_pending_count": 0,
                "error": str(exc),
            }

    return get_cached_value(
        "research-readiness",
        15.0,
        _load,
        stale_on_error=True,
    )


def _summary_active_view_for_lane(queue_lane: str, normalize_research_queue_lane: Callable[[str], str]) -> str:
    normalized_lane = normalize_research_queue_lane(queue_lane)
    if normalized_lane in {"review", "pilot"}:
        return "operator"
    if normalized_lane == "rebalance":
        return "all"
    return "flow"


def _summary_active_view_for_chain(chain: dict[str, Any] | None) -> str:
    if not isinstance(chain, dict):
        return "all"
    freshness = str(chain.get("freshness") or "").strip().lower()
    if freshness == "stale":
        return "stale"
    operator_now = str(chain.get("operator_now") or "").strip().lower()
    if operator_now in {"true", "1", "yes"}:
        return "operator"
    board_group = str(chain.get("board_group") or "").strip().lower()
    if board_group == "operator":
        return "operator"
    if board_group == "flow":
        return "flow"
    stage = str(chain.get("stage") or "").strip().lower()
    if stage in {"pilot_ready", "review_pending", "review_revise", "review_parked"}:
        return "operator"
    if stage:
        return "flow"
    return "all"


def _get_research_operating_summary_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    operating_summary: Callable[[], dict[str, Any]],
    build_engine_a_rebalance_panel_context: Callable[[], dict[str, Any]],
    build_research_queue_follow_up_context: Callable[..., dict[str, Any] | None],
    normalize_research_queue_lane: Callable[[str], str],
    utc_now_iso: Callable[[], str],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            summary = operating_summary()
            try:
                rebalance_panel = build_engine_a_rebalance_panel_context()
                rebalance = rebalance_panel.get("rebalance")
            except Exception as exc:
                logger.debug("Research operating summary rebalance context unavailable: %s", exc)
                rebalance = None

            rebalance_ready_count = 0
            rebalance_detail = "No Engine A proposal waiting."
            rebalance_anchor = "#research-portfolio-expression"
            rebalance_tone = "idle"
            if rebalance:
                if rebalance.get("executed"):
                    rebalance_detail = "Latest proposal already executed."
                    rebalance_tone = "clear"
                elif rebalance.get("can_execute") or rebalance.get("can_dismiss"):
                    rebalance_ready_count = 1
                    rebalance_detail = (
                        f"{int(rebalance.get('move_count') or 0)} move(s) queued"
                        f" · cost {float(rebalance.get('estimated_cost') or 0.0):.4f}"
                    )
                    rebalance_tone = "warning"
                else:
                    rebalance_detail = "Proposal recorded but not actionable yet."
                    rebalance_tone = "neutral"

            if (
                rebalance_ready_count
                and int(summary.get("urgent_review_count") or 0) == 0
                and int(summary.get("pilot_ready_count") or 0) == 0
                and int((summary.get("freshness_counts") or {}).get("stale") or 0) == 0
            ):
                summary["focus_title"] = "Rebalance waiting"
                summary["focus_detail"] = "Engine A has a queued rebalance proposal that still needs an operator call."
                summary["focus_tone"] = "warning"
                summary["focus_anchor"] = rebalance_anchor

            summary["rebalance_ready_count"] = rebalance_ready_count
            recommended_card: dict[str, Any] | None = None
            queue_follow_up = build_research_queue_follow_up_context("all")
            if queue_follow_up and queue_follow_up.get("mode") == "next_item":
                priority = str(queue_follow_up.get("priority") or "routine").strip() or "routine"
                queue_filter = normalize_research_queue_lane(str(queue_follow_up.get("lane") or "all"))
                recommended_card = {
                    "mode": "queue_item",
                    "card_label": "Suggested Queue Entry",
                    "chain_id": str(queue_follow_up.get("chain_id") or "").strip(),
                    "title": str(queue_follow_up.get("title") or "Queue item").strip() or "Queue item",
                    "headline": str(queue_follow_up.get("lane_label") or "Queue").strip() or "Queue",
                    "detail": str(queue_follow_up.get("detail") or "").strip(),
                    "badge": priority,
                    "badge_tone": "warning" if priority in {"urgent", "watch"} else "clear",
                    "meta": (
                        f"{str(queue_follow_up.get('status_label') or '').strip()}: "
                        f"{str(queue_follow_up.get('status_value') or '-').strip() or '-'}"
                    ).strip(),
                    "submeta": str(queue_follow_up.get("meta_value") or "-").strip() or "-",
                    "button_label": str(queue_follow_up.get("open_label") or "Open Suggested Chain").strip()
                    or "Open Suggested Chain",
                    "queue_filter": queue_filter,
                    "secondary_queue_filter": queue_filter,
                    "active_view": _summary_active_view_for_lane(queue_filter, normalize_research_queue_lane),
                    "secondary_anchor": "#research-alerts",
                    "secondary_label": "Open Decision Queue",
                }
            elif isinstance(summary.get("latest_chain"), dict):
                latest_chain = summary["latest_chain"]
                freshness = str(latest_chain.get("freshness") or "unknown").strip() or "unknown"
                recommended_card = {
                    "mode": "latest_chain",
                    "card_label": "Latest Chain",
                    "chain_id": str(latest_chain.get("chain_id") or "").strip(),
                    "title": str(latest_chain.get("ticker") or "-").strip() or "-",
                    "headline": str(latest_chain.get("stage") or "-").strip() or "-",
                    "detail": str(latest_chain.get("next_action") or "").strip(),
                    "badge": freshness,
                    "badge_tone": freshness,
                    "meta": str(latest_chain.get("updated_label") or "-").strip() or "-",
                    "submeta": str(latest_chain.get("created_label") or "").strip(),
                    "button_label": "Open Latest Chain",
                    "queue_filter": "",
                    "secondary_queue_filter": "",
                    "active_view": _summary_active_view_for_chain(latest_chain),
                    "secondary_anchor": "#research-loop",
                    "secondary_label": "Browse Active Research",
                }
            summary["recommended_card"] = recommended_card
            summary["lane_cards"] = [
                {
                    "id": "review",
                    "label": "Review Lane",
                    "count": int(summary.get("pending_review_count") or 0),
                    "detail": (
                        f"{int(summary.get('urgent_review_count') or 0)} urgent"
                        f" / {int(summary.get('watch_review_count') or 0)} watch"
                    ),
                    "tone": (
                        "urgent"
                        if int(summary.get("urgent_review_count") or 0) > 0
                        else "warning"
                        if int(summary.get("pending_review_count") or 0) > 0
                        else "idle"
                    ),
                    "anchor": "#research-alerts",
                    "queue_filter": "review",
                    "active_view": "operator",
                },
                {
                    "id": "pilot",
                    "label": "Pilot Lane",
                    "count": int(summary.get("pilot_ready_count") or 0),
                    "detail": (
                        f"{int(summary.get('review_pending_stage_count') or 0)} already in review pending"
                        if int(summary.get("pilot_ready_count") or 0) > 0
                        else "No pilot sign-off waiting."
                    ),
                    "tone": "warning" if int(summary.get("pilot_ready_count") or 0) > 0 else "idle",
                    "anchor": "#research-alerts",
                    "queue_filter": "pilot",
                    "active_view": "operator",
                },
                {
                    "id": "rebalance",
                    "label": "Rebalance Lane",
                    "count": rebalance_ready_count,
                    "detail": rebalance_detail,
                    "tone": rebalance_tone,
                    "anchor": "#research-alerts" if rebalance_ready_count else rebalance_anchor,
                    "queue_filter": "rebalance",
                    "active_view": "all",
                },
                {
                    "id": "flow",
                    "label": "Flow Lane",
                    "count": int(summary.get("active_chain_count") or 0),
                    "detail": (
                        f"{int((summary.get('freshness_counts') or {}).get('fresh') or 0)} fresh"
                        f" / {int((summary.get('freshness_counts') or {}).get('aging') or 0)} aging"
                        f" / {int((summary.get('freshness_counts') or {}).get('stale') or 0)} stale"
                    ),
                    "tone": (
                        "warning"
                        if int((summary.get("freshness_counts") or {}).get("stale") or 0) > 0
                        else "clear"
                        if int(summary.get("active_chain_count") or 0) > 0
                        else "idle"
                    ),
                    "anchor": "#research-loop",
                    "queue_filter": "all",
                    "active_view": "flow",
                },
            ]
            return summary
        except Exception as exc:
            logger.debug("Research operating summary unavailable: %s", exc)
            return {
                "focus_title": "Operating summary unavailable",
                "focus_detail": str(exc),
                "focus_tone": "idle",
                "focus_anchor": "#research-loop",
                "active_chain_count": 0,
                "freshness_counts": {"fresh": 0, "aging": 0, "stale": 0},
                "pending_review_count": 0,
                "urgent_review_count": 0,
                "watch_review_count": 0,
                "pilot_ready_count": 0,
                "rebalance_ready_count": 0,
                "review_pending_stage_count": 0,
                "lane_cards": [
                    {
                        "id": "review",
                        "label": "Review Lane",
                        "count": 0,
                        "detail": "Operating summary unavailable.",
                        "tone": "idle",
                        "anchor": "#research-alerts",
                        "queue_filter": "review",
                        "active_view": "operator",
                    },
                    {
                        "id": "pilot",
                        "label": "Pilot Lane",
                        "count": 0,
                        "detail": "Operating summary unavailable.",
                        "tone": "idle",
                        "anchor": "#research-alerts",
                        "queue_filter": "pilot",
                        "active_view": "operator",
                    },
                    {
                        "id": "rebalance",
                        "label": "Rebalance Lane",
                        "count": 0,
                        "detail": "Operating summary unavailable.",
                        "tone": "idle",
                        "anchor": "#research-alerts",
                        "queue_filter": "rebalance",
                        "active_view": "all",
                    },
                    {
                        "id": "flow",
                        "label": "Flow Lane",
                        "count": 0,
                        "detail": "Operating summary unavailable.",
                        "tone": "idle",
                        "anchor": "#research-loop",
                        "queue_filter": "all",
                        "active_view": "flow",
                    },
                ],
                "latest_chain": None,
                "latest_decision": None,
                "generated_at": utc_now_iso(),
                "error": str(exc),
            }

    return get_cached_value(
        "research-operating-summary",
        10.0,
        _load,
        stale_on_error=True,
    )


def _get_research_active_hypotheses_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    active_hypotheses: Callable[..., list[dict[str, Any]]],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            rows = active_hypotheses(limit=20)
            operator_rows = [row for row in rows if row.get("operator_now")]
            flow_rows = [row for row in rows if not row.get("operator_now")]
            operator_lane_map: dict[str, dict[str, Any]] = {}
            for row in operator_rows:
                lane_key = str(row.get("operator_lane_label") or "Operator Lane").strip().lower().replace(" ", "_")
                lane = operator_lane_map.setdefault(
                    lane_key,
                    {
                        "key": lane_key,
                        "label": row.get("operator_lane_label") or "Operator Lane",
                        "rows": [],
                        "count": 0,
                        "urgent_count": 0,
                        "watch_count": 0,
                    },
                )
                lane["rows"].append(row)
                lane["count"] += 1
                if row.get("operator_priority") == "urgent":
                    lane["urgent_count"] += 1
                elif row.get("operator_priority") == "watch":
                    lane["watch_count"] += 1
            operator_lanes = sorted(
                operator_lane_map.values(),
                key=lambda lane: (-lane["urgent_count"], -lane["count"], lane["label"]),
            )
            operator_focus = None
            if operator_lanes:
                for lane in operator_lanes:
                    lead_row = next(
                        (row for row in lane["rows"] if row.get("operator_priority") == "urgent"),
                        next(
                            (row for row in lane["rows"] if row.get("operator_priority") == "watch"),
                            lane["rows"][0],
                        ),
                    )
                    lane["lead_row"] = lead_row
                    lane["lead_button_label"] = (
                        "Open Urgent Chain"
                        if lane["urgent_count"]
                        else ("Open Watch Chain" if lane["watch_count"] else "Open Lead Chain")
                    )
                focus_lane = operator_lanes[0]
                lead_row = focus_lane["lead_row"]
                operator_focus = {
                    "lane_label": focus_lane["label"],
                    "chain_id": lead_row["chain_id"],
                    "ticker": lead_row["ticker"],
                    "updated_label": lead_row["updated_label"],
                    "priority": lead_row.get("operator_priority") or "routine",
                    "button_label": focus_lane["lead_button_label"],
                    "detail": (
                        f"{focus_lane['urgent_count']} urgent chain(s) need action in this lane."
                        if focus_lane["urgent_count"]
                        else (
                            f"{focus_lane['watch_count']} watch item(s) are aging in this lane."
                            if focus_lane["watch_count"]
                            else f"{focus_lane['count']} chain(s) are ready for operator action in this lane."
                        )
                    ),
                }
            flow_lane_map: dict[str, dict[str, Any]] = {}
            for row in flow_rows:
                lane_key = str(row.get("flow_lane_key") or "active")
                lane = flow_lane_map.setdefault(
                    lane_key,
                    {
                        "key": lane_key,
                        "label": row.get("flow_lane_label") or "Active",
                        "order": int(row.get("flow_lane_order") or 99),
                        "rows": [],
                        "count": 0,
                        "fresh_count": 0,
                        "stale_count": 0,
                    },
                )
                lane["rows"].append(row)
                lane["count"] += 1
                if row.get("freshness") == "fresh":
                    lane["fresh_count"] += 1
                if row.get("freshness") == "stale":
                    lane["stale_count"] += 1
            flow_lanes = sorted(flow_lane_map.values(), key=lambda lane: (lane["order"], lane["label"]))
            flow_focus = None
            if flow_lanes:
                for lane in flow_lanes:
                    lead_row = next((row for row in lane["rows"] if row.get("freshness") == "stale"), lane["rows"][0])
                    lane["lead_row"] = lead_row
                    lane["lead_button_label"] = "Open Stale Chain" if lane["stale_count"] else "Open Lead Chain"
                focus_lane = sorted(
                    flow_lanes,
                    key=lambda lane: (-lane["stale_count"], -lane["count"], lane["order"], lane["label"]),
                )[0]
                lead_row = focus_lane["lead_row"]
                flow_focus = {
                    "lane_label": focus_lane["label"],
                    "chain_id": lead_row["chain_id"],
                    "ticker": lead_row["ticker"],
                    "updated_label": lead_row["updated_label"],
                    "freshness": lead_row["freshness"],
                    "button_label": focus_lane["lead_button_label"],
                    "detail": (
                        f"{focus_lane['stale_count']} stale chain(s) are backing up this lane."
                        if focus_lane["stale_count"]
                        else f"{focus_lane['count']} chain(s) are currently moving through this lane."
                    ),
                }
            board_focus = None
            if operator_focus:
                board_focus = {
                    "source": "operator",
                    "tone": "operator",
                    "section_label": "Needs Operator Now",
                    "title": operator_focus["lane_label"],
                    "detail": operator_focus["detail"],
                    "chain_id": operator_focus["chain_id"],
                    "ticker": operator_focus["ticker"],
                    "meta": f"{operator_focus['updated_label']} · {operator_focus['priority']}",
                    "button_label": "Open Operator Chain",
                    "priority_reason": "Operator-ready work takes precedence over the in-flight flow.",
                }
                if flow_focus:
                    board_focus["secondary"] = {
                        "section_label": "Then Inspect Flow",
                        "title": flow_focus["lane_label"],
                        "detail": flow_focus["detail"],
                        "chain_id": flow_focus["chain_id"],
                        "ticker": flow_focus["ticker"],
                        "meta": f"{flow_focus['updated_label']} · {flow_focus['freshness']}",
                        "button_label": "Then Open Flow Chain",
                    }
            elif flow_focus:
                board_focus = {
                    "source": "flow",
                    "tone": "flow",
                    "section_label": "Still Flowing",
                    "title": flow_focus["lane_label"],
                    "detail": flow_focus["detail"],
                    "chain_id": flow_focus["chain_id"],
                    "ticker": flow_focus["ticker"],
                    "meta": f"{flow_focus['updated_label']} · {flow_focus['freshness']}",
                    "button_label": "Open Flow Chain",
                    "priority_reason": "No operator-ready chains are waiting, so the board falls back to the most backed-up flow lane.",
                }
            freshness_counts = {
                "fresh": sum(1 for row in rows if row.get("freshness") == "fresh"),
                "aging": sum(1 for row in rows if row.get("freshness") == "aging"),
                "stale": sum(1 for row in rows if row.get("freshness") == "stale"),
            }
            return {
                "rows": rows,
                "operator_rows": operator_rows,
                "operator_lanes": operator_lanes,
                "operator_focus": operator_focus,
                "flow_rows": flow_rows,
                "flow_lanes": flow_lanes,
                "flow_focus": flow_focus,
                "board_focus": board_focus,
                "operator_count": len(operator_rows),
                "flow_count": len(flow_rows),
                "freshness_counts": freshness_counts,
                "error": "",
            }
        except Exception as exc:
            logger.debug("Research active hypotheses unavailable: %s", exc)
            return {
                "rows": [],
                "operator_rows": [],
                "operator_lanes": [],
                "operator_focus": None,
                "flow_rows": [],
                "flow_lanes": [],
                "flow_focus": None,
                "board_focus": None,
                "operator_count": 0,
                "flow_count": 0,
                "freshness_counts": {"fresh": 0, "aging": 0, "stale": 0},
                "error": str(exc),
            }

    return get_cached_value(
        "research-active-hypotheses",
        10.0,
        _load,
        stale_on_error=True,
    )


def _get_research_engine_status_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    pipeline_status: Callable[[], dict[str, Any]],
    pipeline_funnel: Callable[[], list[dict[str, Any]]],
    utc_now_iso: Callable[[], str],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        pipeline = pipeline_status()
        research_db = pipeline.get("research_db") or {}
        try:
            funnel = pipeline_funnel()
            funnel_total = sum(int(stage.get("total", 0)) for stage in funnel)
        except Exception as exc:
            logger.debug("Research engine status counts unavailable: %s", exc)
            funnel_total = 0
        engine_cards = [
            {
                "name": "Scheduler",
                "key": "scheduler",
                "running": bool(pipeline["scheduler"].get("running")),
                "configured": True,
                "status_detail": "Daily orchestration windows",
                "last_result": (pipeline["scheduler"].get("recent_results") or [{}])[-1],
            },
            {
                "name": "Engine A",
                "key": "engine_a",
                "running": bool(pipeline["engine_a"].get("running")),
                "configured": bool(pipeline["engine_a"].get("configured")),
                "enabled": bool(pipeline["engine_a"].get("enabled")),
                "status_detail": "Deterministic futures pipeline",
                "last_result": pipeline["engine_a"].get("last_result"),
            },
            {
                "name": "Engine B",
                "key": "engine_b",
                "running": bool(pipeline["engine_b"].get("running")),
                "configured": bool(pipeline["engine_b"].get("configured")),
                "enabled": bool(pipeline["engine_b"].get("enabled")),
                "status_detail": (
                    f"Event-driven research intake worker"
                    f" | q={pipeline['engine_b'].get('queue_depth', 0)}"
                ),
                "last_result": pipeline["engine_b"].get("last_result"),
            },
            {
                "name": "Research DB",
                "key": "research_db",
                "running": bool(research_db.get("schema_ready")),
                "configured": bool(research_db.get("configured")) and bool(research_db.get("driver_available")),
                "enabled": True,
                "status_detail": str(research_db.get("detail") or "Research PostgreSQL status"),
                "last_result": {
                    "status": research_db.get("status"),
                    "as_of": "schema_ready" if research_db.get("schema_ready") else "attention_required",
                },
            },
            {
                "name": "Dispatcher",
                "key": "dispatcher",
                "running": bool(pipeline["dispatcher"].get("running")),
                "configured": True,
                "enabled": bool(pipeline["config"].get("dispatcher_enabled")),
                "status_detail": "Intent routing loop",
                "last_result": None,
            },
            {
                "name": "Decay Review",
                "key": "decay_review",
                "running": False,
                "configured": bool(pipeline["decay_review"].get("configured")),
                "status_detail": "6-hourly decay audit windows",
                "last_result": pipeline["decay_review"].get("last_result"),
            },
            {
                "name": "Kill Check",
                "key": "kill_check",
                "running": False,
                "configured": bool(pipeline["kill_check"].get("configured")),
                "status_detail": "Hourly live-strategy kill scan",
                "last_result": pipeline["kill_check"].get("last_result"),
            },
        ]
        return {
            "engine_cards": engine_cards,
            "pipeline": pipeline,
            "funnel_total": funnel_total,
            "generated_at": utc_now_iso(),
        }

    return get_cached_value(
        "research-engine-status",
        5.0,
        _load,
        stale_on_error=True,
    )


def _get_research_recent_decisions_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    recent_decisions: Callable[..., list[dict[str, Any]]],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            rows = recent_decisions(limit=20)
            return {"rows": rows, "error": ""}
        except Exception as exc:
            logger.debug("Research recent decisions unavailable: %s", exc)
            return {"rows": [], "error": str(exc)}

    return get_cached_value(
        "research-recent-decisions",
        15.0,
        _load,
        stale_on_error=True,
    )


def _get_research_alerts_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    alerts_loader: Callable[..., dict[str, Any]],
    build_engine_a_rebalance_panel_context: Callable[[], dict[str, Any]],
    relative_time_label: Callable[[Any], str],
    logger: logging.Logger,
) -> dict[str, Any]:
    def _load() -> dict[str, Any]:
        try:
            alerts = alerts_loader(limit=20)
            rebalance_items: list[dict[str, Any]] = []
            rebalance_payload = build_engine_a_rebalance_panel_context()
            rebalance = rebalance_payload.get("rebalance")
            if (
                rebalance
                and int(rebalance.get("move_count") or 0) > 0
                and not bool(rebalance.get("executed"))
            ):
                rebalance_items.append(
                    {
                        "artifact_id": str(rebalance.get("artifact_id") or ""),
                        "chain_id": str(rebalance.get("chain_id") or ""),
                        "approval_status": str(rebalance.get("approval_status") or ""),
                        "decision_source": str(rebalance.get("decision_source") or ""),
                        "estimated_cost": float(rebalance.get("estimated_cost") or 0.0),
                        "move_count": int(rebalance.get("move_count") or 0),
                        "can_execute": bool(rebalance.get("can_execute")),
                        "can_dismiss": bool(rebalance.get("can_dismiss")),
                        "created_at": str(rebalance.get("created_at") or ""),
                        "created_label": relative_time_label(rebalance.get("created_at")),
                        "priority": "watch",
                        "top_moves": list(rebalance.get("top_moves") or [])[:3],
                    }
                )
            alerts["rebalance_items"] = rebalance_items
            alerts["lane_counts"] = {
                "reviews": len(alerts.get("pending_reviews") or []),
                "pilots": len(alerts.get("pending_pilots") or []),
                "rebalances": len(rebalance_items),
                "retirements": len(alerts.get("kill_alerts") or []),
                "total_pending": (
                    len(alerts.get("pending_reviews") or [])
                    + len(alerts.get("pending_pilots") or [])
                    + len(rebalance_items)
                ),
            }
            alerts["error"] = ""
            return alerts
        except Exception as exc:
            logger.debug("Research alerts unavailable: %s", exc)
            return {
                "pending_reviews": [],
                "pending_pilots": [],
                "rebalance_items": [],
                "kill_alerts": [],
                "lane_counts": {
                    "reviews": 0,
                    "pilots": 0,
                    "rebalances": 0,
                    "retirements": 0,
                    "total_pending": 0,
                },
                "error": str(exc),
            }

    return get_cached_value(
        "research-alerts",
        5.0,
        _load,
        stale_on_error=True,
    )


def _get_research_regime_panel_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_engine_a_regime_panel_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "research-regime-panel",
        10.0,
        build_engine_a_regime_panel_context,
        stale_on_error=True,
    )


def _get_research_signal_heatmap_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_engine_a_signal_heatmap_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "research-signal-heatmap",
        10.0,
        build_engine_a_signal_heatmap_context,
        stale_on_error=True,
    )


def _get_research_portfolio_targets_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_engine_a_portfolio_targets_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "research-portfolio-targets",
        10.0,
        build_engine_a_portfolio_targets_context,
        stale_on_error=True,
    )


def _get_research_rebalance_panel_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_engine_a_rebalance_panel_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "research-rebalance-panel",
        10.0,
        build_engine_a_rebalance_panel_context,
        stale_on_error=True,
    )


def _get_research_regime_journal_context(
    *,
    get_cached_value: Callable[..., dict[str, Any]],
    build_engine_a_regime_journal_context: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return get_cached_value(
        "research-regime-journal",
        15.0,
        build_engine_a_regime_journal_context,
        stale_on_error=True,
    )


def _build_research_system_state_context(
    *,
    pipeline_status: Callable[[], dict[str, Any]],
    research_system_active: bool,
) -> dict[str, Any]:
    try:
        pipeline = pipeline_status()
    except Exception:
        pipeline = {}

    engine_b = (pipeline.get("engine_b") or {}) if isinstance(pipeline, dict) else {}
    research_db = (pipeline.get("research_db") or {}) if isinstance(pipeline, dict) else {}
    running = bool(engine_b.get("running"))
    status = str(engine_b.get("status") or ("running" if running else "stopped"))
    queue_depth = int(engine_b.get("queue_depth") or 0)
    active = bool(research_system_active)
    return {
        "research_system_active": active,
        "research_route_label": "Engine B Primary" if active else "Council Primary + Engine B Mirror",
        "research_route_detail": (
            "New intel intake routes directly into Engine B research."
            if active
            else "New intel still enters the legacy council flow while mirroring into Engine B research."
        ),
        "engine_b_state": {
            "running": running,
            "status": status,
            "queue_depth": queue_depth,
        },
        "research_db_state": {
            "status": str(research_db.get("status") or "unknown"),
            "schema_ready": bool(research_db.get("schema_ready")),
            "detail": str(research_db.get("detail") or ""),
        },
    }
