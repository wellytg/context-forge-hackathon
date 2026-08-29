"""
Tests for roles/validator.py — assert_capabilities_within_role and
PrivilegeViolationError.
"""

from __future__ import annotations

import pytest

from roles.validator import PrivilegeViolationError, assert_capabilities_within_role


@pytest.fixture()
def role_map() -> dict[str, frozenset[str]]:
    return {
        "field_tech": frozenset(
            ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
        ),
        "monitor": frozenset(["telemetry_collect", "diagnostics_run"]),
        "read_only": frozenset(["telemetry_collect"]),
    }


class TestExactMatch:
    def test_exact_capability_set_passes(self, role_map):
        # All capabilities declared, exactly matching the role — must pass.
        assert_capabilities_within_role(
            "field_tech",
            ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"],
            role_map,
        )

    def test_single_capability_passes(self, role_map):
        assert_capabilities_within_role("read_only", ["telemetry_collect"], role_map)


class TestSubsetAllowed:
    def test_subset_of_role_capabilities_passes(self, role_map):
        # field_tech manifest that only uses two of its four allowed capabilities.
        assert_capabilities_within_role(
            "field_tech",
            ["telemetry_collect", "diagnostics_run"],
            role_map,
        )

    def test_single_cap_from_multi_cap_role_passes(self, role_map):
        assert_capabilities_within_role("monitor", ["telemetry_collect"], role_map)


class TestExcessCapabilityRejected:
    def test_one_excess_capability_raises(self, role_map):
        with pytest.raises(PrivilegeViolationError) as exc_info:
            assert_capabilities_within_role(
                "read_only",
                ["telemetry_collect", "diagnostics_run"],  # diagnostics_run not allowed
                role_map,
            )
        assert "diagnostics_run" in str(exc_info.value)
        assert exc_info.value.role == "read_only"
        assert "diagnostics_run" in exc_info.value.excess_capabilities

    def test_multiple_excess_capabilities_raises(self, role_map):
        with pytest.raises(PrivilegeViolationError) as exc_info:
            assert_capabilities_within_role(
                "monitor",
                ["telemetry_collect", "diagnostics_run", "sensitive_data_read", "update_receive"],
                role_map,
            )
        assert exc_info.value.excess_capabilities == {"sensitive_data_read", "update_receive"}

    def test_completely_wrong_capabilities_raises(self, role_map):
        with pytest.raises(PrivilegeViolationError):
            assert_capabilities_within_role(
                "read_only",
                ["sensitive_data_read", "update_receive"],
                role_map,
            )


class TestUnknownRoleRejected:
    def test_unknown_role_raises(self, role_map):
        with pytest.raises(PrivilegeViolationError) as exc_info:
            assert_capabilities_within_role("super_admin", ["telemetry_collect"], role_map)
        assert exc_info.value.role == "super_admin"

    def test_empty_role_string_raises(self, role_map):
        with pytest.raises(PrivilegeViolationError):
            assert_capabilities_within_role("", ["telemetry_collect"], role_map)


class TestPrivilegeViolationError:
    def test_error_message_contains_role(self, role_map):
        try:
            assert_capabilities_within_role("monitor", ["sensitive_data_read"], role_map)
        except PrivilegeViolationError as exc:
            assert "monitor" in str(exc)

    def test_error_attributes(self, role_map):
        try:
            assert_capabilities_within_role("read_only", ["update_receive"], role_map)
        except PrivilegeViolationError as exc:
            assert exc.role == "read_only"
            assert exc.excess_capabilities == {"update_receive"}
