"""Control-plane service for in-process options engine lifecycle."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from app.engine.options_engine import OptionsEngine


class BotControlService:
    """Facade used by API routes to control the in-process options engine."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.runtime_dir = self.project_root / ".runtime"
        self.runtime_dir.mkdir(exist_ok=True)
        self.state_file = self.runtime_dir / "options_engine_state.json"
        self.process_log = self.project_root / config.LOG_FILE
        self.engine = OptionsEngine()

    def _write_state(self, data: dict[str, Any]):
        payload = {
            "updated_at": datetime.now().isoformat(),
            **data,
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def status(self) -> dict[str, Any]:
        engine_state = self.engine.status()
        persisted = {}
        if self.state_file.exists():
            try:
                persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                persisted = {}

        return {
            **engine_state,
            "log_file": str(self.process_log),
            "last_action": persisted.get("last_action"),
            "last_action_at": persisted.get("updated_at"),
        }

    def start(self, mode: str) -> dict[str, Any]:
        result = self.engine.start(mode=mode)
        self._write_state({"last_action": f"start:{mode}"})
        return result

    def stop(self) -> dict[str, Any]:
        result = self.engine.stop()
        self._write_state({"last_action": "stop"})
        return result

    def pause(self) -> dict[str, Any]:
        result = self.engine.pause()
        self._write_state({"last_action": "pause"})
        return result

    def resume(self) -> dict[str, Any]:
        result = self.engine.resume()
        self._write_state({"last_action": "resume"})
        return result

    def scan_once(self, mode: str) -> dict[str, Any]:
        result = self.engine.scan_now(mode=mode)
        self._write_state({"last_action": f"scan:{mode}"})
        return result

    def reconcile(self) -> dict[str, Any]:
        result = self.engine.reconcile()
        self._write_state({"last_action": "reconcile"})
        return result

    def reconcile_report(self) -> dict[str, Any]:
        result = self.engine.reconcile_report()
        self._write_state({"last_action": "reconcile-report"})
        return result

    def close_spread(self, spread_id: str = "", ticker: str = "", reason: str = "Manual close") -> dict[str, Any]:
        result = self.engine.close_spread(spread_id=spread_id, ticker=ticker, reason=reason)
        target = spread_id or ticker or "unknown"
        self._write_state({"last_action": f"close:{target}"})
        return result

    def set_kill_switch(self, active: bool, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_kill_switch(active=active, reason=reason, actor=actor)
        action = "kill-on" if active else "kill-off"
        self._write_state({"last_action": f"{action}"})
        return result

    def set_risk_throttle(self, pct: float, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_risk_throttle(pct=pct, reason=reason, actor=actor)
        self._write_state({"last_action": f"risk-throttle:{pct}"})
        return result

    def set_market_cooldown(self, ticker: str, minutes: int, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.set_market_cooldown(ticker=ticker, minutes=minutes, reason=reason, actor=actor)
        self._write_state({"last_action": f"cooldown-set:{ticker}:{minutes}"})
        return result

    def clear_market_cooldown(self, ticker: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        result = self.engine.clear_market_cooldown(ticker=ticker, reason=reason, actor=actor)
        self._write_state({"last_action": f"cooldown-clear:{ticker}"})
        return result
