"""
Edge-case update scenarios — gate and CLI audit tool integration
================================================================

Each test in this module loads one of the edge-case update YAML files from
``updates/`` and drives it through the same two-gate chain that a real workflow
would exercise:

  1. ``deploy_gate.gate.check_manifest_update()``  — the core programmatic gate
     (imported directly, the way the batch dispatcher calls it).
  2. ``deploy_gate.cli.check`` via Typer's ``CliRunner``  — the CLI audit tool
     (the way an automated workflow / CI step calls ``deploy-gate check``).

This dual-path approach lets you verify that the edge-case file alone is
sufficient input for the audit tool; no bespoke fixture wiring is needed beyond
a temporary "current" manifest to diff against.

Edge-case files covered
-----------------------
update_edge_bad_version.yaml     — unsupported fleet_schema_version (9.9.9)
update_edge_corrupted_yaml.yaml  — tab-indented YAML (syntax error)
update_edge_downgrade.yaml       — capability reduction (must PASS)
update_edge_duplicate_caps.yaml  — duplicate entries in capability_set
update_edge_empty_caps.yaml      — empty capability_set
update_edge_role_escalation.yaml — read_only device requesting field_tech caps
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Ensure the package root is on sys.path when pytest is run from the repo root
# ---------------------------------------------------------------------------
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from deploy_gate.cli import app                              # noqa: E402
from deploy_gate.gate import check_manifest_update           # noqa: E402
from manifests.loader import load_manifest                   # noqa: E402
from roles.loader import load_role_map                       # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_UPDATES_DIR = _PKG_ROOT / "updates"
_SUPPORTED_VERSIONS = ["4.2.0", "4.2.1", "4.2.2"]

_ROLE_MAP = {
    "field_tech": frozenset(
        ["telemetry_collect", "diagnostics_run", "update_receive", "sensitive_data_read"]
    ),
    "monitor":    frozenset(["telemetry_collect", "diagnostics_run"]),
    "read_only":  frozenset(["telemetry_collect"]),
}

_CLI_RUNNER = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _stamp_update(update_file: Path, tmp_dir: Path, *, device_id: str, role: str) -> Path:
    """Return a tmp copy of *update_file* with placeholders filled in.

    Mirrors the logic in batch_dispatcher._build_per_device_manifest() so the
    gate receives a fully valid per-device manifest rather than one still
    containing __BATCH_PLACEHOLDER__ strings.
    """
    raw = update_file.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    data["device_id"] = device_id
    data["device_owner_role"] = role
    out = tmp_dir / f"proposed_{device_id}.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def _write_current(tmp_dir: Path, *, role: str, caps: list[str],
                   version: str = "4.2.0") -> Path:
    """Write a minimal 'current' manifest for diffing against the proposed one."""
    data = {
        "fleet_schema_version": version,
        "device_id": f"dev-current-{role}",
        "device_owner_role": role,
        "capability_set": caps,
        "environment": "production",
    }
    out = tmp_dir / f"current_{role}.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def _invoke_audit_cli(current: Path, proposed: Path,
                      extra_args: list[str] | None = None) -> "Result":
    """Call the ``deploy-gate check`` CLI (the audit tool) via Typer's runner.

    This is the canonical way a workflow or CI step would call the audit tool:

        deploy-gate check --current <path> --proposed <path> \\
                          --supported-version 4.2.0          \\
                          --supported-version 4.2.1          \\
                          --output json

    Returns the Typer CliRunner Result so callers can inspect exit_code and
    stdout.
    """
    args = [
        "--current", str(current),
        "--proposed", str(proposed),
        "--supported-version", "4.2.0",
        "--supported-version", "4.2.1",
        "--supported-version", "4.2.2",
        "--output", "json",
    ]
    if extra_args:
        args.extend(extra_args)
    return _CLI_RUNNER.invoke(app, args)


# ---------------------------------------------------------------------------
# 1. update_edge_bad_version.yaml — unsupported fleet_schema_version
# ---------------------------------------------------------------------------

class TestEdgeBadVersion:
    """Unsupported fleet_schema_version (9.9.9) must block at the gate."""

    _UPDATE = _UPDATES_DIR / "update_edge_bad_version.yaml"

    def test_gate_rejects_bad_version(self, tmp_path):
        """Gate programmatic path: version not in supported list → FAIL."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-bad-ver", role="read_only")
        result = check_manifest_update(
            str(current), str(proposed), _ROLE_MAP,
            supported_versions=_SUPPORTED_VERSIONS,
        )
        assert not result.passed
        assert any("9.9.9" in v for v in result.violations)

    def test_audit_cli_exits_1_bad_version(self, tmp_path):
        """CLI audit tool: exit code 1 (FAIL) for unsupported version."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-bad-ver", role="read_only")
        r = _invoke_audit_cli(current, proposed)
        assert r.exit_code == 1
        import json
        payload = json.loads(r.stdout)
        assert payload["passed"] is False
        assert any("9.9.9" in v for v in payload["violations"])


# ---------------------------------------------------------------------------
# 2. update_edge_corrupted_yaml.yaml — tab-indented YAML (syntax error)
# ---------------------------------------------------------------------------

class TestEdgeCorruptedYaml:
    """A tab-indented YAML file must be caught before reaching the gate."""

    _UPDATE = _UPDATES_DIR / "update_edge_corrupted_yaml.yaml"

    def test_yaml_load_raises_yaml_error(self):
        """The corrupted file raises yaml.YAMLError on safe_load, not silently."""
        raw = self._UPDATE.read_text(encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(raw)

    def test_audit_cli_reports_error_on_corrupted_yaml(self, tmp_path):
        """CLI audit tool: attempting to load a corrupted proposed file raises."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        # Pass the raw corrupted file directly as the proposed path — the CLI
        # will attempt to parse it via manifests.loader.load_manifest and must
        # surface an error rather than crash silently.
        r = _invoke_audit_cli(current, self._UPDATE)
        # The CLI may exit with any non-zero code; the key assertion is that it
        # does NOT exit 0 (gate must not pass a file that cannot be parsed).
        assert r.exit_code != 0


