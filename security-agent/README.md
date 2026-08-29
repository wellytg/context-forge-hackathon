# Security Agent

A role-bounded fleet security agent with a two-gate privilege-escalation
defence, built for the **IBM TechXchange 2026 Dev Day Hackathon** using
**Bob IDE**.

Every agent instance is granted capabilities that are strictly bounded by the
device-owner role baked into its deployment manifest. Privilege escalation is
blocked by two independent gates that must both pass before any change can take
effect:

1. **Pre-deploy static gate** (`deploy_gate/`) — diffs the current manifest
   against the proposed update and rejects any capability or role escalation
   before the update is pushed.
2. **Runtime startup assertion** (`agent/startup.py`) — re-validates the
   on-disk manifest against the role map on every agent boot and post-update
   restart. If the check fails the process exits with code `1` and never
   reaches the application layer.

---

## Repository layout

```
security-agent/
├── agent/
│   ├── main.py               # Entry point — startup assertion runs before
│   │                         #   FastAPI or asyncio are initialised
│   ├── startup.py            # run_startup_check() + assert_startup_privileges()
│   ├── capability_router.py  # Registers only the routers permitted by the manifest
│   └── modules/
│       ├── telemetry.py      # Active when capability: telemetry_collect
│       ├── diagnostics.py    # Active when capability: diagnostics_run
│       ├── updater.py        # Active when capability: update_receive
│       └── sensitive_data.py # Active when capability: sensitive_data_read
│
├── manifests/
│   ├── schema.py             # AgentManifest — Pydantic model, canonical manifest shape
│   ├── loader.py             # load_manifest() — validates .yaml or .toml manifests
│   ├── example_field_tech.yaml
│   ├── example_monitor.yaml
│   └── example_read_only.yaml
│
├── roles/
│   ├── role_map.yaml         # Single source of truth: role → max capability set
│   ├── loader.py             # load_role_map() → dict[str, frozenset[str]]
│   └── validator.py          # assert_capabilities_within_role() + PrivilegeViolationError
│
├── deploy_gate/
│   ├── gate.py               # check_manifest_update() — returns GateResult
│   └── cli.py                # deploy-gate CLI (typer): exit 0 pass / 1 fail / 2 auth
│
├── updates/
│   ├── update_v4.2.0_batch.yaml  # Demo: clean batch push — all 3 devices pass
│   ├── update_v4.2.1.yaml        # Demo: unsafe single push — monitor role rejected
│   └── update_batch_demo.yaml    # Mixed demo: 1 pass (field_tech), 2 fail
│
├── batch_targets.yaml        # Fleet roster consumed by batch_dispatcher.py
├── batch_dispatcher.py       # FastAPI service on :8743 — runs gate for every device
├── batch_push_client.py      # CLI client — uploads an update file to the dispatcher
├── launch_devices.py         # Spawns all 3 simulated agent processes
│
├── tests/                    # pytest suite (66 tests, all passing)
│   ├── conftest.py
│   ├── test_role_validator.py
│   ├── test_manifest_schema.py
│   ├── test_startup.py
│   ├── test_capability_router.py
│   ├── test_gate.py
│   ├── test_updater.py
│   ├── test_batch_dispatcher.py
│   └── test_status_endpoint.py
│
└── pyproject.toml
```

---

## Roles and capability boundaries

Capability boundaries are defined in [`roles/role_map.yaml`](roles/role_map.yaml)
and are the single source of truth used by both the startup assertion and the
pre-deploy gate.

| Role | Permitted capabilities |
|---|---|
| `field_tech` | `telemetry_collect`, `diagnostics_run`, `update_receive`, `sensitive_data_read` |
| `monitor` | `telemetry_collect`, `diagnostics_run` |
| `read_only` | `telemetry_collect` |

A manifest's `capability_set` must be a **subset** of the maximum capabilities
listed for its `device_owner_role`. Any excess capability triggers an immediate
violation.

---

## Manifest format

Every manifest is a YAML (or TOML) file with these required fields:

```yaml
fleet_schema_version: "4.2"        # protocol version — static string, checked at startup
device_id: "device-001-field"      # unique device identifier
device_owner_role: "field_tech"    # must match a key in roles/role_map.yaml
capability_set:
  - telemetry_collect
  - diagnostics_run
  - update_receive
  - sensitive_data_read
environment: "production"
```

`fleet_schema_version` is a **static string** — not computed at runtime. It is
included in the startup log record and returned by the `/status` endpoint so the
fleet collector can parse it.

---

## How to run locally

### 1 — Install dependencies

```bash
cd security-agent
pip install -e ".[dev]"
```

### 2 — Start all three simulated devices

```bash
python launch_devices.py
```

This spawns three independent agent processes, each on its own port:

| Port | Device ID | Role |
|---|---|---|
| 8082 | `device-001-field` | `field_tech` |
| 8083 | `device-099-monitor` | `monitor` |
| 8084 | `device-042-readonly` | `read_only` |

Press **Ctrl+C** to stop all three agents.

### 3 — Run a single agent manually

```bash
security-agent --manifest manifests/example_field_tech.yaml --port 8082
```

---

## Viewing device status

