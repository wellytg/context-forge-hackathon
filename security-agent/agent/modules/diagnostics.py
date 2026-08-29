"""
Diagnostics module.

Active when: ``diagnostics_run`` is present in the manifest capability_set.

Runs lightweight device health checks (connectivity, process health, log
tail) and returns a structured diagnostics report.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


async def startup() -> None:
    """Called by capability_router after the module is registered."""
    logger.info("Diagnostics module started.")


@router.get("/report")
async def get_report() -> dict:
    """Return a diagnostics report for this device.

    Stub — in production this would run real health checks.
    """
    return {
        "module": "diagnostics",
        "connectivity": "ok",
        "agent_process": "healthy",
        "last_log_lines": [],
    }
