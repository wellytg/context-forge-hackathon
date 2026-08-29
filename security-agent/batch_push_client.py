"""
Batch Push Client -- Security Engineer Simulation
==================================================

Simulates the security engineer running a universal update push against the
batch dispatcher running on localhost:8743.

Usage:
    cd security-agent
    python batch_push_client.py [path/to/update.yaml]

If no path is given it defaults to:  updates/update_batch_demo.yaml

The client uploads the update file to the dispatcher's /push endpoint and
prints the per-device result table returned by the server.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("[error] 'requests' is not installed.  Run:  pip install requests")
    sys.exit(1)

# Config
HOST = "127.0.0.1"
PORT = 8743
BASE_URL = f"http://{HOST}:{PORT}"

_DEFAULT_UPDATE = Path(__file__).resolve().parent / "updates" / "update_batch_demo.yaml"


def _sep(char: str = "-", width: int = 64) -> str:
    return char * width


def push(update_path: Path) -> None:
    """Upload update_path to the batch dispatcher and display the result."""

    print()
    print(_sep("="))
    print("  SECURITY ENGINEER -- Batch Push Client")
    print(_sep("="))
    print(f"  Dispatcher  : {BASE_URL}")
    print(f"  Update file : {update_path.name}")
    print(_sep("-"))

    # Health check
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        resp.raise_for_status()
        print("  [OK] Dispatcher online")
    except requests.exceptions.ConnectionError:
        print(
            f"\n  [ERR] Cannot connect to {BASE_URL}\n"
            f"        Start the dispatcher first:\n"
            f"          cd security-agent\n"
            f"          python batch_dispatcher.py"
        )
        sys.exit(1)
    except requests.exceptions.RequestException as exc:
        print(f"\n  [ERR] Health check failed: {exc}")
        sys.exit(1)

    if not update_path.exists():
        print(f"\n  [ERR] Update file not found: {update_path}")
        sys.exit(1)

    print(f"  [OK] Update file found -- pushing to fleet ...\n")

    # POST the update
    with update_path.open("rb") as fh:
        try:
            resp = requests.post(
                f"{BASE_URL}/push",
                files={"update_file": (update_path.name, fh, "application/x-yaml")},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            print(f"  [ERR] Push request failed: {exc}")
            sys.exit(1)

    # Parse and display results
    data = resp.json()
    devices = data.get("device_results", [])

    print(_sep("="))
    print("  PUSH RESULT -- Returned by Dispatcher")
    print(_sep("-"))
    print(
        f"  {'DEVICE ID':<28} {'ROLE':<14} {'RESULT':<8}  "
        f"{'ACTION':<10}  VIOLATION"
    )
    print(_sep("-"))

    for dev in devices:
        marker = "[PASS]" if dev["result"] == "PASS" else "[FAIL]"
        violation = ""
        if dev["violations"]:
            raw = dev["violations"][0]
            violation = (raw[:63] + "...") if len(raw) > 66 else raw

        print(
            f"  {dev['device_id']:<28} {dev['role']:<14} "
            f"{marker:<8}  {dev['status']:<10}  {violation}"
        )

    print(_sep("-"))
    applied  = data.get("applied",  0)
    rejected = data.get("rejected", 0)
    total    = data.get("devices_total", len(devices))

    print(f"  Applied: {applied}   Rejected: {rejected}   Total: {total}")
    print(_sep("="))

    if rejected > 0:
        print(
            f"\n  [!] {rejected} device(s) were BLOCKED by deploy-gate.\n"
            f"      The security engineer must correct the privilege mapping\n"
            f"      in the update manifest before those devices can be updated.\n"
        )
    else:
        print("\n  [OK] All devices updated successfully.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    update_file = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_UPDATE
    push(update_file)
