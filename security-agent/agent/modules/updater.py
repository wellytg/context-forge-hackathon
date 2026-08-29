"""
Update receiver module.

Active when: ``update_receive`` is present in the manifest capability_set.

Accepts a new manifest file upload, runs the pre-deploy gate check, and if
the gate passes atomically replaces the active manifest then exits cleanly so
the process supervisor can restart the agent (re-triggering the startup
privilege assertion on the new manifest).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["update"])

# Populated by capability_router when wiring the module.
_active_manifest_path: str | None = None


def set_active_manifest_path(path: str) -> None:
    """Called by capability_router so the updater knows where the live manifest lives."""
    global _active_manifest_path
    _active_manifest_path = path


async def startup() -> None:
    """Called by capability_router after the module is registered."""
    logger.info("Update receiver module started. Active manifest: %s", _active_manifest_path)


async def apply_update(
    new_manifest_path: str | Path,
    current_manifest_path: str | Path,
) -> dict:
    """Gate-check and atomically apply a new manifest.

    Steps:
    1. Run the pre-deploy gate (same logic as the deploy-gate CLI).
    2. On gate failure — log a structured rejection record and return it.
       The active manifest is NEVER touched.
    3. On gate pass — atomically replace the active manifest, emit an
       update_applied record, then call sys.exit(0) so the process supervisor
       restarts the agent and re-runs the startup assertion.

    Args:
        new_manifest_path: Path to the proposed manifest (already written to
            a staging location by the caller).
        current_manifest_path: Path to the currently active manifest.

    Returns:
        A dict describing the outcome (used in tests and the HTTP response
        before sys.exit is called on success).
    """
    # Deferred import — deploy_gate must not be imported at module level to
    # avoid circular dependencies during startup.
    from deploy_gate.gate import check_manifest_update  # noqa: PLC0415
    from roles.loader import load_role_map  # noqa: PLC0415

    role_map = load_role_map()
    result = check_manifest_update(
        current_path=str(current_manifest_path),
        proposed_path=str(new_manifest_path),
        role_map=role_map,
        supported_versions=None,   # accept any version; set to a list to restrict
        allow_role_change=False,
    )

    if not result.passed:
        record = {
            "event": "update_rejected",
            "violations": result.violations,
        }
        logger.warning("Update rejected: %s", record)
        return record

    # Atomic replace: write to .tmp then rename so readers never see a
    # half-written manifest.
    tmp = Path(str(current_manifest_path) + ".tmp")
    shutil.copy2(str(new_manifest_path), str(tmp))
    os.replace(str(tmp), str(current_manifest_path))

    record = {
        "event": "update_applied",
        "new_manifest": str(current_manifest_path),
    }
    logger.info("Update applied: %s — restarting agent.", record)
    # Exit 0 so the process supervisor (systemd Restart=on-success) restarts
    # the agent, which re-runs the startup privilege assertion.
    sys.exit(0)


@router.post("/apply")
async def post_apply_update(file: UploadFile) -> dict:
    """Accept a manifest file upload and apply it via the gate.

    The uploaded file is written to a staging path, gated, and if approved
    applied atomically.  The endpoint only returns a response on rejection —
    on success the process exits before the response can be sent, and the
    supervisor restart makes the agent available again within seconds.
    """
    if _active_manifest_path is None:
        raise HTTPException(status_code=500, detail="Active manifest path not configured.")

    staging_path = Path(_active_manifest_path).with_suffix(".staged")
    try:
        content = await file.read()
        staging_path.write_bytes(content)
        result = await apply_update(staging_path, _active_manifest_path)
        return result
    finally:
        if staging_path.exists():
            staging_path.unlink(missing_ok=True)
