"""Research service for options discovery and calibration jobs."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from data.trade_db import upsert_option_contracts

logger = logging.getLogger(__name__)


class ResearchService:
    """Runs IG discovery/calibration workflows and normalizes persisted outputs."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.discovery_output = self.project_root / "options_discovery.json"
        self.calibration_output = self.project_root / "calibration.json"

    def run_discovery(
        self,
        search_only: bool = False,
        nav_only: bool = False,
        details: bool = True,
        strikes: str = "",
        details_limit: int = 200,
    ) -> dict[str, Any]:
        import discover_options as discover

        s = self._login_with_retry(discover.login, label="options discovery")
        if not s:
            return {
                "ok": False,
                "message": "IG login failed for options discovery after retries.",
            }

        results: dict[str, Any] = {
            "discovery_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "search_results": {},
            "navigation_results": {},
            "option_details": [],
        }

        if not nav_only:
            results["search_results"] = discover.find_options_via_search(s)
        if not search_only:
            results["navigation_results"] = discover.find_options_via_navigation(s)

        if details or strikes:
            all_epics: set[str] = set()
            for source in (results["search_results"], results["navigation_results"]):
                for index_name, opts in source.items():
                    if strikes and strikes.lower() not in str(index_name).lower():
                        continue
                    for opt in opts:
                        epic = str(opt.get("epic", "")).strip()
                        if epic:
                            all_epics.add(epic)
            if all_epics:
                max_detail = min(len(all_epics), max(1, details_limit))
                results["option_details"] = discover.get_option_details(
                    s, list(all_epics), max_detail=max_detail
                )

        contracts = self._normalize_contracts(
            search_results=results["search_results"],
            navigation_results=results["navigation_results"],
            option_details=results["option_details"],
        )
        persisted = upsert_option_contracts(contracts)

        self.discovery_output.write_text(
            json.dumps(results, indent=2, default=str),
            encoding="utf-8",
        )
        message = f"Discovery complete. Persisted {persisted} contracts."
        logger.info(message)
        return {
            "ok": True,
            "message": message,
            "contracts_persisted": persisted,
            "search_count": sum(len(v) for v in results["search_results"].values()),
            "navigation_count": sum(len(v) for v in results["navigation_results"].values()),
            "details_count": len(results["option_details"]),
            "output_file": str(self.discovery_output),
        }

    def run_calibration(self, index_filter: str = "", verbose: bool = False) -> dict[str, Any]:
        import calibrate_bs_vs_ig as calibration

        s = self._login_with_retry(calibration.login, label="calibration")
        if not s:
            return {
                "ok": False,
                "message": "IG login failed for calibration after retries.",
            }

        markets = calibration.MARKETS
        if index_filter:
            markets = {
                name: cfg
                for name, cfg in calibration.MARKETS.items()
                if index_filter.lower() in name.lower()
            }
            if not markets:
                return {"ok": False, "message": f"No market matching '{index_filter}'."}

        all_results: list[dict] = []
        for market_name, market_cfg in markets.items():
            rows = calibration.calibrate_market(s, market_name, market_cfg, verbose=verbose)
            if rows:
                all_results.extend(rows)

        if not all_results:
            return {"ok": False, "message": "No calibration data collected. Markets may be closed."}

        summary = calibration.summarise_calibration(all_results)
        payload = {
            "calibration_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_samples": len(all_results),
            "per_market": summary,
            "raw_quotes": all_results,
        }
        self.calibration_output.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "message": "Calibration complete.",
            "samples": len(all_results),
            "summary": summary,
            "raw_quotes": all_results,
            "output_file": str(self.calibration_output),
        }

    def _normalize_contracts(
        self,
        search_results: dict[str, list[dict]],
        navigation_results: dict[str, list[dict]],
        option_details: list[dict],
    ) -> list[dict]:
        by_epic: dict[str, dict[str, Any]] = {}

        def add_base(opts_by_index: dict[str, list[dict]], source: str):
            for index_name, opts in opts_by_index.items():
                for opt in opts:
                    epic = str(opt.get("epic", "")).strip()
                    if not epic:
                        continue
                    row = by_epic.setdefault(epic, {"epic": epic, "sources": set()})
                    row["sources"].add(source)
                    row["index_name"] = row.get("index_name") or index_name
                    row["instrument_name"] = row.get("instrument_name") or opt.get("name")
                    row["instrument_type"] = row.get("instrument_type") or opt.get("type")
                    row["expiry"] = row.get("expiry") or opt.get("expiry")

        add_base(search_results or {}, "search")
        add_base(navigation_results or {}, "navigation")

        details_by_epic = {
            str(d.get("epic", "")).strip(): d
            for d in (option_details or [])
            if str(d.get("epic", "")).strip()
        }

        contracts: list[dict] = []
        for epic, row in by_epic.items():
            detail = details_by_epic.get(epic, {})
            instrument_name = (
                detail.get("name")
                or row.get("instrument_name")
                or ""
            )
            instrument_type = detail.get("type") or row.get("instrument_type") or ""
            expiry = detail.get("expiry") or row.get("expiry") or ""
            bid = detail.get("bid")
            offer = detail.get("offer")
            mid = detail.get("mid")
            if mid is None and bid is not None and offer is not None:
                try:
                    mid = (float(bid) + float(offer)) / 2.0
                except Exception:
                    mid = None
            spread = None
            if bid is not None and offer is not None:
                try:
                    spread = float(offer) - float(bid)
                except Exception:
                    spread = None

            contracts.append(
                {
                    "index_name": row.get("index_name"),
                    "epic": epic,
                    "instrument_name": instrument_name,
                    "option_type": self._infer_option_type(epic, instrument_name),
                    "expiry_type": self._infer_expiry_type(epic, expiry, instrument_name),
                    "expiry": expiry,
                    "strike": self._infer_strike(epic, instrument_name),
                    "status": detail.get("status"),
                    "bid": bid,
                    "offer": offer,
                    "mid": mid,
                    "spread": spread,
                    "min_deal_size": detail.get("min_deal_size"),
                    "margin_factor": detail.get("margin_factor"),
                    "margin_factor_unit": detail.get("margin_factor_unit"),
                    "source": ",".join(sorted(row.get("sources", set()))),
                    "raw_payload": json.dumps(
                        {"base": row, "detail": detail},
                        default=str,
                    ),
                }
            )
        return contracts

    def _infer_option_type(self, epic: str, name: str) -> str:
        epic_u = epic.upper()
        name_l = (name or "").lower()
        if epic_u.endswith("P.IP") or " put" in name_l:
            return "PUT"
        if epic_u.endswith("C.IP") or " call" in name_l:
            return "CALL"
        return "UNKNOWN"

    def _infer_expiry_type(self, epic: str, expiry: str, name: str) -> str:
        epic_u = epic.upper()
        expiry_u = (expiry or "").upper()
        name_l = (name or "").lower()
        if epic_u.startswith("DO.D.D") or "DAILY" in expiry_u or "daily" in name_l:
            return "daily"
        if any(tag in epic_u for tag in ("WEEK", "MON.", "WED.")) or "weekly" in name_l:
            return "weekly"
        if any(tag in epic_u for tag in ("EOM", "EMO")) or "monthly" in name_l:
            return "monthly"
        if epic_u.startswith("OP.D."):
            return "monthly"
        return "unknown"

    def _infer_strike(self, epic: str, name: str) -> float | None:
        m = re.search(r"\.(\d+)(?:[CP])\.IP$", epic.upper())
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass

        for token in str(name or "").replace(",", " ").split():
            try:
                value = float(token)
            except ValueError:
                continue
            if value >= 100:
                return value
        return None

    def _login_with_retry(self, login_fn, label: str, attempts: int = 3, delay_s: float = 1.5):
        """Best-effort login retry for transient IG auth/network failures."""
        for attempt in range(1, attempts + 1):
            session = login_fn()
            if session:
                return session
            if attempt < attempts:
                logger.warning(
                    "IG login failed for %s (attempt %s/%s); retrying in %.1fs",
                    label,
                    attempt,
                    attempts,
                    delay_s,
                )
                time.sleep(delay_s)
        return None
