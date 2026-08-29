"""
Shared pytest fixtures used across the test suite.
"""

from __future__ import annotations

import pytest

from manifests.schema import AgentManifest
from roles.loader import load_role_map


@pytest.fixture()
def field_tech_manifest() -> AgentManifest:
    """A fully privileged field_tech manifest."""
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="device-001-field",
        device_owner_role="field_tech",
        capability_set=[
            "telemetry_collect",
            "diagnostics_run",
            "update_receive",
            "sensitive_data_read",
        ],
        environment="production",
    )


@pytest.fixture()
def read_only_manifest() -> AgentManifest:
    """A read_only manifest with the minimum capability set."""
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="device-042-readonly",
        device_owner_role="read_only",
        capability_set=["telemetry_collect"],
        environment="production",
    )


@pytest.fixture()
def monitor_manifest() -> AgentManifest:
    """A monitor manifest."""
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="device-010-monitor",
        device_owner_role="monitor",
        capability_set=["telemetry_collect", "diagnostics_run"],
        environment="staging",
    )


@pytest.fixture()
def role_map() -> dict[str, frozenset[str]]:
    """The bundled role map loaded from roles/role_map.yaml."""
    return load_role_map()
