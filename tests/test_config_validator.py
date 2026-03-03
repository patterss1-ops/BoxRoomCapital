"""Tests for K-006 configuration validator."""

from __future__ import annotations

from ops.config_validator import (
    ConfigRule,
    ConfigValidator,
    ValidationReport,
    ValidationResult,
    ValidationSeverity,
)


class TestValidationResult:
    def test_to_dict(self):
        r = ValidationResult(
            field="max_drawdown",
            severity=ValidationSeverity.ERROR,
            message="Out of range",
            current_value=0.99,
        )
        d = r.to_dict()
        assert d["field"] == "max_drawdown"
        assert d["severity"] == "ERROR"
        assert d["message"] == "Out of range"
        assert d["current_value"] == 0.99

    def test_to_dict_none_value(self):
        r = ValidationResult(
            field="name",
            severity=ValidationSeverity.WARNING,
            message="Missing",
        )
        d = r.to_dict()
        assert d["current_value"] is None


class TestValidationReport:
    def test_passed_with_no_errors(self):
        report = ValidationReport(results=[
            ValidationResult(
                field="x", severity=ValidationSeverity.WARNING, message="w",
            ),
            ValidationResult(
                field="y", severity=ValidationSeverity.INFO, message="i",
            ),
        ])
        assert report.passed is True
        assert report.errors == 0
        assert report.warnings == 1

    def test_failed_with_errors(self):
        report = ValidationReport(results=[
            ValidationResult(
                field="x", severity=ValidationSeverity.ERROR, message="e",
            ),
            ValidationResult(
                field="y", severity=ValidationSeverity.WARNING, message="w",
            ),
        ])
        assert report.passed is False
        assert report.errors == 1
        assert report.warnings == 1

    def test_to_text(self):
        report = ValidationReport(results=[
            ValidationResult(
                field="limit",
                severity=ValidationSeverity.ERROR,
                message="Too high",
                current_value=999,
            ),
        ])
        text = report.to_text()
        assert "FAILED" in text
        assert "[ERROR]" in text
        assert "limit" in text
        assert "999" in text

    def test_to_text_passed(self):
        report = ValidationReport(results=[])
        text = report.to_text()
        assert "PASSED" in text
        assert "Errors: 0" in text

    def test_to_dict(self):
        report = ValidationReport(results=[
            ValidationResult(
                field="a", severity=ValidationSeverity.ERROR, message="bad",
            ),
        ])
        d = report.to_dict()
        assert d["passed"] is False
        assert d["errors"] == 1
        assert d["warnings"] == 0
        assert len(d["results"]) == 1


class TestConfigValidator:
    def test_add_and_validate_ok(self):
        v = ConfigValidator()
        v.add_rule(ConfigRule(
            field="name",
            check_fn=lambda val: val is not None,
            message="name is required",
        ))
        report = v.validate({"name": "my_strategy"})
        assert report.passed is True
        assert report.errors == 0

    def test_range_rule_pass(self):
        v = ConfigValidator()
        v.add_range_rule("max_drawdown", 0.0, 1.0)
        report = v.validate({"max_drawdown": 0.5})
        assert report.passed is True

    def test_range_rule_fail(self):
        v = ConfigValidator()
        v.add_range_rule("max_drawdown", 0.0, 1.0)
        report = v.validate({"max_drawdown": 1.5})
        assert report.passed is False
        assert report.errors == 1
        assert report.results[0].field == "max_drawdown"

    def test_required_rule_pass(self):
        v = ConfigValidator()
        v.add_required_rule("api_key")
        report = v.validate({"api_key": "abc123"})
        assert report.passed is True

    def test_required_rule_missing(self):
        v = ConfigValidator()
        v.add_required_rule("api_key")
        report = v.validate({})
        assert report.passed is False
        assert report.results[0].message == "Field is required"

    def test_required_rule_empty_string(self):
        v = ConfigValidator()
        v.add_required_rule("api_key")
        report = v.validate({"api_key": ""})
        assert report.passed is False

    def test_type_rule_pass(self):
        v = ConfigValidator()
        v.add_type_rule("timeout", int)
        report = v.validate({"timeout": 30})
        assert report.passed is True

    def test_type_rule_fail(self):
        v = ConfigValidator()
        v.add_type_rule("timeout", int)
        report = v.validate({"timeout": "thirty"})
        assert report.passed is False
        assert "int" in report.results[0].message

    def test_cross_ref_rule(self):
        v = ConfigValidator()
        v.add_cross_ref_rule(
            field_a="warn_threshold",
            field_b="hard_limit",
            check_fn=lambda a, b: a is not None and b is not None and a < b,
            message="warn_threshold must be less than hard_limit",
        )
        # Passing case
        report = v.validate({"warn_threshold": 80, "hard_limit": 100})
        assert report.passed is True

        # Failing case
        report = v.validate({"warn_threshold": 120, "hard_limit": 100})
        assert report.passed is False
        assert "warn_threshold" in report.results[0].field

    def test_multiple_rules(self):
        v = ConfigValidator()
        v.add_required_rule("name")
        v.add_range_rule("score", 0.0, 100.0)
        v.add_type_rule("enabled", bool)
        report = v.validate({"name": "test", "score": 50.0, "enabled": True})
        assert report.passed is True
        assert report.errors == 0

        report = v.validate({"score": 200.0, "enabled": "yes"})
        assert report.passed is False
        assert report.errors == 3  # name missing, score OOB, enabled wrong type

    def test_warnings_dont_fail(self):
        v = ConfigValidator()
        v.add_range_rule("latency", 0.0, 100.0, severity=ValidationSeverity.WARNING)
        report = v.validate({"latency": 999.0})
        assert report.passed is True
        assert report.warnings == 1
        assert report.errors == 0

    def test_empty_config(self):
        v = ConfigValidator()
        v.add_required_rule("field_a")
        v.add_required_rule("field_b")
        report = v.validate({})
        assert report.passed is False
        assert report.errors == 2

    def test_custom_check_fn(self):
        v = ConfigValidator()
        v.add_rule(ConfigRule(
            field="email",
            check_fn=lambda val: isinstance(val, str) and "@" in val,
            message="Must be a valid email address",
        ))
        report = v.validate({"email": "user@example.com"})
        assert report.passed is True

        report = v.validate({"email": "not-an-email"})
        assert report.passed is False
        assert "email" in report.results[0].message.lower()

    def test_cross_ref_with_validate(self):
        """Cross-ref rules are also included in the main validate() report."""
        v = ConfigValidator()
        v.add_cross_ref_rule(
            field_a="min_size",
            field_b="max_size",
            check_fn=lambda a, b: a is not None and b is not None and a <= b,
            message="min_size must be <= max_size",
        )
        report = v.validate({"min_size": 50, "max_size": 10})
        assert report.passed is False
        assert len(report.results) == 1

    def test_info_severity(self):
        v = ConfigValidator()
        v.add_rule(ConfigRule(
            field="debug",
            check_fn=lambda val: val is not True,
            severity=ValidationSeverity.INFO,
            message="Debug mode is enabled",
        ))
        report = v.validate({"debug": True})
        assert report.passed is True  # INFO does not fail
        assert report.errors == 0
        assert report.warnings == 0
        assert len(report.results) == 1
        assert report.results[0].severity == ValidationSeverity.INFO
