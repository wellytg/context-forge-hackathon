"""
Deploy-gate recommendation engine.

Translates a GateResult + the proposed AgentManifest + the role_map into
structured Recommendation objects — one per violation.  This is a pure
translation layer: no file I/O, no manifest re-parsing, no gating logic.

All facts needed for recommendations are already computed by check_manifest_update:
  - capability_boundary  → set-difference of proposed caps against role_map
  - unsupported_version  → version string not in supported_versions list
  - role_change          → device_owner_role mismatch

The safe_capability_set for a capability violation is derived from:
    sorted(set(proposed.capability_set) & role_map[role])
— a single set intersection using already-loaded data, identical to the
inverse of what the gate already computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deploy_gate.gate import GateResult
    from manifests.schema import AgentManifest


# ---------------------------------------------------------------------------
# Violation type constants — matched by substring against violation strings
# ---------------------------------------------------------------------------

_CAPABILITY_MARKER = "does not permit the following capabilities"
_VERSION_MARKER    = "Unsupported fleet_schema_version"
_ROLE_CHANGE_MARKER = "device_owner_role changed"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """Structured fix-it advice for a single gate violation."""

    violation_type: str
    """One of: 'capability_boundary', 'unsupported_version', 'role_change'."""

    message: str
    """One-sentence human-readable description of the violation."""

    fix: str
    """Concrete action the security engineer should take."""

    safe_capability_set: list[str] | None = field(default=None)
    """For capability_boundary violations: the subset of the proposed
    capability_set that IS permitted for this role.  None for other types."""

    supported_versions: list[str] | None = field(default=None)
    """For unsupported_version violations: the list of accepted versions.
    None for other types."""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_recommendations(
    gate_result: GateResult,
    proposed_manifest: AgentManifest,
    role_map: dict[str, frozenset[str]],
    supported_versions: list[str] | None = None,
) -> list[Recommendation]:
    """Return one Recommendation per violation in gate_result.violations.

    If gate_result.passed is True (no violations) an empty list is returned.

    Args:
        gate_result:        Result from check_manifest_update().
        proposed_manifest:  The AgentManifest that was proposed (already parsed).
        role_map:           Authoritative role → permitted capabilities mapping.
        supported_versions: The version whitelist the dispatcher accepts;
                            only used to populate Recommendation.supported_versions.

    Returns:
        A list of Recommendation objects, one per violation.
    """
    if gate_result.passed:
        return []

    recommendations: list[Recommendation] = []
    role = proposed_manifest.device_owner_role
    proposed_caps = set(proposed_manifest.capability_set)
    permitted = role_map.get(role, frozenset())

    for violation in gate_result.violations:

        # ── Capability boundary ─────────────────────────────────────────────
        if _CAPABILITY_MARKER in violation:
            excess = proposed_caps - permitted
            safe_set = sorted(proposed_caps & permitted)
            # Describe exactly which capabilities are the problem
            excess_sorted = sorted(excess)
            if len(excess_sorted) == 1:
                cap_phrase = f"capability '{excess_sorted[0]}'"
            else:
                quoted = [f"'{c}'" for c in excess_sorted]
                cap_phrase = f"capabilities {', '.join(quoted)}"

            recommendations.append(Recommendation(
                violation_type="capability_boundary",
                message=(
                    f"Role '{role}' does not permit {cap_phrase}. "
                    f"The update cannot be applied to this device as written."
                ),
                fix=(
                    f"Remove {cap_phrase} from the capability_set in the update "
                    f"manifest for '{role}' devices, or target only devices "
                    f"with a role that permits {'it' if len(excess_sorted) == 1 else 'them'}. "
                    f"The safe capability set for this role is: "
                    f"{safe_set if safe_set else '(none of the proposed caps are permitted)'}."
                ),
                safe_capability_set=safe_set,
                supported_versions=None,
            ))

        # ── Unsupported schema version ──────────────────────────────────────
        elif _VERSION_MARKER in violation:
            recommendations.append(Recommendation(
                violation_type="unsupported_version",
                message=(
                    f"fleet_schema_version '{proposed_manifest.fleet_schema_version}' "
                    f"is not accepted by this fleet."
                ),
                fix=(
                    f"Change fleet_schema_version in the update manifest to one of "
                    f"the supported values: {supported_versions}."
                ),
                safe_capability_set=None,
                supported_versions=list(supported_versions) if supported_versions else None,
            ))

        # ── Role change without authorisation ──────────────────────────────
        elif _ROLE_CHANGE_MARKER in violation:
            recommendations.append(Recommendation(
                violation_type="role_change",
                message=(
                    f"The update attempts to change device_owner_role for device "
                    f"'{proposed_manifest.device_id}', which requires explicit authorisation."
                ),
                fix=(
                    "Re-push using the deploy-gate CLI with --allow-role-change and a "
                    "valid DEPLOY_GATE_ADMIN_TOKEN, or restore the original role in the "
                    "update manifest."
                ),
                safe_capability_set=None,
                supported_versions=None,
            ))

        # ── Unknown violation format (forward-compatibility) ────────────────
        else:
            recommendations.append(Recommendation(
                violation_type="unknown",
                message=violation,
                fix="Inspect the violation message and correct the update manifest.",
                safe_capability_set=None,
                supported_versions=None,
            ))

    return recommendations
