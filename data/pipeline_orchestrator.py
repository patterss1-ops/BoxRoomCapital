"""DAG-based data pipeline orchestrator with dependency tracking.

L-001: Provides topological execution ordering, retry logic with
configurable backoff, cycle detection, and thread-safe state management
for multi-step data pipelines.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NodeStatus(str, Enum):
    """Execution status of an individual pipeline node."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(str, Enum):
    """Overall pipeline execution status."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NodeConfig:
    """Configuration for a single pipeline node."""

    name: str
    fn: Callable[[], Any]
    dependencies: list[str] = field(default_factory=list)
    max_retries: int = 0
    retry_delay: float = 0.0


@dataclass
class NodeResult:
    """Execution result for a single pipeline node."""

    name: str
    status: NodeStatus
    duration: float = 0.0
    error: Optional[str] = None
    retries_used: int = 0


@dataclass
class PipelineResult:
    """Execution result for the entire pipeline."""

    status: PipelineStatus
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    duration: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """DAG-based pipeline orchestrator with dependency tracking and retry logic.

    Nodes are registered via ``add_node`` and executed in topological order
    by ``run``.  Failed nodes cause their downstream dependents to be
    skipped.  Thread safety is ensured through a ``threading.Lock`` around
    all state mutations.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[str, NodeConfig] = {}
        self._status: PipelineStatus = PipelineStatus.IDLE

    # -- public API ---------------------------------------------------------

    def add_node(self, config: NodeConfig) -> None:
        """Register a node in the pipeline.

        Raises ``ValueError`` if a node with the same name already exists.
        """
        with self._lock:
            if config.name in self._nodes:
                raise ValueError(f"Node '{config.name}' already exists")
            self._nodes[config.name] = config

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty means valid).

        Checks for:
        * Missing dependencies (referenced but not registered).
        * Cycles in the dependency graph.
        """
        errors: list[str] = []

        # Missing dependencies
        for name, node in self._nodes.items():
            for dep in node.dependencies:
                if dep not in self._nodes:
                    errors.append(
                        f"Node '{name}' depends on unknown node '{dep}'"
                    )

        # Cycle detection via Kahn's algorithm
        if not errors:
            try:
                self._topological_sort()
            except ValueError as exc:
                errors.append(str(exc))

        return errors

    def get_execution_order(self) -> list[str]:
        """Return node names in topological (execution) order.

        Raises ``ValueError`` on cycles or missing dependencies.
        """
        validation_errors = self.validate()
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        return self._topological_sort()

    def run(self) -> PipelineResult:
        """Execute the full pipeline respecting dependency order.

        Returns a :class:`PipelineResult` summarising the run.
        Raises ``ValueError`` if validation fails before execution starts.
        """
        validation_errors = self.validate()
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        with self._lock:
            self._status = PipelineStatus.RUNNING

        order = self._topological_sort()
        node_results: dict[str, NodeResult] = {}
        started_at = datetime.now(timezone.utc)
        pipeline_t0 = time.monotonic()

        for name in order:
            config = self._nodes[name]

            # Check if any dependency failed/skipped → skip this node
            should_skip = False
            for dep in config.dependencies:
                dep_result = node_results.get(dep)
                if dep_result and dep_result.status in (
                    NodeStatus.FAILED,
                    NodeStatus.SKIPPED,
                ):
                    should_skip = True
                    break

            if should_skip:
                node_results[name] = NodeResult(
                    name=name,
                    status=NodeStatus.SKIPPED,
                )
                logger.info("Node '%s' skipped (dependency failed)", name)
                continue

            # Execute the node with retries
            node_results[name] = self._execute_node(config)

        pipeline_duration = time.monotonic() - pipeline_t0
        completed_at = datetime.now(timezone.utc)

        # Determine overall status
        overall = self._determine_overall_status(node_results)

        with self._lock:
            self._status = overall

        return PipelineResult(
            status=overall,
            node_results=node_results,
            duration=pipeline_duration,
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
        )

    def reset(self) -> None:
        """Clear all runtime state so the pipeline can be re-run.

        Node registrations are preserved.
        """
        with self._lock:
            self._status = PipelineStatus.IDLE

    # -- internal helpers ---------------------------------------------------

    def _topological_sort(self) -> list[str]:
        """Kahn's algorithm for topological sorting.

        Raises ``ValueError`` if the graph contains a cycle.
        """
        in_degree: dict[str, int] = {name: 0 for name in self._nodes}
        adjacency: dict[str, list[str]] = {name: [] for name in self._nodes}

        for name, node in self._nodes.items():
            for dep in node.dependencies:
                adjacency[dep].append(name)
                in_degree[name] += 1

        queue: deque[str] = deque(
            name for name, deg in in_degree.items() if deg == 0
        )
        order: list[str] = []

        while queue:
            # Sort the current frontier for deterministic ordering
            batch = sorted(queue)
            queue.clear()
            for node_name in batch:
                order.append(node_name)
                for downstream in adjacency[node_name]:
                    in_degree[downstream] -= 1
                    if in_degree[downstream] == 0:
                        queue.append(downstream)

        if len(order) != len(self._nodes):
            raise ValueError("Pipeline contains a cycle")

        return order

    def _execute_node(self, config: NodeConfig) -> NodeResult:
        """Run a single node, honouring retry configuration."""
        retries_used = 0
        last_error: Optional[str] = None

        with self._lock:
            pass  # status already RUNNING at pipeline level

        t0 = time.monotonic()
        attempts = 1 + config.max_retries

        for attempt in range(attempts):
            try:
                logger.info(
                    "Node '%s' attempt %d/%d",
                    config.name,
                    attempt + 1,
                    attempts,
                )
                config.fn()
                duration = time.monotonic() - t0
                return NodeResult(
                    name=config.name,
                    status=NodeStatus.SUCCESS,
                    duration=duration,
                    retries_used=retries_used,
                )
            except Exception as exc:
                last_error = str(exc)
                retries_used = attempt + 1
                logger.warning(
                    "Node '%s' attempt %d failed: %s",
                    config.name,
                    attempt + 1,
                    last_error,
                )
                if attempt < config.max_retries and config.retry_delay > 0:
                    time.sleep(config.retry_delay)

        duration = time.monotonic() - t0
        # retries_used should reflect only the *retry* count, not the initial attempt
        return NodeResult(
            name=config.name,
            status=NodeStatus.FAILED,
            duration=duration,
            error=last_error,
            retries_used=config.max_retries,
        )

    @staticmethod
    def _determine_overall_status(
        node_results: dict[str, NodeResult],
    ) -> PipelineStatus:
        """Derive pipeline status from individual node results."""
        if not node_results:
            return PipelineStatus.COMPLETED

        statuses = {r.status for r in node_results.values()}

        if statuses == {NodeStatus.SUCCESS}:
            return PipelineStatus.COMPLETED

        if NodeStatus.SUCCESS not in statuses:
            # All failed/skipped — nothing succeeded
            return PipelineStatus.FAILED

        # Mix of success and failure/skipped
        return PipelineStatus.PARTIAL
