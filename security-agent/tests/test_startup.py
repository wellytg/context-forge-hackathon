"""
Tests for agent/startup.py — run_startup_check and assert_startup_privileges.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agent.startup import assert_startup_privileges, run_startup_check
from manifests.schema import AgentManifest
from roles.validator import PrivilegeViolationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def role_map() -> dict[str, frozenset[str]]:
    return {
        "field_tech": frozenset(
            ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
        ),
        "monitor": frozenset(["telemetry_collect", "diagnostics_run"]),
        "read_only": frozenset(["telemetry_collect"]),
    }


@pytest.fixture()
def field_tech_manifest() -> AgentManifest:
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="device-001-field",
        device_owner_role="field_tech",
        capability_set=["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"],
        environment="production",
    )


@pytest.fixture()
def read_only_manifest() -> AgentManifest:
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="device-042-readonly",
        device_owner_role="read_only",
        capability_set=["telemetry_collect"],
        environment="production",
    )


# ---------------------------------------------------------------------------
# assert_startup_privileges (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestAssertStartupPrivileges:
    def test_valid_field_tech_manifest_passes(self, field_tech_manifest, role_map):
        # Must not raise.
        assert_startup_privileges(field_tech_manifest, role_map)

    def test_valid_read_only_manifest_passes(self, read_only_manifest, role_map):
        assert_startup_privileges(read_only_manifest, role_map)

    def test_manifest_with_excess_capability_raises(self, role_map):
        bad_manifest = AgentManifest(
            fleet_schema_version="4.2",
            device_id="device-bad",
            device_owner_role="read_only",
            capability_set=["telemetry_collect", "sensitive_data_read"],
            environment="production",
        )
        with pytest.raises(PrivilegeViolationError):
            assert_startup_privileges(bad_manifest, role_map)

    def test_manifest_with_missing_role_raises(self, role_map):
        bad_manifest = AgentManifest(
            fleet_schema_version="4.2",
            device_id="device-bad",
            device_owner_role="nonexistent_role",
            capability_set=["telemetry_collect"],
            environment="production",
        )
        with pytest.raises(PrivilegeViolationError):
            assert_startup_privileges(bad_manifest, role_map)


# ---------------------------------------------------------------------------
# run_startup_check (loads files, calls sys.exit on failure)
# ---------------------------------------------------------------------------

class TestRunStartupCheck:
    def test_valid_manifest_file_returns_manifest(self, tmp_path):
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'test-device'\n"
            "device_owner_role: read_only\n"
            "capability_set:\n"
            "  - telemetry_collect\n"
            "environment: production\n"
        )
        result = run_startup_check(str(manifest_file))
        assert result.device_id == "test-device"
        assert result.device_owner_role == "read_only"

    def test_startup_ok_record_is_printed(self, tmp_path, capsys):
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'test-device'\n"
            "device_owner_role: read_only\n"
            "capability_set:\n"
            "  - telemetry_collect\n"
            "environment: production\n"
        )
        run_startup_check(str(manifest_file))
        captured = capsys.readouterr()
        record = json.loads(captured.out.strip())
        assert record["event"] == "startup_ok"
        assert record["device_id"] == "test-device"
        assert "telemetry_collect" in record["capabilities"]
        assert record["fleet_schema_version"] == "4.2"

    def test_excess_capability_calls_sys_exit_1(self, tmp_path):
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'bad-device'\n"
            "device_owner_role: read_only\n"
            "capability_set:\n"
            "  - telemetry_collect\n"
            "  - sensitive_data_read\n"
            "environment: production\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            run_startup_check(str(manifest_file))
        assert exc_info.value.code == 1

    def test_startup_blocked_record_is_printed_to_stderr(self, tmp_path, capsys):
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'bad-device'\n"
            "device_owner_role: read_only\n"
            "capability_set:\n"
            "  - telemetry_collect\n"
            "  - sensitive_data_read\n"
            "environment: production\n"
        )
        with pytest.raises(SystemExit):
            run_startup_check(str(manifest_file))
        captured = capsys.readouterr()
        record = json.loads(captured.err.strip())
        assert record["event"] == "startup_blocked"
        assert "reason" in record

    def test_missing_role_in_role_map_calls_sys_exit_1(self, tmp_path):
        manifest_file = tmp_path / "manifest.yaml"
        manifest_file.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'bad-device'\n"
            "device_owner_role: ghost_role\n"
            "capability_set:\n"
            "  - telemetry_collect\n"
            "environment: production\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            run_startup_check(str(manifest_file))
        assert exc_info.value.code == 1
