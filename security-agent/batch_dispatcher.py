"""
Batch Push Dispatcher -- localhost:8743
========================================

Security engineer pushes a universal update to this server.  The agent loops
through every device in batch_targets.yaml, runs the existing deploy-gate
check_manifest_update() for each one, and applies the update only to devices
that PASS.  Devices that FAIL are rejected with a clear violation report.

IMPORTANT: No privilege-check logic lives here.  All gating is delegated to:
  - deploy_gate.gate.check_manifest_update()  -- orchestrates the three rules
  - roles.validator.assert_capabilities_within_role() -- the actual privilege check

Run:
    cd security-agent
    python batch_dispatcher.py

Endpoints:
    POST /push   multipart/form-data  field: "update_file" (.yaml)
    GET  /health
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# Make sure the package root is on sys.path when run directly
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from deploy_gate.gate import GateResult, check_manifest_update  # noqa: E402
from roles.loader import load_role_map  # noqa: E402

# Constants
HOST = "127.0.0.1"          # localhost-only -- never expose to a network interface
PORT = 8743                  # unprivileged dynamic range (8192-49151)
BATCH_TARGETS_PATH = _HERE / "batch_targets.yaml"
SUPPORTED_VERSIONS = ["4.2.0", "4.2.1", "4.2.2"]  # versions the fleet accepts

app = FastAPI(
    title="Batch Push Dispatcher",
    description="Security-agent deploy-gate batch simulation",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(char: str = "-", width: int = 64) -> str:
    return char * width


def _load_batch_targets() -> list[dict[str, str]]:
    """Load device list from batch_targets.yaml."""
    with BATCH_TARGETS_PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    targets = raw.get("batch", [])
    if not targets:
        raise ValueError("batch_targets.yaml contains no entries under 'batch:'")
    return targets


def _build_per_device_manifest(
    template_path: Path,
    device_id: str,
    role: str,
    staging_dir: Path,
) -> Path:
    """Clone the universal update template, stamping in the device's own id/role.

    The universal update YAML uses placeholder values for device_id and
    device_owner_role.  We stamp in the real values so the gate compares
    the same role against the proposed capability_set.

    Returns the path to the stamped manifest inside staging_dir.
    """
    with template_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    data["device_id"] = device_id
    data["device_owner_role"] = role

    out_path = staging_dir / f"proposed_{device_id}.yaml"
    with out_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)

    return out_path


def _apply_update(proposed: Path, current: Path) -> None:
    """Atomically replace the current manifest with the proposed one."""
    tmp = current.with_suffix(".tmp")
    shutil.copy2(str(proposed), str(tmp))
    os.replace(str(tmp), str(current))


def _run_batch(update_template_path: Path) -> dict[str, Any]:
    """Core batch loop -- runs the gate for every target device.

    Returns a structured result dict with per-device outcomes and a summary.
    """
    role_map = load_role_map()        # loaded ONCE for the whole batch
    targets = _load_batch_targets()

    results: list[dict[str, Any]] = []

    print()
    print(_sep("="))
    print("  BATCH PUSH DISPATCH -- Security Agent Deploy-Gate")
    print(_sep("="))
    print(f"  Update template : {update_template_path.name}")
    print(f"  Fleet targets   : {len(targets)} device(s)")
    print(f"  Supported vers  : {SUPPORTED_VERSIONS}")
    print(_sep("-"))

    with tempfile.TemporaryDirectory() as tmp_dir:
        staging = Path(tmp_dir)

        for entry in targets:
            device_id = entry["device_id"]
            role      = entry["role"]
            current   = _HERE / entry["current_manifest"]

            print(f"\n  >>  {device_id}  [{role}]")
            print(f"      current manifest : {entry['current_manifest']}")

            # Stamp device-specific values into the universal template
            proposed = _build_per_device_manifest(
                template_path=update_template_path,
                device_id=device_id,
                role=role,
                staging_dir=staging,
            )

            # -- Run the existing gate -- NO duplicate logic here --
            gate_result: GateResult = check_manifest_update(
                current_path=str(current),
                proposed_path=str(proposed),
                role_map=role_map,
                supported_versions=SUPPORTED_VERSIONS,
                allow_role_change=False,
            )

            if gate_result.passed:
                # PASS: apply update atomically
                _apply_update(proposed, current)
                status = "APPLIED"

                print(f"      gate result      : [PASS]")
                print(f"      action           : UPDATE APPLIED -> {entry['current_manifest']}")

            else:
                # FAIL: reject -- never touch the current manifest
                status = "REJECTED"

                print(f"      gate result      : [FAIL]")
                for v in gate_result.violations:
                    print(f"      violation        : {v}")
                print(f"      action           : UPDATE BLOCKED -- device unchanged")

            results.append(
                {
                    "device_id": device_id,
                    "role":      role,
                    "result":    "PASS" if gate_result.passed else "FAIL",
                    "status":    status,
                    "violations": gate_result.violations,
                }
            )

    # Summary table
    print()
    print(_sep("="))
    print("  FINAL SUMMARY")
    print(_sep("-"))
    print(f"  {'DEVICE ID':<28} {'ROLE':<14} {'RESULT':<8}  ACTION")
    print(_sep("-"))
    for r in results:
        marker = "[PASS]" if r["result"] == "PASS" else "[FAIL]"
        print(
            f"  {r['device_id']:<28} {r['role']:<14} "
            f"{marker:<8}  {r['status']}"
        )
    print(_sep("="))

    applied  = sum(1 for r in results if r["status"] == "APPLIED")
    rejected = sum(1 for r in results if r["status"] == "REJECTED")
    print(f"  Applied: {applied}   Rejected: {rejected}   Total: {len(results)}")
    print(_sep("="))
    print()

    return {
        "update_file":    update_template_path.name,
        "devices_total":  len(results),
        "applied":        applied,
        "rejected":       rejected,
        "device_results": results,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check -- confirm the dispatcher is running."""
    return {"status": "ok", "service": "batch-push-dispatcher", "port": str(PORT)}


@app.post("/push")
async def push_update(update_file: UploadFile) -> JSONResponse:
    """Accept a universal update YAML and run the deploy-gate batch simulation.

    The security engineer uploads a single update manifest.  The dispatcher
    stamps each device's real role/id into a copy, runs the existing gate,
    and returns the full per-device result set.

    Form field:
        update_file  -- the .yaml universal update manifest

    Returns HTTP 200 with the batch result JSON even when some devices are
    rejected, so the caller always gets the full picture.
    """
    if not update_file.filename or not update_file.filename.endswith(".yaml"):
        raise HTTPException(
            status_code=400,
            detail="update_file must be a .yaml file",
        )

    # Write the uploaded template to a temp file
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", delete=False, dir=tempfile.gettempdir()
    ) as tmp_fh:
        tmp_path = Path(tmp_fh.name)
        tmp_path.write_bytes(await update_file.read())

    try:
        batch_result = _run_batch(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return JSONResponse(content=batch_result, status_code=200)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[batch-dispatcher] Starting on http://{HOST}:{PORT}")
    print(f"[batch-dispatcher] Batch targets: {BATCH_TARGETS_PATH}")
    print("[batch-dispatcher] Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
