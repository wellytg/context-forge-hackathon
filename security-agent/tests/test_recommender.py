"""
Tests for deploy_gate/recommender.py — build_recommendations()

The recommender is a pure translation layer: no file I/O, no gating logic.
All tests construct in-memory GateResult and AgentManifest objects and assert
on the structured Recommendation output.

Test organisation:
  TestCleanPass          — passing GateResult → empty list
  TestCapabilityBoundary — capability violations → correct type, safe_cap_set
  TestUnsupportedVersion — version violations → correct type, supported_versions
  TestRoleChange         — role-change violations → correct type
  TestMultiViolation     — multiple violations → one Recommendation per violation
  TestUnknownViolation   — forward-compat: unrecognised violation → 'unknown' type
"""

from __future__ import annotations

import pytest

from deploy_gate.gate import GateResult
from deploy_gate.recommender import Recommendation, build_recommendations
from manifests.schema import AgentManifest
from roles.loader import load_role_map


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _manifest(
    *,
    role: str,
    caps: list[str],
    device_id: str = "test-device",
    version: str = "4.2.0",
) -> AgentManifest:
    return AgentManifest(
        fleet_schema_version=version,
        device_id=device_id,
        device_owner_role=role,
        capability_set=caps,
        environment="production",
    )


_ROLE_MAP = load_role_map()
_SUPPORTED = ["4.2.0", "4.2.1", "4.2.2"]


# ---------------------------------------------------------------------------
# TestCleanPass
# ---------------------------------------------------------------------------

class TestCleanPass:
    def test_passing_result_returns_empty_list(self):
        proposed = _manifest(role="read_only", caps=["telemetry_collect"])
        result = GateResult(passed=True, violations=[])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs == []

    def test_passing_result_with_full_caps_returns_empty_list(self):
        proposed = _manifest(
            role="field_tech",
            caps=["telemetry_collect", "diagnostics_run",
                  "update_receive", "sensitive_data_read"],
        )
        result = GateResult(passed=True, violations=[])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs == []


# ---------------------------------------------------------------------------
# TestCapabilityBoundary
# ---------------------------------------------------------------------------

class TestCapabilityBoundary:
    """Capability violations produce type='capability_boundary' with safe_capability_set."""

    def _reject_monitor_with_update_receive(self) -> tuple[GateResult, AgentManifest]:
        proposed = _manifest(
            role="monitor",
            caps=["telemetry_collect", "diagnostics_run", "update_receive"],
        )
        violation = (
            "Role 'monitor' does not permit the following capabilities: "
            "['update_receive']. Remove them from capability_set or use a "
            "role that allows them."
        )
        result = GateResult(passed=False, violations=[violation])
        return result, proposed

    def test_violation_type_is_capability_boundary(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert len(recs) == 1
        assert recs[0].violation_type == "capability_boundary"

    def test_safe_capability_set_excludes_excess(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        # update_receive is NOT in monitor's permitted set — must not be in safe set
        assert "update_receive" not in recs[0].safe_capability_set

    def test_safe_capability_set_includes_permitted_caps(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        # telemetry_collect and diagnostics_run ARE permitted for monitor
        assert "telemetry_collect" in recs[0].safe_capability_set
        assert "diagnostics_run" in recs[0].safe_capability_set

    def test_safe_capability_set_is_sorted(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        scs = recs[0].safe_capability_set
        assert scs == sorted(scs)

    def test_supported_versions_is_none_for_cap_violation(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].supported_versions is None

    def test_fix_string_mentions_excess_capability(self):
        result, proposed = self._reject_monitor_with_update_receive()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert "update_receive" in recs[0].fix

    def test_read_only_multi_cap_violation(self):
        """read_only only permits telemetry_collect — everything else is excess."""
        proposed = _manifest(
            role="read_only",
            caps=["telemetry_collect", "diagnostics_run", "sensitive_data_read"],
        )
        violation = (
            "Role 'read_only' does not permit the following capabilities: "
            "['diagnostics_run', 'sensitive_data_read']. Remove them from "
            "capability_set or use a role that allows them."
        )
        result = GateResult(passed=False, violations=[violation])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].violation_type == "capability_boundary"
        assert recs[0].safe_capability_set == ["telemetry_collect"]

    def test_empty_safe_set_when_no_proposed_caps_permitted(self):
        """If NONE of the proposed caps are in the role's permitted set."""
        proposed = _manifest(
            role="read_only",
            caps=["diagnostics_run"],  # not permitted for read_only
        )
        violation = (
            "Role 'read_only' does not permit the following capabilities: "
            "['diagnostics_run']. Remove them from capability_set or use a "
            "role that allows them."
        )
        result = GateResult(passed=False, violations=[violation])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].safe_capability_set == []


# ---------------------------------------------------------------------------
# TestUnsupportedVersion
# ---------------------------------------------------------------------------

class TestUnsupportedVersion:
    """Version violations produce type='unsupported_version' with supported_versions."""

    def _reject_bad_version(self) -> tuple[GateResult, AgentManifest]:
        proposed = _manifest(role="read_only", caps=["telemetry_collect"], version="9.9.9")
        violation = (
            "Unsupported fleet_schema_version '9.9.9'. "
            "Supported: ['4.2.0', '4.2.1', '4.2.2']."
        )
        result = GateResult(passed=False, violations=[violation])
        return result, proposed

    def test_violation_type_is_unsupported_version(self):
        result, proposed = self._reject_bad_version()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert len(recs) == 1
        assert recs[0].violation_type == "unsupported_version"

    def test_supported_versions_is_populated(self):
        result, proposed = self._reject_bad_version()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].supported_versions == _SUPPORTED

    def test_safe_capability_set_is_none_for_version_violation(self):
        result, proposed = self._reject_bad_version()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].safe_capability_set is None

    def test_fix_mentions_supported_versions(self):
        result, proposed = self._reject_bad_version()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert "4.2.0" in recs[0].fix or "supported" in recs[0].fix.lower()


