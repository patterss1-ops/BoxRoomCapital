"""Configuration validator.

K-006: Validate configuration dictionaries against a set of rules.
Supports required-field, type, range, and cross-reference checks with
configurable severity levels and human-readable reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ValidationSeverity(str, Enum):
    """Severity level for a validation finding."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationResult:
    """A single validation finding."""

    field: str
    severity: ValidationSeverity
    message: str
    current_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "severity": self.severity.value,
            "message": self.message,
            "current_value": self.current_value,
        }


@dataclass
class ValidationReport:
    """Aggregated validation report."""

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Report passes when there are no ERROR-level results."""
        return all(r.severity != ValidationSeverity.ERROR for r in self.results)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.severity == ValidationSeverity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.severity == ValidationSeverity.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
            "results": [r.to_dict() for r in self.results],
        }

    def to_text(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [
            f"{'=' * 50}",
            f"  Validation Report: {status}",
            f"  Errors: {self.errors}  Warnings: {self.warnings}",
            f"{'=' * 50}",
            "",
        ]
        for r in self.results:
            lines.append(f"  [{r.severity.value}] {r.field}: {r.message}")
            if r.current_value is not None:
                lines.append(f"    current_value = {r.current_value!r}")
        return "\n".join(lines)


@dataclass
class ConfigRule:
    """A single validation rule."""

    field: str
    check_fn: Callable[[Any], bool]
    severity: ValidationSeverity = ValidationSeverity.ERROR
    message: str = ""


@dataclass
class _CrossRefRule:
    """Internal rule for cross-field relationship checks."""

    field_a: str
    field_b: str
    check_fn: Callable[[Any, Any], bool]
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


class ConfigValidator:
    """Validates a configuration dict against registered rules."""

    def __init__(self) -> None:
        self._rules: list[ConfigRule] = []
        self._cross_ref_rules: list[_CrossRefRule] = []

    # ------------------------------------------------------------------
    # Rule registration
    # ------------------------------------------------------------------

    def add_rule(self, rule: ConfigRule) -> None:
        """Register a custom validation rule."""
        self._rules.append(rule)

    def add_range_rule(
        self,
        field: str,
        min_val: float,
        max_val: float,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> None:
        """Convenience: value must be within [min_val, max_val]."""
        self._rules.append(ConfigRule(
            field=field,
            check_fn=lambda v, lo=min_val, hi=max_val: (
                v is not None and lo <= v <= hi
            ),
            severity=severity,
            message=f"Must be between {min_val} and {max_val}",
        ))

    def add_required_rule(
        self,
        field: str,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> None:
        """Convenience: value must not be None or empty."""
        self._rules.append(ConfigRule(
            field=field,
            check_fn=lambda v: v is not None and v != "" and v != [],
            severity=severity,
            message="Field is required",
        ))

    def add_type_rule(
        self,
        field: str,
        expected_type: type,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> None:
        """Convenience: value must be an instance of *expected_type*."""
        self._rules.append(ConfigRule(
            field=field,
            check_fn=lambda v, t=expected_type: isinstance(v, t),
            severity=severity,
            message=f"Must be of type {expected_type.__name__}",
        ))

    def add_cross_ref_rule(
        self,
        field_a: str,
        field_b: str,
        check_fn: Callable[[Any, Any], bool],
        message: str,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> None:
        """Register a cross-field relationship check."""
        self._cross_ref_rules.append(_CrossRefRule(
            field_a=field_a,
            field_b=field_b,
            check_fn=check_fn,
            message=message,
            severity=severity,
        ))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, config: dict[str, Any]) -> ValidationReport:
        """Run all rules against *config* and return a report."""
        results: list[ValidationResult] = []

        for rule in self._rules:
            value = config.get(rule.field)
            if not rule.check_fn(value):
                results.append(ValidationResult(
                    field=rule.field,
                    severity=rule.severity,
                    message=rule.message,
                    current_value=value,
                ))

        results.extend(self.validate_cross_refs(config))

        return ValidationReport(results=results)

    def validate_cross_refs(
        self, config: dict[str, Any],
    ) -> list[ValidationResult]:
        """Check cross-field relationships registered via add_cross_ref_rule."""
        results: list[ValidationResult] = []
        for xr in self._cross_ref_rules:
            val_a = config.get(xr.field_a)
            val_b = config.get(xr.field_b)
            if not xr.check_fn(val_a, val_b):
                results.append(ValidationResult(
                    field=f"{xr.field_a} <-> {xr.field_b}",
                    severity=xr.severity,
                    message=xr.message,
                    current_value={"a": val_a, "b": val_b},
                ))
        return results
