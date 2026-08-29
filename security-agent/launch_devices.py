"""
Launch 3 Security Agent instances — one per role.
===================================================

Starts three independent agent processes, each bound to a different port and
loaded with its own role manifest:

    Port 8082  device-001-field    field_tech   manifests/example_field_tech.yaml
    Port 8083  device-099-monitor  monitor      manifests/example_monitor.yaml
    Port 8084  device-042-readonly read_only    manifests/example_read_only.yaml

Each process emits a clear startup banner before handing off to uvicorn.
This script blocks until you press Ctrl+C, then shuts all three agents down.

Usage:
    cd security-agent
    python launch_devices.py
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Fleet device roster
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent

DEVICES = [
    {
        "device_id":  "device-001-field",
        "role":       "field_tech",
        "manifest":   "manifests/example_field_tech.yaml",
        "port":       8082,
    },
    {
        "device_id":  "device-099-monitor",
        "role":       "monitor",
        "manifest":   "manifests/example_monitor.yaml",
        "port":       8083,
    },
    {
        "device_id":  "device-042-readonly",
        "role":       "read_only",
        "manifest":   "manifests/example_read_only.yaml",
        "port":       8084,
    },
]

_SEP_DOUBLE = "=" * 64
_SEP_SINGLE = "-" * 64


def _print_banner(device: dict) -> None:
    """Print the pre-launch banner for a single device."""
    print(_SEP_DOUBLE)
    print(f"  LAUNCHING SECURITY AGENT INSTANCE")
    print(_SEP_SINGLE)
    print(f"  device_id  :  {device['device_id']}")
    print(f"  role       :  {device['role']}")
    print(f"  port       :  {device['port']}")
    print(f"  manifest   :  {device['manifest']}")
    print(_SEP_DOUBLE)


def _launch(device: dict) -> subprocess.Popen:
    """Spawn a security-agent process and return its Popen handle."""
    _print_banner(device)

    cmd = [
        sys.executable, "-m", "agent.main",
        "--manifest", device["manifest"],
        "--host",     "127.0.0.1",
        "--port",     str(device["port"]),
    ]

    # Each agent gets its own output stream so logs from all three are visible
    # in the same terminal window, prefixed naturally by uvicorn's log format.
    proc = subprocess.Popen(
        cmd,
        cwd=str(_HERE),
    )
    return proc


def main() -> None:
    print()
    print(_SEP_DOUBLE)
    print("  FLEET DEVICE LAUNCHER — Security Agent Demo")
    print(_SEP_DOUBLE)
    print(f"  Starting {len(DEVICES)} agent instance(s) ...")
    print(f"  Press Ctrl+C to stop all agents.")
    print(_SEP_SINGLE)
    print()

    procs: list[subprocess.Popen] = []

    for device in DEVICES:
        proc = _launch(device)
        procs.append(proc)
        # Small stagger so startup logs don't interleave badly on stdout
        time.sleep(0.3)

    print()
    print(_SEP_DOUBLE)
    print("  ALL 3 AGENTS STARTED")
    print(_SEP_SINGLE)
    print(f"  {'PORT':<8}  {'DEVICE ID':<28}  ROLE")
    print(_SEP_SINGLE)
    for d in DEVICES:
        print(f"  {d['port']:<8}  {d['device_id']:<28}  {d['role']}")
    print(_SEP_DOUBLE)
    print()
    print("  Health check URLs:")
    for d in DEVICES:
        print(f"    http://127.0.0.1:{d['port']}/healthz   ({d['role']})")
    print()

    # ── Shutdown on Ctrl+C or SIGTERM ──────────────────────────────────────
    def _shutdown(signum, frame) -> None:  # noqa: ANN001
        print("\n\n  [launcher] Shutdown signal received — stopping all agents ...")
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("  [launcher] All agents stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Wait for all children — blocks until they all exit (or Ctrl+C fires)
    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
