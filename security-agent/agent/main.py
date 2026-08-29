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
import datetime
import json
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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

    @app.get("/status", tags=["internal"])
    async def status() -> dict:
        """Return current agent state from the already-loaded manifest.

        manifest_last_modified is read from the manifest file's mtime at
        request time — it reflects the timestamp of whatever file is currently
        on disk (post-swap or original) without re-parsing the manifest.

        last_rejection is read from the sidecar file written by the batch
        dispatcher when this device's update was rejected.  It is null if no
        rejection sidecar exists (device never rejected, or last push passed).
        """
        mtime = os.path.getmtime(manifest_path)
        last_modified = datetime.datetime.fromtimestamp(
            mtime, tz=datetime.timezone.utc
        ).isoformat()

        # Read the rejection sidecar if present (written by batch_dispatcher)
        sidecar = Path(manifest_path).with_suffix(".rejection.json")
        last_rejection = None
        if sidecar.exists():
            try:
                last_rejection = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                last_rejection = None

        return {
            "device_id": manifest.device_id,
            "role": manifest.device_owner_role,
            "fleet_schema_version": manifest.fleet_schema_version,
            "capability_set": sorted(manifest.capability_set),
            "manifest_path": manifest_path,
            "manifest_last_modified": last_modified,
            "last_rejection": last_rejection,
        }

    @app.get("/", response_class=HTMLResponse, tags=["internal"])
    async def dashboard() -> str:
        """Minimal human-readable status page; auto-refreshes every 3 s."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Agent Status</title>
  <style>
    body{font-family:system-ui,sans-serif;max-width:740px;margin:2rem auto;padding:0 1rem;background:#f7f8fa;color:#1f2328}
    h1{font-size:2rem;margin-bottom:.25rem}
    h2{font-size:1rem;color:#57606a;margin-top:0;font-weight:normal}
    .badges{display:flex;flex-wrap:wrap;gap:.4rem;margin:.75rem 0}
    .badge{background:#e5e7eb;border-radius:4px;padding:.2rem .6rem;font-size:.875rem;font-family:monospace}
    .meta{margin-top:1.5rem;font-size:.875rem;color:#57606a}
    .meta span{color:#1f2328;font-family:monospace}
    #err{color:#b91c1c;font-size:.8rem;margin-top:.5rem}
    #rejection-banner{display:none;margin-top:1.25rem;padding:.75rem 1rem;
      border:1.5px solid #f87171;border-radius:6px;background:#fef2f2;color:#7f1d1d;font-size:.875rem}
    #rejection-banner strong{display:block;margin-bottom:.35rem;font-size:.95rem}
    #rejection-fix{margin-top:.4rem;color:#1f2328;font-style:italic}
    #rejection-ts{font-size:.78rem;color:#57606a;margin-top:.3rem}
    .ok-badge{display:none;margin-top:1.25rem;padding:.4rem .9rem;border-radius:4px;
      background:#dcfce7;color:#14532d;font-size:.85rem;border:1px solid #86efac}
  </style>
</head>
<body>
  <h1 id="device-id">…</h1>
  <h2 id="role">…</h2>
  <div class="badges" id="caps"></div>
  <div class="meta">
    <div>Fleet schema version: <span id="fsv">…</span></div>
    <div>Manifest path: <span id="mp">…</span></div>
    <div>Manifest last modified: <span id="mlm">…</span></div>
  </div>
  <div id="rejection-banner">
    <strong>&#9888; BLOCKED — last push was rejected for this device</strong>
    <div id="rejection-violations"></div>
    <div id="rejection-fix"></div>
    <div id="rejection-ts"></div>
  </div>
  <div class="ok-badge" id="ok-badge">&#10003; No pending rejections — manifest is current</div>
  <div id="err"></div>
  <script>
    function refresh(){
      fetch('/status').then(r=>r.json()).then(d=>{
        document.getElementById('device-id').textContent=d.device_id;
        document.getElementById('role').textContent=d.role;
        document.getElementById('fsv').textContent=d.fleet_schema_version;
        document.getElementById('mp').textContent=d.manifest_path;
        document.getElementById('mlm').textContent=d.manifest_last_modified;
        var caps=document.getElementById('caps');
        caps.innerHTML='';
        (d.capability_set||[]).forEach(function(c){
          var b=document.createElement('span');
          b.className='badge';b.textContent=c;caps.appendChild(b);
        });
        var banner=document.getElementById('rejection-banner');
        var okBadge=document.getElementById('ok-badge');
        if(d.last_rejection){
          var rej=d.last_rejection;
          document.getElementById('rejection-violations').textContent=
            (rej.violations||[]).join(' | ');
          var fix='';
          if(rej.recommendations&&rej.recommendations.length>0){
            fix='Fix: '+rej.recommendations[0].fix;
          }
          document.getElementById('rejection-fix').textContent=fix;
          document.getElementById('rejection-ts').textContent=
            'Rejected at: '+(rej.timestamp||'unknown');
          banner.style.display='block';
          okBadge.style.display='none';
        } else {
          banner.style.display='none';
          okBadge.style.display='block';
        }
        document.getElementById('err').textContent='';
      }).catch(function(e){
        document.getElementById('err').textContent='fetch error: '+e;
      });
    }
    refresh();
    setInterval(refresh,3000);
  </script>
</body>
</html>"""

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