Each running agent exposes two observability routes (replace `8082` with the
device's port).

### JSON status endpoint

```
GET http://127.0.0.1:8082/status
```

Returns:

```json
{
  "device_id": "device-001-field",
  "role": "field_tech",
  "fleet_schema_version": "4.2",
  "capability_set": ["diagnostics_run", "sensitive_data_read", "telemetry_collect", "update_receive"],
  "manifest_path": "manifests/example_field_tech.yaml",
  "manifest_last_modified": "2025-07-14T10:23:45+00:00"
}
```

`manifest_last_modified` is read from the file's mtime at request time — it
updates automatically after a manifest swap without restarting the agent.

### HTML dashboard

```
GET http://127.0.0.1:8082/
```

A minimal browser dashboard that fetches `/status` and auto-refreshes every 3 s.
All three device dashboards while `launch_devices.py` is running:

- http://127.0.0.1:8082/ — `device-001-field` · `field_tech`
- http://127.0.0.1:8083/ — `device-099-monitor` · `monitor`
- http://127.0.0.1:8084/ — `device-042-readonly` · `read_only`

---

## Batch fleet update

The batch layer simulates a security engineer pushing a single universal update
file to the entire fleet. The dispatcher runs the deploy-gate check for every
device independently — devices that fail are skipped entirely.

### Start the dispatcher

```bash
# Terminal 1
python batch_dispatcher.py
```

The dispatcher listens on `http://127.0.0.1:8743`.

### Push an update

```bash
# Terminal 2
python batch_push_client.py updates/update_v4.2.0_batch.yaml
```

Or use the default demo file:

```bash
python batch_push_client.py
```

The fleet roster is defined in `batch_targets.yaml`. Per-device results
(APPLIED / REJECTED + violation messages) are printed to the terminal and
returned as JSON by `POST /push`.

---

## Pre-deploy gate (single manifest check)

```bash
deploy-gate check \
  --current  manifests/example_monitor.yaml \
  --proposed updates/update_v4.2.1.yaml
```

Exit codes: `0` = pass, `1` = fail, `2` = admin token required.

To permit a role change (requires admin authorisation):

```bash
DEPLOY_GATE_ADMIN_TOKEN=<secret> deploy-gate check \
  --current  manifests/current.yaml \
  --proposed manifests/proposed.yaml \
  --allow-role-change
```

> **`DEPLOY_GATE_ADMIN_TOKEN`** must **never** be committed to the repository.
> Inject it as a CI secret (e.g. GitHub Actions secret, Vault-injected env var)
> or set it manually by an operator. Without this token, `--allow-role-change`
> is rejected with exit code `2`.

---

## Running tests

```bash
cd security-agent
pytest
```

Current result: **66 tests, all passing.**

Test files and what they cover:

| File | Coverage |
|---|---|
| `test_manifest_schema.py` | AgentManifest validation — valid, invalid, empty capability_set |
| `test_role_validator.py` | assert_capabilities_within_role — exact match, subset, excess, unknown role |
| `test_startup.py` | run_startup_check — clean boot, excess capability aborts, missing role aborts |
| `test_capability_router.py` | Route gating — only declared capability routes registered |
| `test_gate.py` | check_manifest_update — all three rules, --allow-role-change with/without token |
| `test_updater.py` | apply_update — valid update applied, privilege escalation rejected |
| `test_batch_dispatcher.py` | _run_batch — rejected device untouched, passing device applied, mixed-batch counters |
| `test_status_endpoint.py` | /status fields/types/values, manifest_last_modified after swap, / HTML response |

---

## Demo scenarios

### Scenario A — Clean batch update (all 3 devices pass)

Update file: `updates/update_v4.2.0_batch.yaml`

This manifest only asserts `telemetry_collect` — a capability permitted by
every role. All three devices pass the gate and their manifests are updated to
`fleet_schema_version: "4.2.0"`.

```bash
python batch_dispatcher.py          # Terminal 1
python batch_push_client.py updates/update_v4.2.0_batch.yaml  # Terminal 2
```

Expected output:
```
device-001-field    field_tech   [PASS]  APPLIED
device-099-monitor  monitor      [PASS]  APPLIED
device-042-readonly read_only    [PASS]  APPLIED
Applied: 3   Rejected: 0   Total: 3
```

### Scenario B — Unsafe single-device update (monitor role rejected)

Update file: `updates/update_v4.2.1.yaml`

This manifest adds `update_receive` to a `monitor`-role device. The `monitor`
role's maximum capability set is `{telemetry_collect, diagnostics_run}` —
`update_receive` is not permitted. The gate rejects the update with a named
violation and the device manifest is never touched.

```bash
deploy-gate check \
  --current  manifests/example_monitor.yaml \
  --proposed updates/update_v4.2.1.yaml
```

Expected output:
```
GATE FAIL — violations detected:
  • Role 'monitor' does not permit capabilities: {'update_receive'}
```

Exit code: `1`

---

## Deployment requirements

The agent is designed to be managed by a process supervisor (e.g. **systemd**).
Configure the unit with `Restart=on-success` so that when the updater applies a
new manifest and calls `sys.exit(0)`, the supervisor restarts the agent —
re-triggering the startup privilege assertion on the new manifest.

```ini
[Service]
ExecStart=/path/to/venv/bin/security-agent --manifest /etc/security-agent/manifest.yaml
Restart=on-success
RestartSec=2s
```