# ---------------------------------------------------------------------------
# 3. update_edge_downgrade.yaml — capability reduction (must PASS)
# ---------------------------------------------------------------------------

class TestEdgeDowngrade:
    """A capability reduction is always safe — gate must PASS."""

    _UPDATE = _UPDATES_DIR / "update_edge_downgrade.yaml"

    def test_gate_passes_downgrade(self, tmp_path):
        """Gate programmatic path: capability reduction → PASS."""
        # Current has more caps than the proposed update
        current = _write_current(tmp_path, role="monitor",
                                 caps=["telemetry_collect", "diagnostics_run"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-downgrade", role="monitor")
        result = check_manifest_update(
            str(current), str(proposed), _ROLE_MAP,
            supported_versions=_SUPPORTED_VERSIONS,
        )
        assert result.passed
        assert result.violations == []

    def test_audit_cli_exits_0_downgrade(self, tmp_path):
        """CLI audit tool: exit code 0 (PASS) for a capability downgrade."""
        current = _write_current(tmp_path, role="monitor",
                                 caps=["telemetry_collect", "diagnostics_run"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-downgrade", role="monitor")
        r = _invoke_audit_cli(current, proposed)
        assert r.exit_code == 0
        import json
        payload = json.loads(r.stdout)
        assert payload["passed"] is True


# ---------------------------------------------------------------------------
# 4. update_edge_duplicate_caps.yaml — duplicate entries in capability_set
# ---------------------------------------------------------------------------

class TestEdgeDuplicateCaps:
    """Duplicate capabilities must be caught by Pydantic schema validation."""

    _UPDATE = _UPDATES_DIR / "update_edge_duplicate_caps.yaml"

    def test_load_manifest_raises_on_duplicate_caps(self, tmp_path):
        """manifests.loader.load_manifest raises ValidationError for duplicates."""
        from pydantic import ValidationError
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-dupes", role="monitor")
        with pytest.raises(ValidationError):
            load_manifest(str(proposed))

    def test_audit_cli_exits_nonzero_on_duplicate_caps(self, tmp_path):
        """CLI audit tool: exits non-zero when proposed manifest has duplicate caps."""
        current = _write_current(tmp_path, role="monitor",
                                 caps=["telemetry_collect", "diagnostics_run"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-dupes", role="monitor")
        r = _invoke_audit_cli(current, proposed)
        assert r.exit_code != 0


# ---------------------------------------------------------------------------
# 5. update_edge_empty_caps.yaml — empty capability_set
# ---------------------------------------------------------------------------

class TestEdgeEmptyCaps:
    """An empty capability_set violates the AgentManifest non-empty constraint."""

    _UPDATE = _UPDATES_DIR / "update_edge_empty_caps.yaml"

    def test_load_manifest_raises_on_empty_caps(self, tmp_path):
        """manifests.loader.load_manifest raises ValidationError for empty caps."""
        from pydantic import ValidationError
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-empty", role="read_only")
        with pytest.raises(ValidationError):
            load_manifest(str(proposed))

    def test_audit_cli_exits_nonzero_on_empty_caps(self, tmp_path):
        """CLI audit tool: exits non-zero when proposed manifest has empty caps."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-empty", role="read_only")
        r = _invoke_audit_cli(current, proposed)
        assert r.exit_code != 0


# ---------------------------------------------------------------------------
# 6. update_edge_role_escalation.yaml — role escalation without admin token
# ---------------------------------------------------------------------------

class TestEdgeRoleEscalation:
    """read_only → field_tech escalation without --allow-role-change must FAIL."""

    _UPDATE = _UPDATES_DIR / "update_edge_role_escalation.yaml"

    def test_gate_blocks_role_escalation(self, tmp_path):
        """Gate programmatic path: role change without flag → FAIL."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-escalate", role="field_tech")
        result = check_manifest_update(
            str(current), str(proposed), _ROLE_MAP,
            supported_versions=_SUPPORTED_VERSIONS,
        )
        assert not result.passed
        assert any("device_owner_role" in v for v in result.violations)

    def test_gate_blocks_excess_caps_even_with_correct_role(self, tmp_path):
        """When the device is already field_tech, the caps still pass — they are
        within field_tech's boundary, so the gate should PASS here (the
        escalation scenario is the role change, not the caps themselves)."""
        current = _write_current(tmp_path, role="field_tech",
                                 caps=["telemetry_collect", "diagnostics_run"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-field-upgrade", role="field_tech")
        result = check_manifest_update(
            str(current), str(proposed), _ROLE_MAP,
            supported_versions=_SUPPORTED_VERSIONS,
        )
        # All proposed caps are within field_tech — same role, valid caps → PASS
        assert result.passed

    def test_audit_cli_exits_1_role_escalation(self, tmp_path):
        """CLI audit tool: exit code 1 (FAIL) for unauthorised role escalation."""
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-escalate", role="field_tech")
        r = _invoke_audit_cli(current, proposed)
        assert r.exit_code == 1
        import json
        payload = json.loads(r.stdout)
        assert payload["passed"] is False
        assert any("device_owner_role" in v for v in payload["violations"])

    def test_audit_cli_exits_2_without_admin_token(self, tmp_path, monkeypatch):
        """CLI audit tool: exit code 2 when --allow-role-change is passed but
        DEPLOY_GATE_ADMIN_TOKEN is not set in the environment."""
        monkeypatch.delenv("DEPLOY_GATE_ADMIN_TOKEN", raising=False)
        current = _write_current(tmp_path, role="read_only", caps=["telemetry_collect"])
        proposed = _stamp_update(self._UPDATE, tmp_path,
                                 device_id="dev-escalate", role="field_tech")
        r = _invoke_audit_cli(current, proposed, extra_args=["--allow-role-change"])
        assert r.exit_code == 2
