"""Research workbench and archive view-context builders."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from intelligence.event_store import EventStore
from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactType, Engine


def _build_research_lane_focus(next_lane: str) -> dict[str, str]:
    normalized = str(next_lane or "").strip().lower()
    if normalized.startswith("review"):
        return {
            "queue_filter": "review",
            "queue_label": "Review Lane",
            "queue_anchor": "#research-alerts",
            "active_view": "operator",
            "active_view_label": "Operator",
        }
    if normalized.startswith("pilot"):
        return {
            "queue_filter": "pilot",
            "queue_label": "Pilot Lane",
            "queue_anchor": "#research-alerts",
            "active_view": "operator",
            "active_view_label": "Operator",
        }
    if normalized.startswith("rebalance"):
        return {
            "queue_filter": "rebalance",
            "queue_label": "Rebalance Lane",
            "queue_anchor": "#research-alerts",
            "active_view": "all",
            "active_view_label": "All",
        }
    return {
        "queue_filter": "all",
        "queue_label": "Flow Lane",
        "queue_anchor": "#research-loop",
        "active_view": "flow",
        "active_view_label": "Flow",
    }


def _normalize_research_queue_lane(lane: str) -> str:
    normalized = str(lane or "").strip().lower()
    return normalized if normalized in {"all", "review", "pilot", "rebalance", "retirements"} else "all"


def _normalize_research_active_view(view: str) -> str:
    normalized = str(view or "").strip().lower()
    return normalized if normalized in {"all", "focus", "operator", "flow", "stale"} else "all"


def _research_queue_lane_label(lane: str) -> str:
    normalized = _normalize_research_queue_lane(lane)
    if normalized == "review":
        return "Review Lane"
    if normalized == "pilot":
        return "Pilot Lane"
    if normalized == "rebalance":
        return "Rebalance Lane"
    if normalized == "retirements":
        return "Retirements"
    return "All Lanes"


def _research_active_view_for_lane(lane: str) -> str:
    normalized = str(lane or "").strip().lower()
    if normalized in {"review", "pilot"}:
        return "operator"
    if normalized == "flow":
        return "flow"
    return "all"


def _research_active_view_label(view: str) -> str:
    normalized = _normalize_research_active_view(view)
    if normalized == "focus":
        return "Board Focus"
    if normalized == "operator":
        return "Operator"
    if normalized == "flow":
        return "Flow"
    if normalized == "stale":
        return "Stale"
    return "All"


def _build_research_operator_output_context(
    *,
    chain_id: str = "",
    queue_lane: str = "all",
    active_view: str = "all",
    synthesis: dict[str, Any] | None = None,
    operator_action: dict[str, Any] | None = None,
    pilot_decision: dict[str, Any] | None = None,
    post_mortem: dict[str, Any] | None = None,
    queued_intake: dict[str, Any] | None = None,
    error: str = "",
    build_research_artifact_chain_context: Callable[..., dict[str, Any]],
    build_research_workbench_queue_alignment: Callable[..., dict[str, Any] | None],
    build_research_queue_follow_up_context: Callable[..., dict[str, Any] | None],
    build_research_operating_summary_context: Callable[[], dict[str, Any]],
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    active_chain_id = chain_id
    if not active_chain_id and synthesis:
        active_chain_id = str(synthesis.get("chain_id") or "")
    if not active_chain_id and operator_action:
        active_chain_id = str(operator_action.get("chain_id") or "")
    if not active_chain_id and pilot_decision:
        active_chain_id = str(pilot_decision.get("chain_id") or "")
    if not active_chain_id and post_mortem:
        active_chain_id = str(post_mortem.get("chain_id") or "")
    selected_queue_lane = _normalize_research_queue_lane(queue_lane)
    selected_active_view = _normalize_research_active_view(active_view)
    active_chain = None
    queue_alignment = None
    queue_follow_up = None
    if (
        active_chain_id
        and not error
        and synthesis is None
        and operator_action is None
        and pilot_decision is None
        and post_mortem is None
        and queued_intake is None
    ):
        chain_context = build_research_artifact_chain_context(active_chain_id)
        if int(chain_context.get("artifact_count") or 0) > 0:
            active_chain = chain_context
            queue_alignment = build_research_workbench_queue_alignment(chain_context, selected_queue_lane)
    if not error and not queued_intake:
        queue_follow_up = build_research_queue_follow_up_context(
            selected_queue_lane,
            exclude_chain_id=active_chain_id,
        )
    return {
        "chain_id": active_chain_id,
        "active_chain": active_chain,
        "selected_queue_lane": selected_queue_lane,
        "selected_queue_label": _research_queue_lane_label(selected_queue_lane),
        "selected_active_view": selected_active_view,
        "selected_active_view_label": _research_active_view_label(selected_active_view),
        "return_to_active_view": selected_active_view,
        "return_to_active_view_label": _research_active_view_label(selected_active_view),
        "return_to_queue_label": (
            "Return to Queue"
            if selected_queue_lane == "all"
            else f"Return to {_research_queue_lane_label(selected_queue_lane)}"
        ),
        "queue_alignment": queue_alignment,
        "queue_follow_up": queue_follow_up,
        "synthesis": synthesis,
        "operator_action": operator_action,
        "pilot_decision": pilot_decision,
        "post_mortem": post_mortem,
        "queued_intake": queued_intake,
        "error": error,
        "generated_at": utc_now_iso(),
    }


def _build_research_focus_ribbon_context(
    chain_id: str = "",
    queue_lane: str = "all",
    active_view: str = "all",
    suppress_auto_sync: bool = False,
    artifact_store: ArtifactStore | None = None,
    artifact_store_factory: Callable[[], ArtifactStore] | None = None,
    build_research_artifact_chain_context: Callable[..., dict[str, Any]] | None = None,
    build_research_operating_summary_context: Callable[[], dict[str, Any]] | None = None,
    build_research_queue_follow_up_context: Callable[..., dict[str, Any] | None] | None = None,
    utc_now_iso: Callable[[], str] | None = None,
) -> dict[str, Any]:
    if (
        build_research_artifact_chain_context is None
        or build_research_operating_summary_context is None
        or build_research_queue_follow_up_context is None
        or utc_now_iso is None
    ):
        raise ValueError("research focus ribbon context requires callback dependencies")

    store = artifact_store
    requested_chain_id = str(chain_id or "").strip()
    current_queue_lane = _normalize_research_queue_lane(queue_lane)
    current_active_view = _normalize_research_active_view(active_view)
    missing_error = ""

    if requested_chain_id:
        if store is None:
            if artifact_store_factory is None:
                raise ValueError("artifact_store_factory is required when artifact_store is not provided")
            store = artifact_store_factory()
        selected_chain = build_research_artifact_chain_context(requested_chain_id, artifact_store=store)
        if int(selected_chain.get("artifact_count") or 0) > 0:
            latest = selected_chain.get("latest") or {}
            lane_focus = _build_research_lane_focus(str(selected_chain.get("next_lane") or ""))
            review_pending = bool(selected_chain.get("review_ack_pending"))
            rebalance_pending = bool(
                selected_chain.get("rebalance_can_execute") or selected_chain.get("rebalance_can_dismiss")
            )
            pilot_pending = bool(selected_chain.get("pilot_signoff_pending"))
            synthesis_ready = not review_pending and not rebalance_pending and not pilot_pending
            pilot_decision = selected_chain.get("pilot_decision") or {}
            pilot_decision_body = pilot_decision.get("body") if isinstance(pilot_decision, dict) else {}
            if not isinstance(pilot_decision_body, dict):
                pilot_decision_body = {}
            action_readiness = [
                {
                    "label": "Review Ack",
                    "state": "pending" if review_pending else "recorded" if selected_chain.get("review_context") else "idle",
                    "detail": (
                        f"{selected_chain.get('review_recommended_action') or 'review'} recommended"
                        if selected_chain.get("review_context")
                        else "No review trigger on this chain."
                    ),
                    "tone": "warning" if review_pending else "neutral",
                },
                {
                    "label": "Rebalance",
                    "state": (
                        "pending"
                        if rebalance_pending
                        else "executed"
                        if selected_chain.get("rebalance_executed")
                        else "recorded"
                        if selected_chain.get("rebalance_context")
                        else "idle"
                    ),
                    "detail": (
                        f"{int((selected_chain.get('rebalance_context') or {}).get('move_count') or 0)} move(s)"
                        if selected_chain.get("rebalance_context")
                        else "No rebalance proposal on this chain."
                    ),
                    "tone": "positive" if rebalance_pending else "neutral",
                },
                {
                    "label": "Pilot Sign-Off",
                    "state": (
                        "pending"
                        if pilot_pending
                        else str(pilot_decision_body.get("operator_decision") or "").strip().lower() or "idle"
                    ),
                    "detail": (
                        "Approve or reject the trade expression."
                        if pilot_pending
                        else "Pilot decision already recorded."
                        if pilot_decision
                        else "No pilot sign-off needed yet."
                    ),
                    "tone": "warning" if pilot_pending else "neutral",
                },
                {
                    "label": "Synthesis",
                    "state": "ready" if synthesis_ready else "waiting",
                    "detail": (
                        str(selected_chain.get("next_operator_move") or "inspect chain").strip()
                        if synthesis_ready
                        else "Higher-priority review, pilot, or rebalance action comes first."
                    ),
                    "tone": "positive" if synthesis_ready else "neutral",
                },
                {
                    "label": "Post-Mortem",
                    "state": (
                        "recorded"
                        if int(selected_chain.get("post_mortem_count") or 0) > 0
                        else "available"
                        if selected_chain.get("can_generate_post_mortem")
                        else "idle"
                    ),
                    "detail": (
                        f"{int(selected_chain.get('post_mortem_count') or 0)} note(s) captured"
                        if int(selected_chain.get("post_mortem_count") or 0) > 0
                        else "Capture a learning memo once the loop closes."
                        if selected_chain.get("can_generate_post_mortem")
                        else "No post-mortem lane available yet."
                    ),
                    "tone": "neutral",
                },
            ]
            chain_lane = str(selected_chain.get("next_lane") or "").strip().lower()
            if chain_lane.startswith("review"):
                quick_action_mode = "review"
            elif chain_lane.startswith("rebalance"):
                quick_action_mode = "rebalance"
            elif chain_lane.startswith("pilot"):
                quick_action_mode = "pilot"
            else:
                quick_action_mode = "synthesis"
            return {
                "mode": "selected",
                "focus_label": "Selected Chain",
                "chain_id": requested_chain_id,
                "ticker": str(latest.get("ticker") or "").strip(),
                "engine": str(latest.get("engine") or "").strip(),
                "title": str(selected_chain.get("operator_posture_title") or "Selected chain").strip(),
                "detail": str(selected_chain.get("operator_posture_detail") or "").strip(),
                "tone": str(selected_chain.get("operator_posture_tone") or "neutral").strip(),
                "latest_meta_label": "Latest Artifact",
                "latest_label": str(latest.get("artifact_label") or "Artifact").strip(),
                "status_label": "Status",
                "status_value": str(latest.get("status") or "-").strip() or "-",
                "artifact_count": int(selected_chain.get("artifact_count") or 0),
                "activity_label": "Latest Update",
                "activity_value": str(selected_chain.get("latest_created_label") or "-").strip() or "-",
                "started_label": "Started",
                "started_value": str(selected_chain.get("first_created_label") or "-").strip() or "-",
                "next_lane": str(selected_chain.get("next_lane") or "review").strip() or "review",
                "next_operator_move": str(selected_chain.get("next_operator_move") or "inspect chain").strip(),
                "lifecycle_summary": str((selected_chain.get("lifecycle") or {}).get("summary") or "").strip(),
                "action_readiness": action_readiness,
                "quick_action_mode": quick_action_mode,
                "review_recommended_action": str(selected_chain.get("review_recommended_action") or "").strip(),
                "can_generate_post_mortem": bool(selected_chain.get("can_generate_post_mortem")),
                "post_mortem_count": int(selected_chain.get("post_mortem_count") or 0),
                "current_queue_lane": current_queue_lane,
                "current_queue_label": _research_queue_lane_label(current_queue_lane),
                "current_queue_matches_focus": current_queue_lane == str(lane_focus.get("queue_filter") or "all"),
                "current_active_view": current_active_view,
                "current_active_view_label": _research_active_view_label(current_active_view),
                "current_active_view_matches_focus": current_active_view == str(lane_focus.get("active_view") or "all"),
                "suppress_auto_sync": suppress_auto_sync,
                **lane_focus,
                "show_load_button": False,
                "load_button_label": "",
                "secondary_anchor": "#research-artifact-chain-viewer",
                "secondary_label": "Jump To Timeline",
                "error": "",
                "generated_at": utc_now_iso(),
            }
        missing_error = str(selected_chain.get("error") or "").strip()

    summary = build_research_operating_summary_context()
    queue_follow_up = build_research_queue_follow_up_context("all")
    if queue_follow_up and queue_follow_up.get("mode") == "next_item":
        detail = str(queue_follow_up.get("detail") or "").strip() or "The operator queue has an actionable item waiting."
        if missing_error:
            detail = f"{missing_error} Showing the next queued item instead."
        lane_focus = _build_research_lane_focus(str(queue_follow_up.get("lane") or ""))
        priority = str(queue_follow_up.get("priority") or "").strip().lower()
        tone = "warning" if priority in {"urgent", "watch"} or missing_error else "clear"
        return {
            "mode": "recommended",
            "focus_label": "Recommended Focus",
            "chain_id": str(queue_follow_up.get("chain_id") or "").strip(),
            "ticker": str(queue_follow_up.get("title") or "").strip(),
            "engine": "",
            "title": f"Open the next {str(queue_follow_up.get('lane_label') or 'queue').lower()} item",
            "detail": detail,
            "tone": tone,
            "latest_meta_label": "Queue Lane",
            "latest_label": str(queue_follow_up.get("lane_label") or "Queue").strip(),
            "status_label": "Priority",
            "status_value": str(queue_follow_up.get("priority") or "-").strip() or "-",
            "artifact_count": None,
            "activity_label": str(queue_follow_up.get("meta_label") or "Age").strip() or "Age",
            "activity_value": str(queue_follow_up.get("meta_value") or "-").strip() or "-",
            "started_label": str(queue_follow_up.get("status_label") or "Suggested").strip() or "Suggested",
            "started_value": str(queue_follow_up.get("status_value") or "-").strip() or "-",
            "next_lane": str(queue_follow_up.get("lane_label") or queue_follow_up.get("lane") or "review").strip() or "review",
            "next_operator_move": "open suggested chain",
            "lifecycle_summary": "",
            "action_readiness": [],
            "quick_action_mode": "",
            "review_recommended_action": "",
            "can_generate_post_mortem": False,
            "post_mortem_count": 0,
            "current_queue_lane": current_queue_lane,
            "current_queue_label": _research_queue_lane_label(current_queue_lane),
            "current_queue_matches_focus": current_queue_lane == str(lane_focus.get("queue_filter") or "all"),
            "current_active_view": current_active_view,
            "current_active_view_label": _research_active_view_label(current_active_view),
            "current_active_view_matches_focus": current_active_view == str(lane_focus.get("active_view") or "all"),
            "suppress_auto_sync": suppress_auto_sync,
            **lane_focus,
            "show_load_button": bool(queue_follow_up.get("chain_id")),
            "load_button_label": str(queue_follow_up.get("open_label") or "Load Suggested Chain").strip() or "Load Suggested Chain",
            "secondary_anchor": "#research-alerts",
            "secondary_label": "Open Decision Queue",
            "error": missing_error,
            "generated_at": utc_now_iso(),
        }

    latest_chain = summary.get("latest_chain") if isinstance(summary.get("latest_chain"), dict) else None
    if latest_chain:
        next_lane = str(latest_chain.get("stage") or "flow").strip()
        lane_focus = _build_research_lane_focus(next_lane)
        detail = str(summary.get("focus_detail") or "").strip()
        if missing_error:
            detail = f"{missing_error} Showing the latest active chain instead."
        return {
            "mode": "recommended",
            "focus_label": "Recommended Focus",
            "chain_id": str(latest_chain.get("chain_id") or "").strip(),
            "ticker": str(latest_chain.get("ticker") or "").strip(),
            "engine": str(latest_chain.get("engine") or "").strip(),
            "title": "Load the latest active chain",
            "detail": detail or "The latest chain is ready to inspect.",
            "tone": "warning" if missing_error else str(summary.get("focus_tone") or "clear").strip(),
            "latest_meta_label": "Current Stage",
            "latest_label": next_lane or "-",
            "status_label": "Freshness",
            "status_value": str(latest_chain.get("freshness") or "-").strip() or "-",
            "artifact_count": None,
            "activity_label": "Latest Update",
            "activity_value": str(latest_chain.get("updated_label") or "-").strip() or "-",
            "started_label": "Started",
            "started_value": str(latest_chain.get("created_label") or "-").strip() or "-",
            "next_lane": next_lane or "flow",
            "next_operator_move": str(latest_chain.get("next_action") or "open suggested chain").strip(),
            "lifecycle_summary": "",
            "action_readiness": [],
            "quick_action_mode": "",
            "review_recommended_action": "",
            "can_generate_post_mortem": False,
            "post_mortem_count": 0,
            "current_queue_lane": current_queue_lane,
            "current_queue_label": _research_queue_lane_label(current_queue_lane),
            "current_queue_matches_focus": current_queue_lane == str(lane_focus.get("queue_filter") or "all"),
            "current_active_view": current_active_view,
            "current_active_view_label": _research_active_view_label(current_active_view),
            "current_active_view_matches_focus": current_active_view == str(lane_focus.get("active_view") or "all"),
            "suppress_auto_sync": suppress_auto_sync,
            **lane_focus,
            "show_load_button": bool(latest_chain.get("chain_id")),
            "load_button_label": "Load Suggested Chain",
            "secondary_anchor": "#research-loop",
            "secondary_label": "Browse Active Research",
            "error": missing_error,
            "generated_at": utc_now_iso(),
        }

    idle_detail = str(summary.get("focus_detail") or "").strip() or "Open an active chain or send a new intake."
    if missing_error:
        idle_detail = f"{missing_error} Open another active chain or send a new intake."
    return {
        "mode": "idle",
        "focus_label": "Current Focus",
        "chain_id": "",
        "ticker": "",
        "engine": "",
        "title": str(summary.get("focus_title") or "No chain selected").strip() or "No chain selected",
        "detail": idle_detail,
        "tone": "warning" if missing_error else str(summary.get("focus_tone") or "idle").strip(),
        "latest_meta_label": "Surface",
        "latest_label": "Research Workbench",
        "status_label": "State",
        "status_value": "waiting",
        "artifact_count": None,
        "activity_label": "Suggested Lane",
        "activity_value": "flow",
        "started_label": "Next Move",
        "started_value": "open a chain or submit intake",
        "next_lane": "flow",
        "next_operator_move": "open a chain or submit intake",
        "lifecycle_summary": "",
        "action_readiness": [],
        "quick_action_mode": "",
        "review_recommended_action": "",
        "can_generate_post_mortem": False,
        "post_mortem_count": 0,
        "current_queue_lane": current_queue_lane,
        "current_queue_label": _research_queue_lane_label(current_queue_lane),
        "current_queue_matches_focus": current_queue_lane == "all",
        "current_active_view": current_active_view,
        "current_active_view_label": _research_active_view_label(current_active_view),
        "current_active_view_matches_focus": current_active_view == "all",
        "suppress_auto_sync": suppress_auto_sync,
        "queue_filter": "all",
        "queue_label": "Flow Lane",
        "queue_anchor": "#research-loop",
        "active_view": "all",
        "active_view_label": "All",
        "show_load_button": False,
        "load_button_label": "",
        "secondary_anchor": "#research-intake",
        "secondary_label": "Open Intake",
        "error": missing_error,
        "generated_at": utc_now_iso(),
    }


def _build_research_selected_chain_queue_context(
    chain_id: str,
    *,
    artifact_store: ArtifactStore | None = None,
    build_research_artifact_chain_context: Callable[..., dict[str, Any]],
    logger: logging.Logger,
) -> dict[str, Any] | None:
    clean_chain_id = str(chain_id or "").strip()
    if not clean_chain_id:
        return None
    try:
        chain_context = build_research_artifact_chain_context(clean_chain_id, artifact_store=artifact_store)
    except Exception as exc:
        logger.debug("Research selected-chain queue context unavailable for %s: %s", clean_chain_id, exc)
        return {
            "chain_id": clean_chain_id,
            "ticker": "",
            "engine": "",
            "next_lane": "",
            "queue_filter": "all",
            "active_view": "all",
            "operator_posture_title": "Selected chain unavailable",
            "next_operator_move": "reopen the chain or clear focus",
            "latest_label": "",
            "latest_created_label": "",
            "error": str(exc),
        }
    if int(chain_context.get("artifact_count") or 0) <= 0:
        return {
            "chain_id": clean_chain_id,
            "ticker": "",
            "engine": "",
            "next_lane": "",
            "queue_filter": "all",
            "active_view": "all",
            "operator_posture_title": "Selected chain unavailable",
            "next_operator_move": "reopen the chain or clear focus",
            "latest_label": "",
            "latest_created_label": "",
            "error": str(chain_context.get("error") or "").strip(),
        }
    latest = chain_context.get("latest") or {}
    lane_focus = _build_research_lane_focus(str(chain_context.get("next_lane") or ""))
    return {
        "chain_id": clean_chain_id,
        "ticker": str(latest.get("ticker") or "").strip(),
        "engine": str(latest.get("engine") or "").strip(),
        "next_lane": str(chain_context.get("next_lane") or "").strip(),
        "queue_filter": _normalize_research_queue_lane(str(lane_focus.get("queue_filter") or "all")),
        "active_view": _normalize_research_active_view(str(lane_focus.get("active_view") or "all")),
        "operator_posture_title": str(chain_context.get("operator_posture_title") or "").strip(),
        "next_operator_move": str(chain_context.get("next_operator_move") or "").strip(),
        "latest_label": str(latest.get("artifact_label") or "").strip(),
        "latest_created_label": str(chain_context.get("latest_created_label") or "").strip(),
        "error": "",
    }


def _build_research_workbench_queue_alignment(
    chain_context: dict[str, Any] | None,
    selected_queue_lane: str,
    *,
    research_queue_lane_label: Callable[[str], str] = _research_queue_lane_label,
    research_active_view_label: Callable[[str], str] = _research_active_view_label,
) -> dict[str, Any] | None:
    if not isinstance(chain_context, dict) or int(chain_context.get("artifact_count") or 0) <= 0:
        return None

    preferred_focus = _build_research_lane_focus(str(chain_context.get("next_lane") or ""))
    preferred_lane = _normalize_research_queue_lane(str(preferred_focus.get("queue_filter") or "all"))
    preferred_active_view = _normalize_research_active_view(str(preferred_focus.get("active_view") or "all"))
    current_lane = _normalize_research_queue_lane(selected_queue_lane)
    preferred_label = research_queue_lane_label(preferred_lane)
    current_label = research_queue_lane_label(current_lane)

    if current_lane == preferred_lane:
        return {
            "state": "aligned",
            "tone": "positive",
            "title": "Queue aligned with selected chain",
            "detail": f"Queue focus already matches this chain's current lane: {preferred_label}.",
            "current_lane": current_lane,
            "current_label": current_label,
            "preferred_lane": preferred_lane,
            "preferred_label": preferred_label,
            "preferred_active_view": preferred_active_view,
            "preferred_active_view_label": research_active_view_label(preferred_active_view),
            "show_action": False,
            "action_label": "",
        }

    if current_lane == "all":
        return {
            "state": "broad",
            "tone": "neutral",
            "title": "Queue is broader than selected chain",
            "detail": f"This chain is asking for {preferred_label}, but the queue is still showing all lanes.",
            "current_lane": current_lane,
            "current_label": current_label,
            "preferred_lane": preferred_lane,
            "preferred_label": preferred_label,
            "preferred_active_view": preferred_active_view,
            "preferred_active_view_label": research_active_view_label(preferred_active_view),
            "show_action": preferred_lane != "all",
            "action_label": "Focus Queue",
        }

    if preferred_lane == "all":
        return {
            "state": "misaligned",
            "tone": "warning",
            "title": "Queue focus is narrower than selected chain",
            "detail": f"This chain is currently in Flow Lane, but the queue is filtered to {current_label}.",
            "current_lane": current_lane,
            "current_label": current_label,
            "preferred_lane": preferred_lane,
            "preferred_label": preferred_label,
            "preferred_active_view": preferred_active_view,
            "preferred_active_view_label": research_active_view_label(preferred_active_view),
            "show_action": True,
            "action_label": "Show All Lanes",
        }

    return {
        "state": "misaligned",
        "tone": "warning",
        "title": "Queue focus is off-lane",
        "detail": f"This chain is asking for {preferred_label}, but the queue is filtered to {current_label}.",
        "current_lane": current_lane,
        "current_label": current_label,
        "preferred_lane": preferred_lane,
        "preferred_label": preferred_label,
        "preferred_active_view": preferred_active_view,
        "preferred_active_view_label": research_active_view_label(preferred_active_view),
        "show_action": True,
        "action_label": "Sync Queue Focus",
    }


def _build_research_next_queue_item_context(
    selected_queue_lane: str,
    *,
    alerts: dict[str, Any] | None = None,
    exclude_chain_id: str = "",
) -> dict[str, Any] | None:
    normalized_lane = _normalize_research_queue_lane(selected_queue_lane)
    skip_chain_id = str(exclude_chain_id or "").strip()
    alerts = alerts or {}

    lane_order = {
        "review": ["review"],
        "pilot": ["pilot"],
        "rebalance": ["rebalance"],
        "all": ["review", "pilot", "rebalance"],
        "retirements": [],
    }.get(normalized_lane, ["review", "pilot", "rebalance"])

    def candidate_from_review(row: dict[str, Any]) -> dict[str, Any]:
        flags = list(row.get("flags") or [])
        detail = f"Suggested {str(row.get('recommended_action') or 'review').strip() or 'review'}."
        if flags:
            detail = f"{', '.join(str(flag) for flag in flags[:2])}."
        return {
            "lane": "review",
            "lane_label": "Review Lane",
            "active_view": "operator",
            "active_view_label": "Operator",
            "chain_id": str(row.get("chain_id") or "").strip(),
            "title": str(row.get("strategy_id") or row.get("ticker") or "Review item").strip() or "Review item",
            "detail": detail,
            "status_label": "Suggested",
            "status_value": str(row.get("recommended_action") or "review").strip() or "review",
            "meta_label": "Age",
            "meta_value": str(row.get("created_label") or row.get("created_at") or "-").strip() or "-",
            "priority": str(row.get("priority") or "routine").strip() or "routine",
            "open_label": "Open Next Review",
        }

    def candidate_from_pilot(row: dict[str, Any]) -> dict[str, Any]:
        score = row.get("score")
        detail = str(row.get("next_action") or "approve or reject pilot").strip() or "approve or reject pilot"
        if score is not None:
            detail = f"Score {float(score):.1f} · {detail}"
        return {
            "lane": "pilot",
            "lane_label": "Pilot Lane",
            "active_view": "operator",
            "active_view_label": "Operator",
            "chain_id": str(row.get("chain_id") or "").strip(),
            "title": str(row.get("ticker") or "Pilot candidate").strip() or "Pilot candidate",
            "detail": detail,
            "status_label": "Freshness",
            "status_value": str(row.get("freshness") or "-").strip() or "-",
            "meta_label": "Updated",
            "meta_value": str(row.get("updated_label") or row.get("updated_at") or "-").strip() or "-",
            "priority": str(row.get("priority") or "routine").strip() or "routine",
            "open_label": "Open Next Pilot",
        }

    def candidate_from_rebalance(row: dict[str, Any]) -> dict[str, Any]:
        detail = (
            f"{int(row.get('move_count') or 0)} move(s) queued"
            f" · cost {float(row.get('estimated_cost') or 0.0):.4f}"
        )
        return {
            "lane": "rebalance",
            "lane_label": "Rebalance Lane",
            "active_view": "all",
            "active_view_label": "All",
            "chain_id": str(row.get("chain_id") or "").strip(),
            "title": "Engine A rebalance proposal",
            "detail": detail,
            "status_label": "Approval",
            "status_value": str(row.get("approval_status") or "draft").strip() or "draft",
            "meta_label": "Created",
            "meta_value": str(row.get("created_label") or row.get("created_at") or "-").strip() or "-",
            "priority": str(row.get("priority") or "watch").strip() or "watch",
            "open_label": "Open Next Rebalance",
        }

    builders = {
        "review": (alerts.get("pending_reviews") or [], candidate_from_review),
        "pilot": (alerts.get("pending_pilots") or [], candidate_from_pilot),
        "rebalance": (alerts.get("rebalance_items") or [], candidate_from_rebalance),
    }
    for lane in lane_order:
        rows, builder = builders.get(lane, ([], None))
        if builder is None:
            continue
        for row in rows:
            chain_id = str(row.get("chain_id") or "").strip()
            if not chain_id or chain_id == skip_chain_id:
                continue
            candidate = builder(row)
            if candidate.get("chain_id"):
                return candidate
    return None


def _build_research_queue_follow_up_context(
    selected_queue_lane: str,
    *,
    exclude_chain_id: str = "",
    get_research_alerts_context: Callable[[], dict[str, Any]],
) -> dict[str, Any] | None:
    alerts = get_research_alerts_context()
    next_item = _build_research_next_queue_item_context(
        selected_queue_lane,
        alerts=alerts,
        exclude_chain_id=exclude_chain_id,
    )
    if next_item:
        return {"mode": "next_item", **next_item}

    normalized_lane = _normalize_research_queue_lane(selected_queue_lane)
    lane_counts = dict(alerts.get("lane_counts") or {})
    total_pending = int(lane_counts.get("total_pending") or 0)

    if normalized_lane == "all":
        if total_pending <= 0:
            return {
                "mode": "lane_clear",
                "title": "Operator queue clear",
                "detail": "No review, pilot, or rebalance items are waiting right now.",
                "current_lane": "all",
                "current_label": "All Lanes",
                "next_lane": "",
                "next_label": "",
                "next_active_view": "all",
                "next_active_view_label": "All",
                "next_count": 0,
                "primary_action_label": "Open Intake",
                "primary_action_mode": "intake",
            }
        return None

    current_label = _research_queue_lane_label(normalized_lane)
    current_count = {
        "review": int(lane_counts.get("reviews") or 0),
        "pilot": int(lane_counts.get("pilots") or 0),
        "rebalance": int(lane_counts.get("rebalances") or 0),
    }.get(normalized_lane, 0)
    if current_count > 0:
        return None

    for next_lane, count in (
        ("review", int(lane_counts.get("reviews") or 0)),
        ("pilot", int(lane_counts.get("pilots") or 0)),
        ("rebalance", int(lane_counts.get("rebalances") or 0)),
    ):
        if next_lane == normalized_lane or count <= 0:
            continue
        return {
            "mode": "lane_clear",
            "title": f"{current_label} cleared",
            "detail": f"No more items are waiting in {current_label}. {count} item(s) remain in { _research_queue_lane_label(next_lane) }.",
            "current_lane": normalized_lane,
            "current_label": current_label,
            "next_lane": next_lane,
            "next_label": _research_queue_lane_label(next_lane),
            "next_active_view": _research_active_view_for_lane(next_lane),
            "next_active_view_label": _research_active_view_label(_research_active_view_for_lane(next_lane)),
            "next_count": count,
            "primary_action_label": f"Open { _research_queue_lane_label(next_lane) }",
            "primary_action_mode": "lane",
        }

    return {
        "mode": "lane_clear",
        "title": f"{current_label} cleared",
        "detail": f"No more items are waiting in {current_label}. The operator queue is clear for now.",
        "current_lane": normalized_lane,
        "current_label": current_label,
        "next_lane": "",
        "next_label": "",
        "next_active_view": "all",
        "next_active_view_label": "All",
        "next_count": 0,
        "primary_action_label": "Open Intake",
        "primary_action_mode": "intake",
    }


def _build_research_archive_context(
    *,
    limit: int = 6,
    ticker: str = "",
    search_text: str = "",
    view: str = "all",
    artifact_store: ArtifactStore,
    event_store: EventStore,
    serialize_research_synthesis_event: Callable[[dict[str, Any]], dict[str, Any]],
    serialize_research_artifact: Callable[[Any], dict[str, Any]],
    build_research_chain_lifecycle: Callable[[list[Any]], dict[str, Any]],
    extract_research_artifact_note: Callable[[Any], str],
    research_archive_views: set[str] | tuple[str, ...] | list[str],
    research_synthesis_event_type: str,
    utc_now_iso: Callable[[], str],
) -> dict[str, Any]:
    store = artifact_store
    events = event_store
    errors: list[str] = []
    clamped_limit = max(1, min(int(limit), 20))
    scan_limit = max(clamped_limit * 5, 20)
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_search = str(search_text or "").strip()
    normalized_view = str(view or "all").strip().lower()
    if normalized_view not in research_archive_views:
        normalized_view = "all"

    try:
        synthesis_events = [
            serialize_research_synthesis_event(row)
            for row in events.list_events(limit=scan_limit, event_type=research_synthesis_event_type)
        ]
    except Exception as exc:
        synthesis_events = []
        errors.append(f"synthesis history unavailable: {exc}")

    try:
        post_mortems = [
            serialize_research_artifact(envelope)
            for envelope in store.query(
                artifact_type=ArtifactType.POST_MORTEM_NOTE,
                engine=Engine.ENGINE_B,
                ticker=normalized_ticker or None,
                limit=scan_limit,
            )
        ]
    except Exception as exc:
        post_mortems = []
        errors.append(f"post-mortems unavailable: {exc}")

    try:
        retirements = [
            serialize_research_artifact(envelope)
            for envelope in store.query(
                artifact_type=ArtifactType.RETIREMENT_MEMO,
                engine=Engine.ENGINE_B,
                ticker=normalized_ticker or None,
                limit=scan_limit,
            )
        ]
    except Exception as exc:
        retirements = []
        errors.append(f"retirements unavailable: {exc}")

    def _matches_filters(row: dict[str, Any]) -> bool:
        row_ticker = str(row.get("ticker") or "").strip().upper()
        if normalized_ticker and normalized_ticker not in row_ticker:
            return False
        if normalized_search:
            haystack = json.dumps(row, sort_keys=True, default=str).lower()
            if normalized_search.lower() not in haystack:
                return False
        return True

    synthesis_events = [row for row in synthesis_events if _matches_filters(row)][:clamped_limit]
    post_mortems = [row for row in post_mortems if _matches_filters(row)][:clamped_limit]
    retirements = [row for row in retirements if _matches_filters(row)][:clamped_limit]

    completed_index: dict[str, dict[str, Any]] = {}

    def _touch_completed_chain(
        chain_id: str,
        *,
        ticker_value: str = "",
        created_at: str = "",
        note: str = "",
        artifact_label: str = "",
        final_status: str = "",
        retirement_trigger: str = "",
        synthesis_hit: bool = False,
        post_mortem_hit: bool = False,
        retirement_hit: bool = False,
    ) -> None:
        if not chain_id:
            return
        entry = completed_index.setdefault(
            chain_id,
            {
                "chain_id": chain_id,
                "ticker": ticker_value or "-",
                "artifact_count": 0,
                "latest_artifact_label": artifact_label or "-",
                "latest_artifact_status": "",
                "last_activity_at": created_at or "",
                "latest_note": note or "",
                "final_status": final_status or "",
                "retirement_trigger": retirement_trigger or "",
                "synthesis_count": 0,
                "post_mortem_count": 0,
                "retirement_count": 0,
                "lifecycle": {
                    "milestones": [],
                    "completed_count": 0,
                    "total_count": 11,
                    "summary": "",
                },
            },
        )
        if ticker_value and (entry["ticker"] in {"", "-"}):
            entry["ticker"] = ticker_value
        if synthesis_hit:
            entry["synthesis_count"] += 1
        if post_mortem_hit:
            entry["post_mortem_count"] += 1
        if retirement_hit:
            entry["retirement_count"] += 1
        if final_status:
            entry["final_status"] = final_status
        if retirement_trigger:
            entry["retirement_trigger"] = retirement_trigger
        if created_at and created_at >= str(entry.get("last_activity_at") or ""):
            entry["last_activity_at"] = created_at
            if artifact_label:
                entry["latest_artifact_label"] = artifact_label
            if note:
                entry["latest_note"] = note

    for row in synthesis_events:
        _touch_completed_chain(
            str(row.get("chain_id") or ""),
            ticker_value=str(row.get("ticker") or ""),
            created_at=str(row.get("created_at") or ""),
            note=str(row.get("summary") or ""),
            artifact_label="Research Synthesis",
            synthesis_hit=True,
        )
    for row in post_mortems:
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        _touch_completed_chain(
            str(row.get("chain_id") or ""),
            ticker_value=str(row.get("ticker") or ""),
            created_at=str(row.get("created_at") or ""),
            note=str(body.get("thesis_assessment") or ""),
            artifact_label=str(row.get("artifact_label") or "Post Mortem Note"),
            post_mortem_hit=True,
        )
    for row in retirements:
        body = row.get("body") if isinstance(row.get("body"), dict) else {}
        _touch_completed_chain(
            str(row.get("chain_id") or ""),
            ticker_value=str(row.get("ticker") or ""),
            created_at=str(row.get("created_at") or ""),
            note=str(body.get("trigger_detail") or ""),
            artifact_label=str(row.get("artifact_label") or "Retirement Memo"),
            final_status=str(body.get("final_status") or ""),
            retirement_trigger=str(body.get("trigger") or ""),
            retirement_hit=True,
        )

    completed_chains: list[dict[str, Any]] = []
    for chain_id, entry in completed_index.items():
        try:
            chain = store.get_chain(chain_id)
        except Exception:
            chain = []
        if chain:
            latest = chain[-1]
            entry["artifact_count"] = len(chain)
            lifecycle = build_research_chain_lifecycle(chain)
            entry["lifecycle"] = lifecycle
            if isinstance(lifecycle, dict):
                entry["lifecycle"]["total_count"] = int(lifecycle.get("total_count") or 0)
            entry["latest_artifact_label"] = latest.artifact_type.value.replace("_", " ").title()
            entry["latest_artifact_status"] = latest.status.value
            latest_created_at = str(latest.created_at or "")
            latest_note = extract_research_artifact_note(latest)
            if latest_created_at and latest_created_at >= str(entry.get("last_activity_at") or ""):
                entry["last_activity_at"] = latest_created_at
                if latest_note:
                    entry["latest_note"] = latest_note
            elif not entry["latest_note"] and latest_note:
                entry["latest_note"] = latest_note
        else:
            entry["artifact_count"] = max(
                int(entry["post_mortem_count"]) + int(entry["retirement_count"]),
                int(entry["artifact_count"]),
            )
        completed_chains.append(entry)

    completed_chains.sort(
        key=lambda row: (str(row.get("last_activity_at") or ""), str(row.get("chain_id") or "")),
        reverse=True,
    )
    completed_chains = completed_chains[:clamped_limit]

    return {
        "filters": {
            "limit": clamped_limit,
            "ticker": normalized_ticker,
            "q": normalized_search,
            "view": normalized_view,
        },
        "completed_chains": completed_chains,
        "synthesis_events": synthesis_events,
        "post_mortems": post_mortems,
        "retirements": retirements,
        "show_completed_chains": normalized_view in {"all", "completed"},
        "show_syntheses": normalized_view in {"all", "synthesis"},
        "show_post_mortems": normalized_view in {"all", "post_mortem"},
        "show_retirements": normalized_view in {"all", "retirement"},
        "error": "; ".join(errors),
        "generated_at": utc_now_iso(),
    }
