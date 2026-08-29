"""
Capability router.

Inspects the validated manifest's capability_set and registers ONLY the
FastAPI routers for capabilities that are explicitly listed.  Routes for
capabilities not in the manifest simply do not exist in the running process.

This is a second enforcement layer on top of the startup privilege assertion:
even if a manifest is somehow misconfigured, uncapped routes are unreachable.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from manifests.schema import AgentManifest

logger = logging.getLogger(__name__)

# Maps capability identifier → (module_path, router_attr, startup_attr)
# Using lazy string-based imports so the modules are only imported when
# their capability is present in the manifest.
_CAPABILITY_MODULE_MAP: dict[str, str] = {
    "telemetry_collect": "agent.modules.telemetry",
    "diagnostics_run": "agent.modules.diagnostics",
    "update_receive": "agent.modules.updater",
    "sensitive_data_read": "agent.modules.sensitive_data",
}


def build_capability_router(
    app: FastAPI,
    manifest: AgentManifest,
    manifest_path: str = "",
) -> None:
    """Register only the routers permitted by the manifest capability_set.

    For each capability in the manifest:
      - Import the corresponding module.
      - Include its ``router`` in the FastAPI app.
      - Call its async ``startup()`` hook via an ``on_startup`` event.

    Capabilities NOT in the manifest are logged as skipped and their modules
    are never imported — their routes do not exist at runtime.

    Args:
        app: The FastAPI application instance to register routes on.
        manifest: The validated, privilege-checked AgentManifest.
        manifest_path: Filesystem path to the active manifest file, passed to
            the updater module so it knows where to atomically replace on update.
    """
    active_caps = set(manifest.capability_set)

    for capability, module_path in _CAPABILITY_MODULE_MAP.items():
        if capability not in active_caps:
            logger.info("Capability '%s' not in manifest — module skipped.", capability)
            continue

        # Dynamic import — module only loaded if capability is granted.
        import importlib  # noqa: PLC0415
        module = importlib.import_module(module_path)

        app.include_router(module.router)
        logger.info("Capability '%s' registered (router: %s).", capability, module_path)

        # Wire the updater's active manifest path so it knows where to write.
        if capability == "update_receive" and hasattr(module, "set_active_manifest_path"):
            module.set_active_manifest_path(manifest_path)

        # Register the module's startup hook.
        app.add_event_handler("startup", module.startup)

    logger.info(
        "Capability routing complete. Active: %s. Skipped: %s.",
        sorted(active_caps),
        sorted(set(_CAPABILITY_MODULE_MAP) - active_caps),
    )
