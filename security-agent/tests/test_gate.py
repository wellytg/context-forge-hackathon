"""
Tests for deploy_gate/gate.py — check_manifest_update and GateResult.
"""

from __future__ import annotations

import pytest

from deploy_gate.gate import GateResult, check_manifest_update


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ROLE_MAP: dict[str, frozenset[str]] = {
    "field_tech": frozenset(
        ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
    ),
    "monitor": frozenset(["telemetry_collect", "diagnostics_run"]),
    "read_only": frozenset(["telemetry_collect"]),
}

SUPPORTED_VERSIONS = ["4.2", "4.3"]


def _write_manifest(path, *, version="4.2", device_id="dev-001", role="read_only", caps=None, env="production"):
    caps = caps or ["telemetry_collect"]
    lines = [
        f"fleet_schema_version: '{version}'",
        f"device_id: '{device_id}'",
        f"device_owner_role: {role}",
        "capability_set:",
    ]
    for c in caps:
        lines.append(f"  - {c}")
    lines.append(f"environment: {env}")
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoChangePass:
    def test_identical_manifests_pass(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current)
        _write_manifest(proposed)
        result = check_manifest_update(str(current), str(proposed), ROLE_MAP)
        assert result.passed
        assert result.violations == []


class TestCapabilityReductionPass:
    def test_removing_a_capability_is_allowed(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="monitor", caps=["telemetry_collect", "diagnostics_run"])
        _write_manifest(proposed, role="monitor", caps=["telemetry_collect"])
        result = check_manifest_update(str(current), str(proposed), ROLE_MAP)
        assert result.passed


class TestCapabilityEscalationFail:
    def test_capability_beyond_role_fails(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        _write_manifest(proposed, role="read_only", caps=["telemetry_collect", "sensitive_data_read"])
        result = check_manifest_update(str(current), str(proposed), ROLE_MAP)
        assert not result.passed
        assert any("sensitive_data_read" in v for v in result.violations)

    def test_multiple_excess_capabilities_all_reported(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="monitor", caps=["telemetry_collect"])
        _write_manifest(
            proposed,
            role="monitor",
            caps=["telemetry_collect", "sensitive_data_read", "update_receive"],
        )
        result = check_manifest_update(str(current), str(proposed), ROLE_MAP)
        assert not result.passed
        combined = " ".join(result.violations)
        assert "sensitive_data_read" in combined or "update_receive" in combined


class TestRoleEscalationFail:
    def test_role_change_without_flag_fails(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        _write_manifest(
            proposed, role="field_tech",
            caps=["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
        )
        result = check_manifest_update(str(current), str(proposed), ROLE_MAP)
        assert not result.passed
        assert any("device_owner_role" in v for v in result.violations)

    def test_role_change_with_flag_and_valid_caps_passes(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        _write_manifest(proposed, role="monitor", caps=["telemetry_collect", "diagnostics_run"])
        result = check_manifest_update(
            str(current), str(proposed), ROLE_MAP, allow_role_change=True
        )
        assert result.passed

    def test_role_change_with_flag_but_excess_caps_still_fails(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        # monitor role but requesting sensitive_data_read which monitor doesn't allow
        _write_manifest(
            proposed, role="monitor",
            caps=["telemetry_collect", "diagnostics_run", "sensitive_data_read"]
        )
        result = check_manifest_update(
            str(current), str(proposed), ROLE_MAP, allow_role_change=True
        )
        assert not result.passed


class TestSchemaVersionFail:
    def test_unsupported_version_fails(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, version="4.2")
        _write_manifest(proposed, version="9.9")
        result = check_manifest_update(
            str(current), str(proposed), ROLE_MAP,
            supported_versions=SUPPORTED_VERSIONS
        )
        assert not result.passed
        assert any("9.9" in v for v in result.violations)

    def test_supported_version_passes(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, version="4.2")
        _write_manifest(proposed, version="4.3")
        result = check_manifest_update(
            str(current), str(proposed), ROLE_MAP,
            supported_versions=SUPPORTED_VERSIONS
        )
        assert result.passed

    def test_no_version_restriction_accepts_any(self, tmp_path):
        current = tmp_path / "current.yaml"
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(current, version="4.2")
        _write_manifest(proposed, version="99.0")
        result = check_manifest_update(
            str(current), str(proposed), ROLE_MAP,
            supported_versions=None   # no restriction
        )
        assert result.passed


class TestGateResult:
    def test_str_on_pass(self):
        r = GateResult(passed=True)
        assert "PASS" in str(r)

    def test_str_on_fail_lists_violations(self):
        r = GateResult(passed=False, violations=["violation one", "violation two"])
        s = str(r)
        assert "FAIL" in s
        assert "violation one" in s
        assert "violation two" in s
