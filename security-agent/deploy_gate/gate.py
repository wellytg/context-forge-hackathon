"""
Pre-deploy safety gate — core diff logic.

check_manifest_update() compares a current manifest against a proposed
manifest and returns a GateResult that describes whether the update is safe
to apply.

Rules enforced:
  1. fleet_schema_version of the proposed manifest must be in supported_versions
     (if a supported_versions list is supplied).
  2. device_owner_role must not change unless allow_role_change=True AND the
     caller has been authorised (token check is enforced in the CLI layer).
  3. Every capability in the proposed capability_set must be permitted for the
     proposed role (checked via roles.validator).

This module is imported by both the deploy-gate CLI and the updater module so
there is a single source of truth for gate logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from manifests.loader import load_manifest
from manifests.schema import AgentManifest
from roles.validator import PrivilegeViolationError, assert_capabilities_within_role


@dataclass
class GateResult:
    """Result of a manifest update gate check."""

    passed: bool
    violations: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.passed:
            return "GATE PASS — no violations."
        lines = ["GATE FAIL — violations detected:"]
        for v in self.violations:
            lines.append(f"  • {v}")
        return "\n".join(lines)


def check_manifest_update(
    current_path: str,
    proposed_path: str,
    role_map: dict[str, frozenset[str]],
    supported_versions: list[str] | None = None,
    allow_role_change: bool = False,
) -> GateResult:
    """Diff current vs proposed manifest and return a GateResult.

    Args:
        current_path: Path to the currently deployed manifest file.
        proposed_path: Path to the proposed replacement manifest file.
        role_map: Authoritative role → capability mapping.
        supported_versions: If provided, the proposed fleet_schema_version must
            be in this list.  Pass None to accept any version.
        allow_role_change: If False (default), a change of device_owner_role is
            a violation.  Only set to True after the caller has verified the
            DEPLOY_GATE_ADMIN_TOKEN (enforced in the CLI, not here).

    Returns:
        A GateResult with passed=True and an empty violations list if all
        checks pass, or passed=False with a populated violations list.
    """
    violations: list[str] = []

    current: AgentManifest = load_manifest(current_path)
    proposed: AgentManifest = load_manifest(proposed_path)

    # ── Rule 1: schema version must be supported ──────────────────────────────
    if supported_versions is not None:
        if proposed.fleet_schema_version not in supported_versions:
            violations.append(
                f"Unsupported fleet_schema_version '{proposed.fleet_schema_version}'. "
                f"Supported: {supported_versions}."
            )

    # ── Rule 2: role change requires explicit authorisation ───────────────────
    if proposed.device_owner_role != current.device_owner_role:
        if not allow_role_change:
            violations.append(
                f"device_owner_role changed from '{current.device_owner_role}' to "
                f"'{proposed.device_owner_role}' without --allow-role-change."
            )

    # ── Rule 3: proposed capabilities must be within proposed role ────────────
    try:
        assert_capabilities_within_role(
            role=proposed.device_owner_role,
            capability_set=proposed.capability_set,
            role_map=role_map,
        )
    except PrivilegeViolationError as exc:
        violations.append(str(exc))

    return GateResult(passed=len(violations) == 0, violations=violations)
