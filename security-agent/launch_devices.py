"""
Launch 7 Security Agent instances — 1 Field Tech, 2 Monitors, 4 Read-Only.
==========================================================================

Starts seven independent agent processes, each bound to a different port and
loaded with its own role manifest:

    Port 8082  device-001-field    field_tech   manifests/example_field_tech.yaml
    Port 8083  device-099-monitor  monitor      manifests/example_monitor.yaml
    Port 8085  device-098-monitor  monitor      manifests/example_monitor_02.yaml
    Port 8084  device-042-readonly read_only    manifests/example_read_only.yaml
    Port 8086  device-043-readonly read_only    manifests/example_read_only_02.yaml
    Port 8087  device-044-readonly read_only    manifests/example_read_only_03.yaml
    Port 8088  device-045-readonly read_only    manifests/example_read_only_04.yaml

Each process emits a clear startup banner before handing off to uvicorn.
This script blocks until you press Ctrl+C, then shuts all agents down.

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
# Fleet device roster (7 devices)
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent

DEVICES = [
    # 1 Field Tech
    {
        "device_id":  "device-001-field",
        "role":       "field_tech",
        "manifest":   "manifests/example_field_tech.yaml",
        "port":       8082,
    },
    # 2 Monitors
    {
        "device_id":  "device-099-monitor",
        "role":       "monitor",
        "manifest":   "manifests/example_monitor.yaml",
        "port":       8083,
    },
    {
        "device_id":  "device-098-monitor",
        "role":       "monitor",
        "manifest":   "manifests/example_monitor_02.yaml",
        "port":       8085,
    },
    # 4 Read-Only
    {
        "device_id":  "device-042-readonly",
        "role":       "read_only",
        "manifest":   "manifests/example_read_only.yaml",
        "port":       8084,
    },
    {
        "device_id":  "device-043-readonly",
        "role":       "read_only",
        "manifest":   "manifests/example_read_only_02.yaml",
        "port":       8086,
    },
    {
        "device_id":  "device-044-readonly",
        "role":       "read_only",
        "manifest":   "manifests/example_read_only_03.yaml",
        "port":       8087,
    },
    {
        "device_id":  "device-045-readonly",
        "role":       "read_only",
        "manifest":   "manifests/example_read_only_04.yaml",
        "port":       8088,
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

    proc = subprocess.Popen(
        cmd,
        cwd=str(_HERE),
    )
    return proc


def main() -> None:
    print()
    print(_SEP_DOUBLE)
    print("  FLEET DEVICE LAUNCHER — Security Agent Demo (7 Devices)")
    print(_SEP_DOUBLE)
    print(f"  Starting {len(DEVICES)} agent instance(s) ...")
    print(f"  Press Ctrl+C to stop all agents.")
    print(_SEP_SINGLE)
    print()

    procs: list[subprocess.Popen] = []

    for device in DEVICES:
        proc = _launch(device)
        procs.append(proc)
        time.sleep(0.3)

    print()
    print(_SEP_DOUBLE)
    print(f"  ALL {len(DEVICES)} AGENTS STARTED")
    print(_SEP_SINGLE)
    print(f"  {'PORT':<8}  {'DEVICE ID':<28}  ROLE")
    print(_SEP_SINGLE)
    for d in DEVICES:
        print(f"  {d['port']:<8}  {d['device_id']:<28}  {d['role']}")
    print(_SEP_DOUBLE)
    print()
    print("  Live status dashboards (auto-refresh every 3 s):")
    for d in DEVICES:
        print(f"    http://127.0.0.1:{d['port']}/   ({d['device_id']} · {d['role']})")
    print()
    print("  JSON status endpoints:")
    for d in DEVICES:
        print(f"    http://127.0.0.1:{d['port']}/status")
    print()

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

    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
