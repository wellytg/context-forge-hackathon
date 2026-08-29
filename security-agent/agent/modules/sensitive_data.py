"""
Sensitive data access module.

Active when: ``sensitive_data_read`` is present in the manifest capability_set.

Provides field-level read access to sensitive device data (e.g. certificates,
secrets metadata, compliance markers).  Never exposes raw secrets — only
metadata and redacted summaries.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sensitive", tags=["sensitive_data"])


async def startup() -> None:
    """Called by capability_router after the module is registered."""
    logger.info("Sensitive data module started.")


@router.get("/metadata")
async def get_metadata() -> dict:
    """Return redacted metadata about sensitive fields on this device.

    Stub — in production this would read from a local secure store.
    Raw secret values are NEVER returned by this endpoint.
    """
    return {
        "module": "sensitive_data",
        "cert_expiry_days": 87,
        "compliance_status": "pass",
        "last_rotated": "2025-06-01",
    }
