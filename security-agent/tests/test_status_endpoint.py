"""
Tests for the /status and / (dashboard) endpoints added to agent/main.py.

These routes are purely observational — they read data the agent already holds
in memory from startup and read the manifest file's mtime from disk.  No
manifest re-parsing occurs, and no touched files outside of agent/main.py.

Manifest-swap pattern is taken directly from test_updater.py: write a YAML
manifest to tmp_path, build the app against that path, then overwrite the file
and check that manifest_last_modified reflects the new mtime.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.main import build_app


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_manifest(path: Path, *, role: str, caps: list[str], version: str = "4.2") -> None:
    """Write a minimal valid YAML manifest to *path*."""
    lines = [
        f"fleet_schema_version: '{version}'",
        "device_id: 'test-device-status'",
        f"device_owner_role: {role}",
        "capability_set:",
    ]
    for c in caps:
        lines.append(f"  - {c}")
    lines.append("environment: production")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    """GET /status returns the 5 required fields with correct types."""

    def test_status_returns_all_five_fields(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        app = build_app(str(manifest))
        client = TestClient(app)

        response = client.get("/status")
        assert response.status_code == 200

        data = response.json()
        assert "device_id" in data
        assert "role" in data
        assert "fleet_schema_version" in data
        assert "capability_set" in data
        assert "manifest_path" in data
        assert "manifest_last_modified" in data

    def test_status_field_types_are_correct(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(
            manifest,
            role="field_tech",
            caps=["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"],
        )

        app = build_app(str(manifest))
        client = TestClient(app)

        data = client.get("/status").json()

        assert isinstance(data["device_id"], str)
        assert isinstance(data["role"], str)
        assert isinstance(data["fleet_schema_version"], str)
        assert isinstance(data["capability_set"], list)
        assert all(isinstance(c, str) for c in data["capability_set"])
        assert isinstance(data["manifest_path"], str)
        # manifest_last_modified must be an ISO 8601 string
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(data["manifest_last_modified"])
        assert dt.tzinfo is not None, "manifest_last_modified must be timezone-aware"

    def test_status_values_match_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"], version="4.2")

        app = build_app(str(manifest))
        client = TestClient(app)

        data = client.get("/status").json()

        assert data["device_id"] == "test-device-status"
        assert data["role"] == "read_only"
        assert data["fleet_schema_version"] == "4.2"
        assert data["capability_set"] == ["telemetry_collect"]
        assert data["manifest_path"] == str(manifest)


class TestStatusManifestLastModified:
    """manifest_last_modified changes after a manifest file is swapped on disk."""

    def test_last_modified_updates_after_file_swap(self, tmp_path):
        """
        Simulate the same atomic-swap that batch_dispatcher / updater performs:
        write a new manifest to a .tmp file then os.replace it over the active one.
        The next GET /status must report a later manifest_last_modified timestamp.
        """
        import os

        active = tmp_path / "manifest.yaml"
        _write_manifest(active, role="read_only", caps=["telemetry_collect"])

        app = build_app(str(active))
        client = TestClient(app)

        before = client.get("/status").json()["manifest_last_modified"]

        # Ensure filesystem mtime resolution is exceeded.
        time.sleep(0.05)

        # Atomic swap — same pattern used by updater.py and batch_dispatcher.py.
        proposed = tmp_path / "proposed.yaml"
        _write_manifest(proposed, role="read_only", caps=["telemetry_collect"], version="4.2.1")
        tmp_file = Path(str(active) + ".tmp")
        shutil.copy2(str(proposed), str(tmp_file))
        os.replace(str(tmp_file), str(active))

        after = client.get("/status").json()["manifest_last_modified"]

        assert after > before, (
            f"manifest_last_modified should advance after a file swap; "
            f"before={before!r}, after={after!r}"
        )


class TestDashboardEndpoint:
    """GET / returns an HTML page with the expected structure."""

    def test_root_returns_html(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        app = build_app(str(manifest))
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        body = response.text
        assert "fetch('/status')" in body
        assert "device-id" in body
        assert "setInterval" in body


class TestStatusLastRejection:
    """/status returns last_rejection from sidecar, null when absent."""

    def test_last_rejection_is_null_when_no_sidecar(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        app = build_app(str(manifest))
        client = TestClient(app)

        data = client.get("/status").json()
        assert "last_rejection" in data
        assert data["last_rejection"] is None

    def test_last_rejection_populated_when_sidecar_present(self, tmp_path):
        import json as _json

        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        # Write a sidecar as the dispatcher would
        sidecar = manifest.with_suffix(".rejection.json")
        payload = {
            "timestamp": "2025-01-01T00:00:00+00:00",
            "violations": ["Role 'read_only' does not permit the following capabilities: ['diagnostics_run']."],
            "recommendations": [
                {
                    "violation_type": "capability_boundary",
                    "message": "Role 'read_only' does not permit capability 'diagnostics_run'.",
                    "fix": "Remove diagnostics_run.",
                    "safe_capability_set": ["telemetry_collect"],
                    "supported_versions": None,
                }
            ],
        }
        sidecar.write_text(_json.dumps(payload), encoding="utf-8")

        app = build_app(str(manifest))
        client = TestClient(app)

        data = client.get("/status").json()
        assert data["last_rejection"] is not None
        assert data["last_rejection"]["timestamp"] == "2025-01-01T00:00:00+00:00"
        assert len(data["last_rejection"]["violations"]) == 1
        assert len(data["last_rejection"]["recommendations"]) == 1

    def test_last_rejection_clears_when_sidecar_deleted(self, tmp_path):
        import json as _json

        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        sidecar = manifest.with_suffix(".rejection.json")
        sidecar.write_text(_json.dumps({
            "timestamp": "2025-01-01T00:00:00+00:00",
            "violations": ["some violation"],
            "recommendations": [],
        }), encoding="utf-8")

        app = build_app(str(manifest))
        client = TestClient(app)

        # Sidecar present → rejection shown
        assert client.get("/status").json()["last_rejection"] is not None

        # Simulate dispatcher deleting sidecar after a successful push
        sidecar.unlink()

        # Sidecar gone → null
        assert client.get("/status").json()["last_rejection"] is None

    def test_dashboard_contains_rejection_banner_element(self, tmp_path):
        """The HTML dashboard must contain the rejection banner DOM element."""
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(manifest, role="read_only", caps=["telemetry_collect"])

        app = build_app(str(manifest))
        client = TestClient(app)

        body = client.get("/").text
        assert "rejection-banner" in body
        assert "last_rejection" in body
