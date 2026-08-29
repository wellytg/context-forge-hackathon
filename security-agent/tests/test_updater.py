"""
Tests for agent/modules/updater.py — apply_update.

The updater calls the gate and, on pass, atomically replaces the manifest
then calls sys.exit(0).  On failure it returns a rejection record without
touching the active manifest.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest


def _write_manifest(path: Path, *, role: str, caps: list[str], version: str = "4.2") -> None:
    lines = [
        f"fleet_schema_version: '{version}'",
        "device_id: 'test-device'",
        f"device_owner_role: {role}",
        "capability_set:",
    ]
    for c in caps:
        lines.append(f"  - {c}")
    lines.append("environment: production")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestApplyUpdate:
    def test_valid_update_applies_and_exits_0(self, tmp_path):
        """A gate-passing update atomically replaces the manifest and exits 0."""
        current = tmp_path / "manifest.yaml"
        proposed = tmp_path / "proposed.yaml"

        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        # Proposed is still within read_only permissions.
        _write_manifest(proposed, role="read_only", caps=["telemetry_collect"])

        from agent.modules.updater import apply_update

        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(apply_update(proposed, current))

        assert exc_info.value.code == 0
        # Active manifest should now contain the proposed content.
        assert current.read_text() == proposed.read_text()

    def test_privilege_escalation_rejected_and_manifest_untouched(self, tmp_path):
        """A gate-failing update is rejected; the active manifest is unchanged."""
        current = tmp_path / "manifest.yaml"
        proposed = tmp_path / "proposed.yaml"

        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        original_content = current.read_text()

        # Proposed requests sensitive_data_read — not permitted for read_only.
        _write_manifest(
            proposed,
            role="read_only",
            caps=["telemetry_collect", "sensitive_data_read"],
        )

        from agent.modules.updater import apply_update

        result = asyncio.run(apply_update(proposed, current))

        assert result["event"] == "update_rejected"
        assert len(result["violations"]) > 0
        # Active manifest must be untouched.
        assert current.read_text() == original_content

    def test_role_change_without_flag_rejected(self, tmp_path):
        """Role change without allow_role_change=True is rejected."""
        current = tmp_path / "manifest.yaml"
        proposed = tmp_path / "proposed.yaml"

        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        _write_manifest(
            proposed,
            role="monitor",
            caps=["telemetry_collect", "diagnostics_run"],
        )

        from agent.modules.updater import apply_update

        result = asyncio.run(apply_update(proposed, current))
        assert result["event"] == "update_rejected"

    def test_staging_file_cleaned_up_after_rejection(self, tmp_path):
        """No .staged or .tmp artefacts left behind after a rejected update."""
        current = tmp_path / "manifest.yaml"
        proposed = tmp_path / "staged.yaml"

        _write_manifest(current, role="read_only", caps=["telemetry_collect"])
        _write_manifest(proposed, role="read_only", caps=["telemetry_collect", "sensitive_data_read"])

        from agent.modules.updater import apply_update

        asyncio.run(apply_update(proposed, current))
        # No .tmp file should remain.
        assert not (tmp_path / "manifest.yaml.tmp").exists()
