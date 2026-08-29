"""
Tests for the GET /recommendations endpoint on the batch dispatcher.

Verifies:
  - Before any push: empty sentinel with correct shape
  - After a push with rejections: correct device IDs, violation_type, safe_capability_set
  - After a clean push (all pass): recommendations list is empty

Uses the same fleet fixture and helpers from test_batch_dispatcher.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import sys
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import batch_dispatcher as bd
from batch_dispatcher import app, _run_batch


# ---------------------------------------------------------------------------
# Helpers (mirrors test_batch_dispatcher.py pattern)
# ---------------------------------------------------------------------------

def _write_manifest(path: Path, *, version: str, device_id: str, role: str,
                    caps: list[str], env: str = "production") -> None:
    data = {
        "fleet_schema_version": version,
        "device_id": device_id,
        "device_owner_role": role,
        "capability_set": caps,
        "environment": env,
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def _write_update_template(path: Path, *, version: str, caps: list[str]) -> None:
    data = {
        "fleet_schema_version": version,
        "device_id": "__BATCH_PLACEHOLDER__",
        "device_owner_role": "__BATCH_PLACEHOLDER__",
        "capability_set": caps,
        "environment": "production",
    }
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def _write_batch_targets(path: Path, targets: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"batch": targets}, fh, sort_keys=False)


@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """Synthetic 2-device fleet (same pattern as test_batch_dispatcher.py)."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()

    ft_manifest  = manifests_dir / "field_tech.yaml"
    mon_manifest = manifests_dir / "monitor.yaml"

    _write_manifest(ft_manifest, version="4.2", device_id="dev-ft-001",
                    role="field_tech",
                    caps=["telemetry_collect", "diagnostics_run",
                          "update_receive", "sensitive_data_read"])

    _write_manifest(mon_manifest, version="4.2", device_id="dev-mon-099",
                    role="monitor",
                    caps=["telemetry_collect", "diagnostics_run"])

    targets_file = tmp_path / "batch_targets.yaml"
    _write_batch_targets(targets_file, [
        {"device_id": "dev-ft-001",  "role": "field_tech",
         "current_manifest": str(ft_manifest)},
        {"device_id": "dev-mon-099", "role": "monitor",
         "current_manifest": str(mon_manifest)},
    ])

    monkeypatch.setattr(bd, "BATCH_TARGETS_PATH", targets_file)
    monkeypatch.setattr(bd, "_HERE", tmp_path)

    return {"ft_manifest": ft_manifest, "mon_manifest": mon_manifest,
            "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecommendationsEndpointBeforePush:
    """GET /recommendations before any push returns the empty sentinel."""

    def test_empty_sentinel_before_push(self, monkeypatch):
        monkeypatch.setattr(bd, "_last_recommendations", None)
        client = TestClient(app)
        resp = client.get("/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_push_file"] is None
        assert data["recommendations"] == []
        assert data["rejected"] == 0

    def test_empty_sentinel_has_required_keys(self, monkeypatch):
        monkeypatch.setattr(bd, "_last_recommendations", None)
        client = TestClient(app)
        data = client.get("/recommendations").json()
        for key in ("last_push_file", "devices_total", "applied", "rejected",
                    "recommendations"):
            assert key in data, f"Missing key: {key}"


class TestRecommendationsEndpointAfterRejection:
    """GET /recommendations after a push with rejections returns structured data."""

    def test_rejected_device_appears_in_recommendations(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        _run_batch(update)  # monitor will be rejected

        client = TestClient(app)
        data = client.get("/recommendations").json()

        assert data["rejected"] == 1
        assert len(data["recommendations"]) == 1
        device_rec = data["recommendations"][0]
        assert device_rec["device_id"] == "dev-mon-099"

    def test_recommendation_has_violation_type(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        _run_batch(update)

        client = TestClient(app)
        data = client.get("/recommendations").json()

        device_rec = data["recommendations"][0]
        assert len(device_rec["recommendations"]) >= 1
        rec = device_rec["recommendations"][0]
        assert rec["violation_type"] == "capability_boundary"

    def test_recommendation_has_safe_capability_set(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        _run_batch(update)

        client = TestClient(app)
        data = client.get("/recommendations").json()

        rec = data["recommendations"][0]["recommendations"][0]
        scs = rec["safe_capability_set"]
        assert isinstance(scs, list)
        assert "update_receive" not in scs   # excess cap excluded
        assert "telemetry_collect" in scs    # permitted cap included

    def test_passing_devices_not_in_recommendations_list(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        _run_batch(update)

        client = TestClient(app)
        data = client.get("/recommendations").json()

        device_ids = [r["device_id"] for r in data["recommendations"]]
        assert "dev-ft-001" not in device_ids   # field_tech passed
        assert "dev-mon-099" in device_ids       # monitor was rejected

    def test_last_push_file_is_set(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "update_receive"])
        _run_batch(update)

        client = TestClient(app)
        data = client.get("/recommendations").json()
        assert data["last_push_file"] == "update_bad.yaml"


class TestRecommendationsEndpointAfterCleanPush:
    """After a fully clean push, recommendations list is empty."""

    def test_clean_push_produces_empty_recommendations_list(self, fleet, tmp_path):
        update = tmp_path / "update_clean.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect"])  # safe for all roles
        _run_batch(update)

        client = TestClient(app)
        data = client.get("/recommendations").json()

        assert data["rejected"] == 0
        assert data["recommendations"] == []
