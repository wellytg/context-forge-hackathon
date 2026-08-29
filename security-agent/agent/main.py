"""
Agent entry point.

Usage:
    security-agent --manifest <path/to/manifest.yaml>

The startup privilege assertion runs BEFORE asyncio or FastAPI are
initialised.  If it fails the process exits with code 1 immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn
from fastapi import FastAPI

from agent.startup import run_startup_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


def build_app(manifest_path: str) -> FastAPI:
    """Build the FastAPI application after the startup assertion passes.

    Importing capability_router is deferred until here so that the privilege
    check always runs first, even in testing scenarios that import this module.
    """
    # Privilege assertion — exits with code 1 on violation.
    manifest = run_startup_check(manifest_path)

    # Deferred import to avoid circular issues and to ensure assertion runs first.
    from agent.capability_router import build_capability_router  # noqa: PLC0415

    app = FastAPI(
        title="Security Agent",
        description="Fleet security agent — capabilities bounded by device owner role.",
        version="0.1.0",
    )

    build_capability_router(app, manifest, manifest_path=manifest_path)

    @app.get("/healthz", tags=["internal"])
    async def health() -> dict:
        return {
            "status": "ok",
            "device_id": manifest.device_id,
            "role": manifest.device_owner_role,
            "fleet_schema_version": manifest.fleet_schema_version,
            "capabilities": sorted(manifest.capability_set),
        }

    return app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Security Agent")
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the device deployment manifest (YAML or TOML).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Bind port (default: 8080).",
    )
    return parser.parse_args()


async def _serve(manifest_path: str, host: str, port: int) -> None:
    app = build_app(manifest_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def cli_entry() -> None:
    """Console script entry point registered in pyproject.toml."""
    args = _parse_args()
    asyncio.run(_serve(args.manifest, args.host, args.port))


if __name__ == "__main__":
    cli_entry()
