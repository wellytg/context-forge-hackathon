"""
Startup privilege assertion.

This module is the FIRST thing called by agent/main.py before any other
initialisation.  It loads the manifest, loads the role map, and verifies that
the manifest's capability_set does not exceed what the declared role permits.

On success  → emits a structured startup_ok log record.
On failure  → emits a structured startup_blocked log record and exits with
              code 1.  The agent process never reaches the application layer.
"""

from __future__ import annotations

import json
import logging
import sys

from manifests.loader import load_manifest
from manifests.schema import AgentManifest
from roles.loader import load_role_map
from roles.validator import PrivilegeViolationError, assert_capabilities_within_role

logger = logging.getLogger(__name__)


def assert_startup_privileges(
    manifest: AgentManifest,
    role_map: dict[str, frozenset[str]],
) -> None:
    """Verify manifest capabilities are within the permitted role boundary.

    Args:
        manifest: Validated AgentManifest loaded from the device manifest file.
        role_map: Authoritative role → capability mapping from role_map.yaml.

    Raises:
        PrivilegeViolationError: If capabilities exceed the role boundary.
            The caller (run_startup_check) handles this by emitting a
            structured log and calling sys.exit(1).
    """
    assert_capabilities_within_role(
        role=manifest.device_owner_role,
        capability_set=manifest.capability_set,
        role_map=role_map,
    )


def run_startup_check(manifest_path: str) -> AgentManifest:
    """Load manifest + role map, assert privileges, and return the manifest.

    This function does NOT return on privilege violation — it exits the process
    with code 1 after emitting a machine-readable startup_blocked record.

    Args:
        manifest_path: Filesystem path to the deployment manifest file.

    Returns:
        The validated AgentManifest (only if the privilege check passes).
    """
    manifest = load_manifest(manifest_path)
    role_map = load_role_map()

    try:
        assert_startup_privileges(manifest, role_map)
    except PrivilegeViolationError as exc:
        record = {
            "event": "startup_blocked",
            "device_id": manifest.device_id,
            "role": manifest.device_owner_role,
            "fleet_schema_version": manifest.fleet_schema_version,
            "reason": str(exc),
        }
        # Use print to stderr so it is always visible regardless of log config.
        print(json.dumps(record), file=sys.stderr)
        sys.exit(1)

    record = {
        "event": "startup_ok",
        "device_id": manifest.device_id,
        "role": manifest.device_owner_role,
        "fleet_schema_version": manifest.fleet_schema_version,
        "environment": manifest.environment,
        "capabilities": sorted(manifest.capability_set),
    }
    print(json.dumps(record))
    logger.info("Startup privilege check passed: %s", record)

    return manifest
