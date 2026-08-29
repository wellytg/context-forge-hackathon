"""
Telemetry collection module.

Active when: ``telemetry_collect`` is present in the manifest capability_set.

Collects periodic device telemetry (CPU, memory, disk, network counters) and
exposes an endpoint so the fleet collector can pull a snapshot on demand.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


async def startup() -> None:
    """Called by capability_router after the module is registered."""
    logger.info("Telemetry module started.")


@router.get("/snapshot")
async def get_snapshot() -> dict:
    """Return a telemetry snapshot for this device.

    In production this would read real system metrics.  The stub returns
    representative placeholder values so the schema is exercisable immediately.
    """
    return {
        "module": "telemetry",
        "cpu_percent": 12.4,
        "memory_used_mb": 512,
        "disk_free_gb": 48.2,
        "net_bytes_sent": 1_048_576,
        "net_bytes_recv": 2_097_152,
    }
