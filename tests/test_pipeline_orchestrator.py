"""Tests for L-001 DAG-based pipeline orchestrator.

Covers:
1.  Simple linear pipeline (A -> B -> C)
2.  Diamond dependency (A -> B, A -> C, B+C -> D)
3.  Parallel independent nodes
4.  Node failure skips dependents
5.  Retry on failure (node fails once, succeeds on retry)
6.  Cycle detection raises ValueError
7.  Missing dependency detection
8.  Empty pipeline
9.  Single node pipeline
10. Node with exception includes error in result
11. Pipeline duration tracking
12. Reset and re-run
13. Topological ordering correctness
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.pipeline_orchestrator import (
    NodeConfig,
    NodeResult,
    NodeStatus,
    PipelineOrchestrator,
    PipelineResult,
    PipelineStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recorder(log: list[str], name: str):
    """Return a callable that appends *name* to *log* when called."""
    def _fn() -> None:
        log.append(name)
    return _fn


def _make_failing(error_msg: str = "boom"):
    """Return a callable that always raises RuntimeError."""
    def _fn() -> None:
        raise RuntimeError(error_msg)
    return _fn


def _make_flaky(fail_times: int, log: list[str], name: str):
    """Return a callable that fails *fail_times* then succeeds."""
    call_count: dict[str, int] = {"n": 0}

    def _fn() -> None:
        call_count["n"] += 1
        if call_count["n"] <= fail_times:
            raise RuntimeError(f"{name} transient failure #{call_count['n']}")
        log.append(name)
    return _fn


# ---------------------------------------------------------------------------
# 1. Simple linear pipeline (A -> B -> C)
# ---------------------------------------------------------------------------

class TestLinearPipeline:

    def test_executes_in_order(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))
        orch.add_node(NodeConfig("B", _make_recorder(log, "B"), dependencies=["A"]))
        orch.add_node(NodeConfig("C", _make_recorder(log, "C"), dependencies=["B"]))

        result = orch.run()

        assert log == ["A", "B", "C"]
        assert result.status == PipelineStatus.COMPLETED
        assert set(result.node_results.keys()) == {"A", "B", "C"}
        for nr in result.node_results.values():
            assert nr.status == NodeStatus.SUCCESS


# ---------------------------------------------------------------------------
# 2. Diamond dependency
# ---------------------------------------------------------------------------

class TestDiamondDependency:

    def test_diamond_execution(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))
        orch.add_node(NodeConfig("B", _make_recorder(log, "B"), dependencies=["A"]))
        orch.add_node(NodeConfig("C", _make_recorder(log, "C"), dependencies=["A"]))
        orch.add_node(
            NodeConfig("D", _make_recorder(log, "D"), dependencies=["B", "C"]),
        )

        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        # A must come first, D must come last
        assert log[0] == "A"
        assert log[-1] == "D"
        # B and C come after A but before D
        assert set(log[1:3]) == {"B", "C"}


# ---------------------------------------------------------------------------
# 3. Parallel independent nodes
# ---------------------------------------------------------------------------

class TestParallelIndependentNodes:

    def test_independent_nodes_all_execute(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("X", _make_recorder(log, "X")))
        orch.add_node(NodeConfig("Y", _make_recorder(log, "Y")))
        orch.add_node(NodeConfig("Z", _make_recorder(log, "Z")))

        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert set(log) == {"X", "Y", "Z"}
        assert len(log) == 3


# ---------------------------------------------------------------------------
# 4. Node failure skips dependents
# ---------------------------------------------------------------------------

class TestFailureSkipsDependents:

    def test_downstream_skipped_on_failure(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))
        orch.add_node(NodeConfig("B", _make_failing("B failed"), dependencies=["A"]))
        orch.add_node(NodeConfig("C", _make_recorder(log, "C"), dependencies=["B"]))

        result = orch.run()

        assert result.node_results["A"].status == NodeStatus.SUCCESS
        assert result.node_results["B"].status == NodeStatus.FAILED
        assert result.node_results["C"].status == NodeStatus.SKIPPED
        assert "C" not in log
        assert result.status == PipelineStatus.PARTIAL

    def test_transitive_skip(self):
        """D depends on C which depends on B which fails."""
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))
        orch.add_node(NodeConfig("B", _make_failing(), dependencies=["A"]))
        orch.add_node(NodeConfig("C", _make_recorder(log, "C"), dependencies=["B"]))
        orch.add_node(NodeConfig("D", _make_recorder(log, "D"), dependencies=["C"]))

        result = orch.run()

        assert result.node_results["B"].status == NodeStatus.FAILED
        assert result.node_results["C"].status == NodeStatus.SKIPPED
        assert result.node_results["D"].status == NodeStatus.SKIPPED


# ---------------------------------------------------------------------------
# 5. Retry on failure
# ---------------------------------------------------------------------------

class TestRetryLogic:

    def test_succeeds_after_retry(self):
        log: list[str] = []
        flaky_fn = _make_flaky(fail_times=1, log=log, name="B")
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))
        orch.add_node(
            NodeConfig(
                "B", flaky_fn, dependencies=["A"],
                max_retries=2, retry_delay=0.0,
            ),
        )

        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert result.node_results["B"].status == NodeStatus.SUCCESS
        assert result.node_results["B"].retries_used == 1

    def test_exhausts_retries_then_fails(self):
        orch = PipelineOrchestrator()
        orch.add_node(
            NodeConfig(
                "A", _make_failing("always fails"),
                max_retries=2, retry_delay=0.0,
            ),
        )

        result = orch.run()

        assert result.node_results["A"].status == NodeStatus.FAILED
        assert result.node_results["A"].retries_used == 2
        assert result.node_results["A"].error == "always fails"


# ---------------------------------------------------------------------------
# 6. Cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:

    def test_direct_cycle_raises(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None, dependencies=["B"]))
        orch.add_node(NodeConfig("B", lambda: None, dependencies=["A"]))

        with pytest.raises(ValueError, match="cycle"):
            orch.run()

    def test_indirect_cycle_raises(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None, dependencies=["C"]))
        orch.add_node(NodeConfig("B", lambda: None, dependencies=["A"]))
        orch.add_node(NodeConfig("C", lambda: None, dependencies=["B"]))

        with pytest.raises(ValueError, match="cycle"):
            orch.get_execution_order()

    def test_validate_reports_cycle(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None, dependencies=["B"]))
        orch.add_node(NodeConfig("B", lambda: None, dependencies=["A"]))

        errors = orch.validate()
        assert len(errors) == 1
        assert "cycle" in errors[0].lower()


# ---------------------------------------------------------------------------
# 7. Missing dependency detection
# ---------------------------------------------------------------------------

class TestMissingDependency:

    def test_validate_catches_missing_dep(self):
        orch = PipelineOrchestrator()
        orch.add_node(
            NodeConfig("A", lambda: None, dependencies=["nonexistent"]),
        )

        errors = orch.validate()
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_run_raises_on_missing_dep(self):
        orch = PipelineOrchestrator()
        orch.add_node(
            NodeConfig("A", lambda: None, dependencies=["ghost"]),
        )

        with pytest.raises(ValueError, match="ghost"):
            orch.run()


# ---------------------------------------------------------------------------
# 8. Empty pipeline
# ---------------------------------------------------------------------------

class TestEmptyPipeline:

    def test_empty_pipeline_completes(self):
        orch = PipelineOrchestrator()
        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert result.node_results == {}
        assert result.duration >= 0

    def test_empty_execution_order(self):
        orch = PipelineOrchestrator()
        assert orch.get_execution_order() == []

    def test_empty_validate(self):
        orch = PipelineOrchestrator()
        assert orch.validate() == []


# ---------------------------------------------------------------------------
# 9. Single node pipeline
# ---------------------------------------------------------------------------

class TestSingleNodePipeline:

    def test_single_success(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("only", _make_recorder(log, "only")))

        result = orch.run()

        assert result.status == PipelineStatus.COMPLETED
        assert log == ["only"]
        assert result.node_results["only"].status == NodeStatus.SUCCESS

    def test_single_failure(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("only", _make_failing("oops")))

        result = orch.run()

        assert result.status == PipelineStatus.FAILED
        assert result.node_results["only"].status == NodeStatus.FAILED
        assert result.node_results["only"].error == "oops"


# ---------------------------------------------------------------------------
# 10. Node exception includes error in result
# ---------------------------------------------------------------------------

class TestNodeExceptionInResult:

    def test_error_message_captured(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("bad", _make_failing("detailed error info")))

        result = orch.run()

        nr = result.node_results["bad"]
        assert nr.status == NodeStatus.FAILED
        assert nr.error == "detailed error info"

    def test_error_type_preserved(self):
        def _raise_value_error() -> None:
            raise ValueError("wrong value")

        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("ve", _raise_value_error))

        result = orch.run()

        assert result.node_results["ve"].error == "wrong value"


# ---------------------------------------------------------------------------
# 11. Pipeline duration tracking
# ---------------------------------------------------------------------------

class TestDurationTracking:

    def test_pipeline_has_positive_duration(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: time.sleep(0.01)))

        result = orch.run()

        assert result.duration > 0
        assert result.started_at is not None
        assert result.completed_at is not None

    def test_node_has_positive_duration(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: time.sleep(0.01)))

        result = orch.run()

        assert result.node_results["A"].duration > 0

    def test_started_before_completed(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None))

        result = orch.run()

        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.started_at <= result.completed_at


# ---------------------------------------------------------------------------
# 12. Reset and re-run
# ---------------------------------------------------------------------------

class TestResetAndRerun:

    def test_can_rerun_after_reset(self):
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_recorder(log, "A")))

        result1 = orch.run()
        assert result1.status == PipelineStatus.COMPLETED
        assert log == ["A"]

        orch.reset()

        result2 = orch.run()
        assert result2.status == PipelineStatus.COMPLETED
        assert log == ["A", "A"]

    def test_reset_preserves_nodes(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None))
        orch.run()
        orch.reset()

        order = orch.get_execution_order()
        assert order == ["A"]


# ---------------------------------------------------------------------------
# 13. Topological ordering correctness
# ---------------------------------------------------------------------------

class TestTopologicalOrdering:

    def test_linear_order(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("C", lambda: None, dependencies=["B"]))
        orch.add_node(NodeConfig("B", lambda: None, dependencies=["A"]))
        orch.add_node(NodeConfig("A", lambda: None))

        order = orch.get_execution_order()
        assert order == ["A", "B", "C"]

    def test_diamond_order_constraints(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None))
        orch.add_node(NodeConfig("B", lambda: None, dependencies=["A"]))
        orch.add_node(NodeConfig("C", lambda: None, dependencies=["A"]))
        orch.add_node(
            NodeConfig("D", lambda: None, dependencies=["B", "C"]),
        )

        order = orch.get_execution_order()
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_independent_nodes_sorted_alphabetically(self):
        """Deterministic ordering: independent nodes come out alphabetically."""
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("Z", lambda: None))
        orch.add_node(NodeConfig("M", lambda: None))
        orch.add_node(NodeConfig("A", lambda: None))

        order = orch.get_execution_order()
        assert order == ["A", "M", "Z"]


# ---------------------------------------------------------------------------
# Edge cases / additional coverage
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_duplicate_node_raises(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", lambda: None))

        with pytest.raises(ValueError, match="already exists"):
            orch.add_node(NodeConfig("A", lambda: None))

    def test_all_nodes_fail_pipeline_status_failed(self):
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("A", _make_failing()))
        orch.add_node(NodeConfig("B", _make_failing()))

        result = orch.run()
        assert result.status == PipelineStatus.FAILED

    def test_partial_status_on_mixed_results(self):
        """One success + one independent failure = PARTIAL."""
        log: list[str] = []
        orch = PipelineOrchestrator()
        orch.add_node(NodeConfig("good", _make_recorder(log, "good")))
        orch.add_node(NodeConfig("bad", _make_failing()))

        result = orch.run()
        assert result.status == PipelineStatus.PARTIAL
