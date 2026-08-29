"""
Role-based capability validator.

Provides a single function that asserts an agent's capability_set does not
exceed the maximum permissions allowed for its declared device_owner_role.
"""

from __future__ import annotations


class PrivilegeViolationError(Exception):
    """Raised when an agent manifest declares capabilities beyond its role.

    Attributes:
        role: The role that was checked.
        excess_capabilities: Capabilities present in the manifest but not
            permitted by the role.
    """

    def __init__(self, role: str, excess_capabilities: set[str]) -> None:
        self.role = role
        self.excess_capabilities = excess_capabilities
        sorted_excess = sorted(excess_capabilities)
        super().__init__(
            f"Role '{role}' does not permit the following capabilities: {sorted_excess}. "
            "Remove them from capability_set or use a role that allows them."
        )


def assert_capabilities_within_role(
    role: str,
    capability_set: list[str] | set[str] | frozenset[str],
    role_map: dict[str, frozenset[str]],
) -> None:
    """Assert that every capability in capability_set is permitted for role.

    Args:
        role: The device_owner_role declared in the manifest.
        capability_set: The capabilities declared in the manifest.
        role_map: Mapping of role name → permitted capabilities, as returned
            by ``roles.loader.load_role_map()``.

    Raises:
        PrivilegeViolationError: If any capability is not permitted for the
            role, or if the role is not defined in role_map.
    """
    if role not in role_map:
        raise PrivilegeViolationError(
            role=role,
            excess_capabilities=set(capability_set),
        )

    permitted = role_map[role]
    requested = set(capability_set)
    excess = requested - permitted

    if excess:
        raise PrivilegeViolationError(role=role, excess_capabilities=excess)