# ---------------------------------------------------------------------------
# TestRoleChange
# ---------------------------------------------------------------------------

class TestRoleChange:
    """Role-change violations produce type='role_change'."""

    def _reject_role_change(self) -> tuple[GateResult, AgentManifest]:
        proposed = _manifest(role="field_tech", caps=["telemetry_collect"])
        violation = (
            "device_owner_role changed from 'read_only' to 'field_tech' "
            "without --allow-role-change."
        )
        result = GateResult(passed=False, violations=[violation])
        return result, proposed

    def test_violation_type_is_role_change(self):
        result, proposed = self._reject_role_change()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert len(recs) == 1
        assert recs[0].violation_type == "role_change"

    def test_safe_capability_set_is_none(self):
        result, proposed = self._reject_role_change()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].safe_capability_set is None

    def test_supported_versions_is_none(self):
        result, proposed = self._reject_role_change()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert recs[0].supported_versions is None

    def test_fix_mentions_allow_role_change(self):
        result, proposed = self._reject_role_change()
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert "--allow-role-change" in recs[0].fix


# ---------------------------------------------------------------------------
# TestMultiViolation
# ---------------------------------------------------------------------------

class TestMultiViolation:
    """Multiple violations in one GateResult → one Recommendation per violation."""

    def test_two_violations_produce_two_recommendations(self):
        proposed = _manifest(
            role="read_only",
            caps=["telemetry_collect", "diagnostics_run"],
            version="9.9.9",
        )
        violations = [
            "Unsupported fleet_schema_version '9.9.9'. Supported: ['4.2.0'].",
            "Role 'read_only' does not permit the following capabilities: "
            "['diagnostics_run']. Remove them from capability_set or use a "
            "role that allows them.",
        ]
        result = GateResult(passed=False, violations=violations)
        recs = build_recommendations(result, proposed, _ROLE_MAP, ["4.2.0"])
        assert len(recs) == 2

    def test_multi_violation_types_are_distinct(self):
        proposed = _manifest(
            role="read_only",
            caps=["telemetry_collect", "diagnostics_run"],
            version="9.9.9",
        )
        violations = [
            "Unsupported fleet_schema_version '9.9.9'. Supported: ['4.2.0'].",
            "Role 'read_only' does not permit the following capabilities: "
            "['diagnostics_run']. Remove them from capability_set or use a "
            "role that allows them.",
        ]
        result = GateResult(passed=False, violations=violations)
        recs = build_recommendations(result, proposed, _ROLE_MAP, ["4.2.0"])
        types = {r.violation_type for r in recs}
        assert "unsupported_version" in types
        assert "capability_boundary" in types


# ---------------------------------------------------------------------------
# TestUnknownViolation
# ---------------------------------------------------------------------------

class TestUnknownViolation:
    """Unrecognised violation strings are handled gracefully."""

    def test_unknown_violation_type_is_unknown(self):
        proposed = _manifest(role="read_only", caps=["telemetry_collect"])
        result = GateResult(passed=False, violations=["Something entirely new."])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert len(recs) == 1
        assert recs[0].violation_type == "unknown"

    def test_unknown_violation_message_contains_original_text(self):
        proposed = _manifest(role="read_only", caps=["telemetry_collect"])
        result = GateResult(passed=False, violations=["Something entirely new."])
        recs = build_recommendations(result, proposed, _ROLE_MAP, _SUPPORTED)
        assert "Something entirely new." in recs[0].message
