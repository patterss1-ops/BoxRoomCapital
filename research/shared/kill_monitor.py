"""Kill-criteria tracking and retirement memo generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    PerformanceMetrics,
    RetirementMemo,
)


@dataclass(frozen=True)
class KillCriterion:
    trigger: str
    threshold: float | None = None
    description: str = ""
    auto_approve: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KillAlert:
    hypothesis_id: str
    trigger: str
    trigger_detail: str
    auto_kill: bool


class KillMonitor:
    """Track kill criteria and emit retirement artifacts when triggered."""

    KILL_TRIGGERS = {
        "invalidation",
        "decay",
        "drawdown",
        "operator_decision",
        "regime_change",
        "cost_exceeded",
        "data_breach",
    }

    def __init__(
        self,
        artifact_store: ArtifactStore,
        state_provider: Callable[[str, str], dict[str, Any]] | None = None,
        pipeline_state_updater: Callable[[str, str], None] | None = None,
        notifier: Callable[[str, str, str], None] | None = None,
    ):
        self._artifact_store = artifact_store
        self._state_provider = state_provider or (lambda hypothesis_id, as_of: {})
        self._pipeline_state_updater = pipeline_state_updater or (lambda chain_id, stage: None)
        self._notifier = notifier or (lambda hypothesis_id, trigger, detail: None)
        self._criteria: dict[str, list[KillCriterion]] = {}

    def register_kill_criteria(self, hypothesis_id: str, criteria: list[KillCriterion]) -> None:
        for criterion in criteria:
            if criterion.trigger not in self.KILL_TRIGGERS:
                raise ValueError(f"Unsupported kill trigger '{criterion.trigger}'")
        self._criteria[hypothesis_id] = list(criteria)

    def check_all(self, as_of: str) -> list[KillAlert]:
        alerts: list[KillAlert] = []
        for hypothesis_id, criteria in self._criteria.items():
            state = self._state_provider(hypothesis_id, as_of)
            for criterion in criteria:
                detail = self._check_criterion(criterion, state)
                if detail is not None:
                    alerts.append(
                        KillAlert(
                            hypothesis_id=hypothesis_id,
                            trigger=criterion.trigger,
                            trigger_detail=detail,
                            auto_kill=criterion.auto_approve,
                        )
                    )
        return alerts

    def execute_kill(
        self,
        hypothesis_id: str,
        trigger: str,
        trigger_detail: str,
        operator_approved: bool,
        performance_summary: PerformanceMetrics | dict[str, Any] | None = None,
        live_duration_days: int | None = None,
    ) -> ArtifactEnvelope:
        if trigger not in self.KILL_TRIGGERS:
            raise ValueError(f"Unsupported kill trigger '{trigger}'")

        criteria = self._criteria.get(hypothesis_id, [])
        auto_allowed = any(criterion.trigger == trigger and criterion.auto_approve for criterion in criteria)
        if not operator_approved and not auto_allowed:
            raise PermissionError("Kill execution requires operator approval for this trigger")

        hypothesis = self._artifact_store.get(hypothesis_id)
        if hypothesis is None:
            raise ValueError(f"Hypothesis '{hypothesis_id}' not found")

        performance_model = None
        if performance_summary is not None:
            performance_model = (
                performance_summary
                if isinstance(performance_summary, PerformanceMetrics)
                else PerformanceMetrics.model_validate(performance_summary)
            )
        memo = RetirementMemo(
            hypothesis_ref=hypothesis_id,
            trigger=trigger,
            trigger_detail=trigger_detail,
            diagnosis=self._diagnosis_for(trigger, trigger_detail),
            lessons=self._lessons_for(trigger),
            final_status="dead" if operator_approved or auto_allowed else "parked",
            performance_summary=performance_model,
            live_duration_days=live_duration_days,
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.RETIREMENT_MEMO,
            engine=Engine.ENGINE_B,
            ticker=hypothesis.ticker,
            edge_family=hypothesis.edge_family,
            chain_id=hypothesis.chain_id,
            body=memo,
            created_by="system",
            tags=["retirement"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        self._pipeline_state_updater(hypothesis.chain_id or envelope.chain_id or "", "retired")
        self._notifier(hypothesis_id, trigger, trigger_detail)
        return envelope

    @staticmethod
    def _check_criterion(criterion: KillCriterion, state: dict[str, Any]) -> str | None:
        if criterion.trigger == "drawdown":
            drawdown = float(state.get("max_drawdown_pct", 0.0))
            if criterion.threshold is not None and drawdown >= criterion.threshold:
                return f"max_drawdown_pct={drawdown:.2f} exceeded threshold={criterion.threshold:.2f}"
        elif criterion.trigger == "decay":
            status = state.get("health_status")
            if status == "decay":
                return "strategy health status is decay"
        elif criterion.trigger == "invalidation":
            if state.get("invalidated"):
                return "declared invalidation condition met"
        elif criterion.trigger == "regime_change":
            blocked = set(criterion.metadata.get("blocked_regimes", []))
            current = state.get("current_regime")
            if current in blocked:
                return f"current_regime={current} is blocked"
        elif criterion.trigger == "cost_exceeded":
            cost_multiple = float(state.get("cost_multiple", 0.0))
            if criterion.threshold is not None and cost_multiple >= criterion.threshold:
                return f"cost_multiple={cost_multiple:.2f} exceeded threshold={criterion.threshold:.2f}"
        elif criterion.trigger == "data_breach":
            age_minutes = float(state.get("data_age_minutes", 0.0))
            if criterion.threshold is not None and age_minutes >= criterion.threshold:
                return f"data_age_minutes={age_minutes:.1f} exceeded threshold={criterion.threshold:.1f}"
        return None

    @staticmethod
    def _diagnosis_for(trigger: str, detail: str) -> str:
        return f"{trigger.replace('_', ' ').title()} triggered: {detail}"

    @staticmethod
    def _lessons_for(trigger: str) -> list[str]:
        return {
            "invalidation": ["Re-test the thesis assumptions before reactivation."],
            "decay": ["Compare recent metrics to baseline and cut complexity if needed."],
            "drawdown": ["Reassess sizing and stop conditions."],
            "operator_decision": ["Document qualitative rationale for discretionary override."],
            "regime_change": ["Map strategy viability to explicit regime states."],
            "cost_exceeded": ["Re-estimate realistic implementation costs before relaunch."],
            "data_breach": ["Restore data health before trusting downstream signals."],
        }[trigger]
