"""
Tests for agent/capability_router.py — build_capability_router.

Verifies that routes are registered only for capabilities present in the
manifest and that excluded capabilities produce no registered routes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.capability_router import build_capability_router
from manifests.schema import AgentManifest


def _make_manifest(capabilities: list[str], role: str = "field_tech") -> AgentManifest:
    return AgentManifest(
        fleet_schema_version="4.2",
        device_id="test-device",
        device_owner_role=role,
        capability_set=capabilities,
        environment="test",
    )


def _route_paths(app: FastAPI) -> set[str]:
    return {r.path for r in app.routes}


class TestCapabilityRouterRegistration:
    def test_telemetry_only_manifest_registers_telemetry_route(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect"], role="read_only")
        build_capability_router(app, manifest)
        paths = _route_paths(app)
        assert "/telemetry/snapshot" in paths

    def test_telemetry_only_manifest_does_not_register_other_routes(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect"], role="read_only")
        build_capability_router(app, manifest)
        paths = _route_paths(app)
        assert "/diagnostics/report" not in paths
        assert "/update/apply" not in paths
        assert "/sensitive/metadata" not in paths

    def test_full_field_tech_manifest_registers_all_routes(self):
        app = FastAPI()
        manifest = _make_manifest(
            ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
        )
        build_capability_router(app, manifest)
        paths = _route_paths(app)
        assert "/telemetry/snapshot" in paths
        assert "/diagnostics/report" in paths
        assert "/update/apply" in paths
        assert "/sensitive/metadata" in paths

    def test_monitor_manifest_registers_only_telemetry_and_diagnostics(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect", "diagnostics_run"], role="monitor")
        build_capability_router(app, manifest)
        paths = _route_paths(app)
        assert "/telemetry/snapshot" in paths
        assert "/diagnostics/report" in paths
        assert "/update/apply" not in paths
        assert "/sensitive/metadata" not in paths


class TestCapabilityRouterResponds:
    def test_telemetry_endpoint_returns_200(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect"], role="read_only")
        build_capability_router(app, manifest)
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/telemetry/snapshot")
        assert response.status_code == 200
        assert response.json()["module"] == "telemetry"

    def test_diagnostics_endpoint_returns_200(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect", "diagnostics_run"], role="monitor")
        build_capability_router(app, manifest)
        client = TestClient(app)
        response = client.get("/diagnostics/report")
        assert response.status_code == 200
        assert response.json()["module"] == "diagnostics"

    def test_sensitive_endpoint_returns_200_when_capability_present(self):
        app = FastAPI()
        manifest = _make_manifest(
            ["telemetry_collect", "sensitive_data_read"], role="field_tech"
        )
        build_capability_router(app, manifest)
        client = TestClient(app)
        response = client.get("/sensitive/metadata")
        assert response.status_code == 200

    def test_excluded_route_returns_404(self):
        app = FastAPI()
        manifest = _make_manifest(["telemetry_collect"], role="read_only")
        build_capability_router(app, manifest)
        client = TestClient(app)
        response = client.get("/diagnostics/report")
        assert response.status_code == 404
