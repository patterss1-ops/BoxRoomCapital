"""Ledger API routes for unified broker/account snapshots."""

from __future__ import annotations

from fastapi import APIRouter

from data.trade_db import get_ledger_reconcile_report, get_unified_ledger_snapshot

router = APIRouter(prefix="/api/ledger", tags=["ledger"])


@router.get("/snapshot")
def ledger_snapshot(nav_limit: int = 50):
    return get_unified_ledger_snapshot(nav_limit=nav_limit)


@router.get("/reconcile")
def ledger_reconcile(stale_after_minutes: int = 30):
    return get_ledger_reconcile_report(stale_after_minutes=stale_after_minutes)
