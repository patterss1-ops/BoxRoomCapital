"""Tests for the Idea Pipeline — trade idea lifecycle management."""
import json
import os
import uuid

import pytest


_test_db = ""


@pytest.fixture(autouse=True)
def _setup_db(monkeypatch, tmp_path):
    """Initialise a fresh test DB per test using pytest's tmp_path."""
    global _test_db
    from data import trade_db
    db_path = str(tmp_path / "test_idea_pipeline.db")
    _test_db = db_path
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db(db_path)


# ─── Schema tests ────────────────────────────────────────────────────────────


def test_trade_ideas_table_created():
    from data.trade_db import get_conn
    conn = get_conn(_test_db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "trade_ideas" in tables
    assert "idea_transitions" in tables


# ─── CRUD tests ──────────────────────────────────────────────────────────────


def test_create_and_get_trade_idea():
    from data.trade_db import create_trade_idea, get_trade_idea
    idea_id = str(uuid.uuid4())
    create_trade_idea(
        idea_id=idea_id, analysis_id="a1", ticker="SPY", direction="long",
        conviction="high", timeframe="weeks", thesis="SPY will go up",
        confidence=0.75, db_path=_test_db,
    )
    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea is not None
    assert idea["ticker"] == "SPY"
    assert idea["direction"] == "long"
    assert idea["conviction"] == "high"
    assert idea["confidence"] == 0.75
    assert idea["pipeline_stage"] == "idea"


def test_update_trade_idea():
    from data.trade_db import create_trade_idea, update_trade_idea, get_trade_idea
    idea_id = str(uuid.uuid4())
    create_trade_idea(idea_id=idea_id, analysis_id="a2", ticker="QQQ",
                      direction="short", db_path=_test_db)
    update_trade_idea(idea_id, db_path=_test_db, pipeline_stage="review", user_notes="looks good")
    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea["pipeline_stage"] == "review"
    assert idea["user_notes"] == "looks good"


def test_get_trade_ideas_filter_by_stage():
    from data.trade_db import create_trade_idea, get_trade_ideas
    for i, stage in enumerate(["idea", "idea", "review", "backtest"]):
        create_trade_idea(
            idea_id=str(uuid.uuid4()), analysis_id=f"a{i}", ticker="SPY",
            direction="long", pipeline_stage=stage, db_path=_test_db,
        )
    ideas = get_trade_ideas(stage="idea", db_path=_test_db)
    assert len(ideas) == 2
    review = get_trade_ideas(stage="review", db_path=_test_db)
    assert len(review) == 1


def test_get_trade_ideas_filter_by_ticker():
    from data.trade_db import create_trade_idea, get_trade_ideas
    create_trade_idea(idea_id=str(uuid.uuid4()), analysis_id="a1",
                      ticker="SPY", direction="long", db_path=_test_db)
    create_trade_idea(idea_id=str(uuid.uuid4()), analysis_id="a2",
                      ticker="QQQ", direction="long", db_path=_test_db)
    spy = get_trade_ideas(ticker="spy", db_path=_test_db)  # lowercase should work
    assert len(spy) == 1
    assert spy[0]["ticker"] == "SPY"


def test_get_trade_ideas_by_analysis():
    from data.trade_db import create_trade_idea, get_trade_ideas_by_analysis
    aid = "analysis_123"
    create_trade_idea(idea_id=str(uuid.uuid4()), analysis_id=aid,
                      ticker="SPY", direction="long", db_path=_test_db)
    create_trade_idea(idea_id=str(uuid.uuid4()), analysis_id=aid,
                      ticker="QQQ", direction="short", db_path=_test_db)
    create_trade_idea(idea_id=str(uuid.uuid4()), analysis_id="other",
                      ticker="IWM", direction="long", db_path=_test_db)
    ideas = get_trade_ideas_by_analysis(aid, db_path=_test_db)
    assert len(ideas) == 2


def test_record_and_get_transitions():
    from data.trade_db import record_idea_transition, get_idea_transitions
    idea_id = str(uuid.uuid4())
    record_idea_transition(idea_id, "idea", "review", actor="operator",
                           reason="looks promising", db_path=_test_db)
    record_idea_transition(idea_id, "review", "backtest", actor="system",
                           db_path=_test_db)
    transitions = get_idea_transitions(idea_id, db_path=_test_db)
    assert len(transitions) == 2
    assert transitions[0]["from_stage"] == "idea"
    assert transitions[1]["to_stage"] == "backtest"


# ─── Pipeline Manager: transition validation ─────────────────────────────────


def _make_idea(stage="idea", ticker="SPY", direction="long", thesis="test thesis",
               confidence=0.5, **kwargs):
    from data.trade_db import create_trade_idea
    idea_id = str(uuid.uuid4())
    create_trade_idea(
        idea_id=idea_id, analysis_id=str(uuid.uuid4()), ticker=ticker,
        direction=direction, thesis=thesis, confidence=confidence,
        pipeline_stage=stage, db_path=_test_db, **kwargs,
    )
    return idea_id


def test_validate_idea_to_review():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("idea")
    gate = mgr.validate_transition(idea_id, "review")
    assert gate.allowed


def test_validate_idea_to_backtest_blocked():
    """Can't skip review."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("idea")
    gate = mgr.validate_transition(idea_id, "backtest")
    assert not gate.allowed
    assert "INVALID_TRANSITION" in gate.reasons


def test_validate_review_to_backtest_needs_thesis():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("review", thesis=None, confidence=0.5)
    gate = mgr.validate_transition(idea_id, "backtest")
    assert not gate.allowed
    assert "MISSING_THESIS" in gate.reasons


def test_validate_review_to_backtest_needs_confidence():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("review", confidence=0.1)
    gate = mgr.validate_transition(idea_id, "backtest")
    assert not gate.allowed
    assert "LOW_CONFIDENCE" in gate.reasons


def test_validate_review_to_backtest_passes():
    import config as cfg
    original = cfg.IDEA_RESEARCH_AUTO
    cfg.IDEA_RESEARCH_AUTO = False  # Skip research requirement for this test
    try:
        from intelligence.idea_pipeline import IdeaPipelineManager
        mgr = IdeaPipelineManager(db_path=_test_db)
        idea_id = _make_idea("review", thesis="good thesis", confidence=0.5)
        gate = mgr.validate_transition(idea_id, "backtest")
        assert gate.allowed
    finally:
        cfg.IDEA_RESEARCH_AUTO = original


def test_validate_backtest_to_paper_needs_backtest():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("backtest")
    gate = mgr.validate_transition(idea_id, "paper")
    assert not gate.allowed
    assert "BACKTEST_NOT_RUN" in gate.reasons


def test_validate_backtest_to_paper_with_completed_job():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import create_job, update_job, update_trade_idea
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("backtest")
    job_id = f"bt_{uuid.uuid4().hex[:12]}"
    create_job(job_id, "idea_backtest", "completed", db_path=_test_db)
    update_trade_idea(idea_id, db_path=_test_db, backtest_job_id=job_id,
                      backtest_result_json=json.dumps({"sharpe": 0.5, "profit_factor": 1.2}))
    gate = mgr.validate_transition(idea_id, "paper")
    assert gate.allowed


def test_validate_paper_to_live_needs_soak():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import update_trade_idea
    from datetime import datetime, timezone
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("paper")
    # Paper just started (now)
    update_trade_idea(idea_id, db_path=_test_db,
                      paper_deal_id="test_deal",
                      paper_entry_time=datetime.now(timezone.utc).isoformat())
    gate = mgr.validate_transition(idea_id, "live")
    assert not gate.allowed
    assert "PAPER_SOAK_NOT_MET" in gate.reasons


def test_validate_reject_always_allowed():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    for stage in ["idea", "review", "backtest", "paper", "live"]:
        idea_id = _make_idea(stage)
        gate = mgr.validate_transition(idea_id, "rejected")
        assert gate.allowed, f"Reject from {stage} should be allowed"


def test_validate_resurrect_from_rejected():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("rejected")
    gate = mgr.validate_transition(idea_id, "idea")
    assert gate.allowed


def test_validate_nonexistent_idea():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    gate = mgr.validate_transition("nonexistent_id", "review")
    assert not gate.allowed
    assert "IDEA_NOT_FOUND" in gate.reasons


# ─── Pipeline Manager: promotions ────────────────────────────────────────────


def test_promote_idea_to_review():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import get_trade_idea, get_idea_transitions
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("idea")
    result = mgr.promote_idea(idea_id, "review", reason="looks good")
    assert result["success"]
    assert result["from_stage"] == "idea"
    assert result["to_stage"] == "review"
    # Check DB updated
    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea["pipeline_stage"] == "review"
    # Check audit trail
    transitions = get_idea_transitions(idea_id, db_path=_test_db)
    assert len(transitions) == 1
    assert transitions[0]["reason"] == "looks good"


def test_promote_blocked_returns_reasons():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("idea")
    result = mgr.promote_idea(idea_id, "backtest")  # can't skip review
    assert not result["success"]
    assert "INVALID_TRANSITION" in result["reasons"]


def test_reject_idea():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import get_trade_idea
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("review")
    result = mgr.reject_idea(idea_id, reason="weak thesis")
    assert result["success"]
    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea["pipeline_stage"] == "rejected"
    assert idea["rejection_reason"] == "weak thesis"


def test_reject_already_rejected():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("rejected")
    result = mgr.reject_idea(idea_id, reason="again")
    assert not result["success"]


# ─── Pipeline Manager: backtest trigger ──────────────────────────────────────


def test_trigger_backtest_creates_job():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import get_trade_idea, get_job
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("backtest")
    result = mgr.trigger_backtest(idea_id)
    assert result["success"]
    assert result["job_id"]
    # Check job created
    job = get_job(result["job_id"], db_path=_test_db)
    assert job is not None
    assert job["job_type"] == "idea_backtest"
    # Check idea linked
    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea["backtest_job_id"] == result["job_id"]


def test_trigger_backtest_no_ticker():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import create_trade_idea
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = str(uuid.uuid4())
    create_trade_idea(idea_id=idea_id, analysis_id="a1", ticker="",
                      direction="long", db_path=_test_db)
    result = mgr.trigger_backtest(idea_id)
    assert not result["success"]
    assert "MISSING_TICKER" in result["reasons"]


# ─── Pipeline Manager: pipeline stats ────────────────────────────────────────


def test_pipeline_stats():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    _make_idea("idea", ticker="SPY")
    _make_idea("idea", ticker="QQQ")
    _make_idea("review", ticker="SPY")
    _make_idea("backtest", ticker="IWM")
    stats = mgr.get_pipeline_stats()
    assert stats["stages"]["idea"]["count"] == 2
    assert stats["stages"]["review"]["count"] == 1
    assert stats["stages"]["backtest"]["count"] == 1
    assert stats["total"] == 4
    assert stats["unique_tickers"] == 3


# ─── Full lifecycle test ─────────────────────────────────────────────────────


def test_full_lifecycle_idea_to_review_to_reject_to_resurrect():
    from intelligence.idea_pipeline import IdeaPipelineManager
    from data.trade_db import get_trade_idea, get_idea_transitions
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("idea", thesis="good thesis", confidence=0.6)

    # Promote to review
    r1 = mgr.promote_idea(idea_id, "review")
    assert r1["success"]

    # Reject from review
    r2 = mgr.reject_idea(idea_id, reason="changed mind")
    assert r2["success"]

    # Resurrect
    r3 = mgr.promote_idea(idea_id, "idea", reason="second look")
    assert r3["success"]

    idea = get_trade_idea(idea_id, db_path=_test_db)
    assert idea["pipeline_stage"] == "idea"

    transitions = get_idea_transitions(idea_id, db_path=_test_db)
    assert len(transitions) == 3
    assert transitions[0]["to_stage"] == "review"
    assert transitions[1]["to_stage"] == "rejected"
    assert transitions[2]["to_stage"] == "idea"


# ─── Allowed transitions completeness ────────────────────────────────────────


def test_no_skip_stages():
    """Verify you can't jump from idea directly to backtest, paper, or live."""
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    for target in ["backtest", "paper", "live"]:
        idea_id = _make_idea("idea")
        gate = mgr.validate_transition(idea_id, target)
        assert not gate.allowed, f"Should not allow idea -> {target}"


def test_demote_from_review():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("review")
    gate = mgr.validate_transition(idea_id, "idea")
    assert gate.allowed


def test_demote_from_backtest():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("backtest")
    gate = mgr.validate_transition(idea_id, "review")
    assert gate.allowed


def test_demote_from_paper():
    from intelligence.idea_pipeline import IdeaPipelineManager
    mgr = IdeaPipelineManager(db_path=_test_db)
    idea_id = _make_idea("paper")
    gate = mgr.validate_transition(idea_id, "review")
    assert gate.allowed
