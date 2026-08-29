"""
Tests for batch_dispatcher._run_batch()
=========================================

Exercises the two core dispatcher behaviours without starting a real HTTP server:

1. A device whose role boundary is violated is REJECTED and its manifest is
   left completely untouched.
2. A device whose update is within its role boundary is APPLIED — the manifest
   file on disk is atomically replaced with the proposed content.

Both tests use temporary directories and synthetic manifests/targets so they
run entirely in isolation with no side-effects on the real manifest files.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

# Ensure the package root is importable when pytest is run from the repo root.
import sys
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from batch_dispatcher import _run_batch, BATCH_TARGETS_PATH  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
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
    """Write a universal update template with placeholder device_id / role."""
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """
    Build a self-contained synthetic fleet in tmp_path:

        manifests/
            field_tech.yaml   — field_tech role (4 caps)
            monitor.yaml      — monitor role   (2 caps)
        batch_targets.yaml    — 2-device roster

    Patches BATCH_TARGETS_PATH in batch_dispatcher so _run_batch() reads the
    synthetic roster instead of the real one.
    """
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()

    # Current manifests (the "before" state on disk)
    ft_manifest  = manifests_dir / "field_tech.yaml"
    mon_manifest = manifests_dir / "monitor.yaml"

    _write_manifest(ft_manifest,
                    version="4.2", device_id="dev-ft-001",
                    role="field_tech",
                    caps=["telemetry_collect", "diagnostics_run",
                          "update_receive", "sensitive_data_read"])

    _write_manifest(mon_manifest,
                    version="4.2", device_id="dev-mon-099",
                    role="monitor",
                    caps=["telemetry_collect", "diagnostics_run"])

    # Batch targets file
    targets_file = tmp_path / "batch_targets.yaml"
    _write_batch_targets(targets_file, [
        {
            "device_id":        "dev-ft-001",
            "role":             "field_tech",
            "current_manifest": str(ft_manifest),
        },
        {
            "device_id":        "dev-mon-099",
            "role":             "monitor",
            "current_manifest": str(mon_manifest),
        },
    ])

    # Patch the module-level constant so _run_batch reads our synthetic targets
    import batch_dispatcher as bd
    monkeypatch.setattr(bd, "BATCH_TARGETS_PATH", targets_file)
    # Also patch _HERE so relative current_manifest paths resolve correctly
    # (not needed here because we use absolute paths in targets, but keep
    #  the patch for correctness)
    monkeypatch.setattr(bd, "_HERE", tmp_path)

    return {
        "ft_manifest":  ft_manifest,
        "mon_manifest": mon_manifest,
        "tmp_path":     tmp_path,
    }


# ---------------------------------------------------------------------------
# Test 1 — failing device is REJECTED and manifest is untouched
# ---------------------------------------------------------------------------

class TestFailingDeviceIsRejected:
    def test_monitor_rejected_when_caps_exceed_role(self, fleet, tmp_path):
        """
        Pushing an update with 'update_receive' to a monitor-role device must
        be REJECTED.  The monitor manifest on disk must be byte-for-byte
        identical after the batch run — i.e. the dispatcher never touched it.
        """
        # Capture the monitor manifest content BEFORE the run
        mon_before = fleet["mon_manifest"].read_bytes()

        # Update template adds update_receive — forbidden for monitor
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])

        result = _run_batch(update)

        # Find the monitor device result
        mon_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-mon-099"
        )

        assert mon_result["result"] == "FAIL", (
            "monitor device should FAIL gate check"
        )
        assert mon_result["status"] == "REJECTED", (
            "monitor device should be REJECTED"
        )
        assert any("update_receive" in v for v in mon_result["violations"]), (
            "violation message should name the offending capability"
        )

        # Manifest on disk must be unchanged
        mon_after = fleet["mon_manifest"].read_bytes()
        assert mon_before == mon_after, (
            "monitor manifest must not be modified when gate FAILS"
        )


# ---------------------------------------------------------------------------
# Test 2 — passing device is APPLIED and manifest is updated on disk
# ---------------------------------------------------------------------------

class TestPassingDeviceIsApplied:
    def test_field_tech_applied_when_caps_within_role(self, fleet, tmp_path):
        """
        Pushing a clean update (all caps within field_tech boundary) to a
        field_tech device must be APPLIED.  The manifest on disk must reflect
        the new fleet_schema_version after the batch run.
        """
        # Update template: version bump, caps all within field_tech boundary
        update = tmp_path / "update_clean.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive", "sensitive_data_read"])

        result = _run_batch(update)

        ft_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-ft-001"
        )

        assert ft_result["result"] == "PASS", (
            "field_tech device should PASS gate check"
        )
        assert ft_result["status"] == "APPLIED", (
            "field_tech device should be APPLIED"
        )
        assert ft_result["violations"] == [], (
            "no violations expected on a clean update"
        )

        # Manifest on disk must now carry the new version
        updated_data = yaml.safe_load(fleet["ft_manifest"].read_text(encoding="utf-8"))
        assert updated_data["fleet_schema_version"] == "4.2.0", (
            "manifest on disk should reflect the newly applied version"
        )


# ---------------------------------------------------------------------------
# Test 3 — summary counts are correct in a mixed batch
# ---------------------------------------------------------------------------

class TestSummaryCountsMixedBatch:
    def test_applied_rejected_counts_match_results(self, fleet, tmp_path):
        """
        When one device passes and one fails, the top-level summary counters
        (applied / rejected / devices_total) must reflect the per-device results.
        """
        # Bad update: update_receive is fine for field_tech, forbidden for monitor
        update = tmp_path / "update_mixed.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])

        result = _run_batch(update)

        assert result["devices_total"] == 2
        assert result["applied"]  == 1, "only field_tech should be applied"
        assert result["rejected"] == 1, "only monitor should be rejected"


# ---------------------------------------------------------------------------
# Test 4 — recommendations key is present on every device result
# ---------------------------------------------------------------------------

class TestRecommendationsInBatchResult:
    """Every device result carries a 'recommendations' key after a batch run."""

    def test_recommendations_key_present_on_all_devices(self, fleet, tmp_path):
        """recommendations key must exist on every entry — pass or fail."""
        update = tmp_path / "update_mixed.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        result = _run_batch(update)

        for dev in result["device_results"]:
            assert "recommendations" in dev, (
                f"'recommendations' key missing for {dev['device_id']}"
            )

    def test_rejected_device_has_non_empty_recommendations(self, fleet, tmp_path):
        """A rejected device must have at least one recommendation."""
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        result = _run_batch(update)

        mon_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-mon-099"
        )
        assert mon_result["status"] == "REJECTED"
        assert len(mon_result["recommendations"]) >= 1

    def test_rejected_recommendation_violation_type_is_capability_boundary(
        self, fleet, tmp_path
    ):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        result = _run_batch(update)

        mon_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-mon-099"
        )
        rec = mon_result["recommendations"][0]
        assert rec["violation_type"] == "capability_boundary"

    def test_passing_device_has_empty_recommendations(self, fleet, tmp_path):
        """A device that passes must have an empty recommendations list."""
        update = tmp_path / "update_clean.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive", "sensitive_data_read"])
        result = _run_batch(update)

        ft_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-ft-001"
        )
        assert ft_result["status"] == "APPLIED"
        assert ft_result["recommendations"] == []

    def test_safe_capability_set_excludes_rejected_cap(self, fleet, tmp_path):
        """safe_capability_set in the recommendation must not include the excess cap."""
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        result = _run_batch(update)

        mon_result = next(
            r for r in result["device_results"] if r["device_id"] == "dev-mon-099"
        )
        rec = mon_result["recommendations"][0]
        assert "update_receive" not in rec["safe_capability_set"]
        assert "telemetry_collect" in rec["safe_capability_set"]


# ---------------------------------------------------------------------------
# Test 5 — rejection sidecar lifecycle
# ---------------------------------------------------------------------------

class TestRejectionSidecarLifecycle:
    """Dispatcher writes sidecar on REJECT and deletes it on APPLY."""

    def test_sidecar_written_on_rejection(self, fleet, tmp_path):
        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "diagnostics_run",
                                     "update_receive"])
        _run_batch(update)

        sidecar = fleet["mon_manifest"].with_suffix(".rejection.json")
        assert sidecar.exists(), "rejection sidecar must be written for rejected device"

    def test_sidecar_not_written_on_pass(self, fleet, tmp_path):
        update = tmp_path / "update_clean.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect"])
        _run_batch(update)

        sidecar = fleet["ft_manifest"].with_suffix(".rejection.json")
        assert not sidecar.exists(), (
            "no rejection sidecar should be written for a passing device"
        )

    def test_sidecar_deleted_when_device_subsequently_passes(self, fleet, tmp_path):
        # First: reject the monitor
        bad_update = tmp_path / "update_bad.yaml"
        _write_update_template(bad_update, version="4.2.0",
                               caps=["telemetry_collect", "update_receive"])
        _run_batch(bad_update)

        sidecar = fleet["mon_manifest"].with_suffix(".rejection.json")
        assert sidecar.exists(), "pre-condition: sidecar must exist after rejection"

        # Second: clean push — monitor passes
        clean_update = tmp_path / "update_clean.yaml"
        _write_update_template(clean_update, version="4.2.0",
                               caps=["telemetry_collect"])
        _run_batch(clean_update)

        assert not sidecar.exists(), (
            "sidecar must be deleted when the device subsequently passes"
        )

    def test_sidecar_content_has_required_keys(self, fleet, tmp_path):
        import json

        update = tmp_path / "update_bad.yaml"
        _write_update_template(update, version="4.2.0",
                               caps=["telemetry_collect", "update_receive"])
        _run_batch(update)

        sidecar = fleet["mon_manifest"].with_suffix(".rejection.json")
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        for key in ("timestamp", "violations", "recommendations"):
            assert key in data, f"sidecar missing key: {key}"
