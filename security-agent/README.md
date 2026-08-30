# Security Agent

A role-bounded fleet security agent with a two-gate privilege-escalation
defence, built for the **IBM TechXchange 2026 Dev Day Hackathon** using
**Bob IDE**.

Every agent instance is granted capabilities strictly bounded by the
device-owner role baked into its deployment manifest. Privilege escalation
is blocked by two independent gates that must both pass before any change
takes effect:

1. **Pre-deploy static gate** (`deploy_gate/`, `batch_dispatcher.py`) — diffs
   the current manifest against the proposed update and rejects any capability
   or role escalation before the update is pushed to any device.
2. **Runtime startup assertion** (`agent/startup.py`) — re-validates the on-disk
   manifest against the role map on every agent boot and post-update restart.
   If the check fails the process exits with code `1` before reaching the
   application layer.

Each running device also exposes a live **per-device status dashboard** (`/`)
that auto-refreshes every 3 s and shows the current capability set alongside
any pending rejection from the last batch push.

---

## Architecture

```
security-agent/
│
├── manifests/                       # Manifest schema, loader, and example files
│   ├── schema.py                    # AgentManifest — Pydantic model, canonical shape
│   ├── loader.py                    # load_manifest() — validates .yaml or .toml
│   ├── example_field_tech.yaml
│   ├── example_monitor.yaml
│   └── example_read_only.yaml
│
├── roles/                           # Authoritative role → capability boundaries
│   ├── role_map.yaml                # Single source of truth for all privilege rules
│   ├── loader.py                    # load_role_map() → dict[str, frozenset[str]]
│   └── validator.py                 # assert_capabilities_within_role() + PrivilegeViolationError
│
├── agent/                           # Per-device runtime
│   ├── main.py                      # Entry point — startup assertion runs before
│   │                                #   FastAPI or asyncio are initialised
│   ├── startup.py                   # run_startup_check() — loads manifest, runs gate,
│   │                                #   exits 1 on violation
│   ├── capability_router.py         # Registers only the routes permitted by the manifest
│   └── modules/
│       ├── telemetry.py             # Active when capability: telemetry_collect
│       ├── diagnostics.py           # Active when capability: diagnostics_run
│       ├── updater.py               # Active when capability: update_receive
│       └── sensitive_data.py        # Active when capability: sensitive_data_read
│
├── deploy_gate/                     # Pre-deploy gate and recommendation engine
│   ├── gate.py                      # check_manifest_update() — returns GateResult
│   ├── recommender.py               # build_recommendations() — structured fix-it advice
│   └── cli.py                       # deploy-gate CLI (typer): exit 0 pass / 1 fail / 2 auth
│
├── updates/                         # Demo update manifests
│   ├── update_v4.2.0_batch.yaml     # Clean batch push — all 3 devices pass
│   ├── update_v4.2.1.yaml           # Unsafe single push — monitor role rejected
│   └── update_batch_demo.yaml       # Mixed demo: 1 pass (field_tech), 2 fail
│
├── batch_targets.yaml               # Fleet roster consumed by batch_dispatcher.py
├── batch_dispatcher.py              # FastAPI service on :8743 — runs gate for every device
├── batch_push_client.py             # CLI client — uploads an update file to the dispatcher
├── launch_devices.py                # Spawns all 3 simulated agent processes
│
├── tests/                           # pytest suite — 125 tests, all passing
│   ├── conftest.py
│   ├── test_role_validator.py
│   ├── test_manifest_schema.py
│   ├── test_startup.py
│   ├── test_capability_router.py
│   ├── test_gate.py
│   ├── test_updater.py
│   ├── test_batch_dispatcher.py
│   ├── test_recommendations_endpoint.py
│   ├── test_recommender.py
│   ├── test_status_endpoint.py
│   └── test_operator_edge_cases.py
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
violation at both the pre-deploy gate and the runtime startup assertion.
Capability reductions (downgrades) always pass — only escalations are blocked.

---

## Manifest format

Every manifest is a YAML (or TOML) file with these required fields:

```yaml
fleet_schema_version: "4.2.0"     # protocol version — static string, checked at startup
device_id: "device-001-field"     # unique device identifier
device_owner_role: "field_tech"   # must match a key in roles/role_map.yaml
capability_set:
  - telemetry_collect
  - diagnostics_run
  - update_receive
  - sensitive_data_read
environment: "production"
```

`fleet_schema_version` is a **static string** — not computed at runtime. It is
included in the startup log record and returned by the `/status` endpoint so a
fleet collector can parse it. The schema is validated by `AgentManifest`
(Pydantic); empty `capability_set`, duplicate entries, and missing required
fields are all rejected at parse time before any gate logic runs.

---

## The recommendation system

When the gate rejects an update, the dispatcher does not just report a
violation — it generates structured, per-device fix-it advice via
`deploy_gate/recommender.py`.

**Each `Recommendation` carries:**

| Field | Content |
|---|---|
| `violation_type` | One of `capability_boundary`, `unsupported_version`, `role_change` |
| `message` | One-sentence description of what went wrong |
| `fix` | Concrete action the security engineer should take |
| `safe_capability_set` | For capability violations: the subset of the proposed caps that *are* permitted for this role |
| `supported_versions` | For version violations: the list of accepted version strings |

**Where recommendations are surfaced:**

- **`POST /push` response** — the full `device_results` array in the JSON
  response includes a `recommendations` list for each rejected device.
- **`GET /recommendations`** — the batch dispatcher caches the last push's
  rejection report in memory; this endpoint returns it for CI scripts that
  poll after a failed push without re-running it.
- **Rejection sidecar** — on every REJECTED device the dispatcher writes
  a `<manifest>.rejection.json` sidecar alongside the manifest file.
- **Device dashboard (`/`)** — the per-device HTML dashboard reads the sidecar
  at request time and displays a **BLOCKED** banner with the violation and fix
  string whenever one is present. The banner clears automatically on the next
  successful push.

---

## Per-device error isolation

Operator misconfigurations — a missing manifest path, a malformed update file,
or a schema-violating template — are caught per-device inside `_run_batch`.
One bad entry never stops evaluation of the remaining fleet.

A device that hits one of these conditions is recorded as:

```json
{
  "device_id": "...",
  "role": "...",
  "result": "ERROR",
  "status": "SKIPPED",
  "violation": "<readable message>",
  "recommendations": []
}
```

The three categories caught per-device:

| Error type | Condition | Example |
|---|---|---|
| `FileNotFoundError` | `current_manifest` path in `batch_targets.yaml` does not exist | Typo in the roster, device removed from fleet |
| `ValidationError` (Pydantic) | Update template violates `AgentManifest` schema | Empty `capability_set`, missing `fleet_schema_version`, duplicate capabilities |
| `yaml.YAMLError` / `TypeError` | Update template is malformed YAML or its top-level value is not a mapping | Tab-indented YAML, truncated file, bare scalar instead of a dict |

All other exception types still propagate loudly so unexpected failures are
never silently swallowed. This behaviour is covered by the
`tests/test_operator_edge_cases.py` suite (16 tests).

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

### 3 — Open the device dashboards

While `launch_devices.py` is running, open any device's live status page:

- http://127.0.0.1:8082/ — `device-001-field` · `field_tech`
- http://127.0.0.1:8083/ — `device-099-monitor` · `monitor`
- http://127.0.0.1:8084/ — `device-042-readonly` · `read_only`

Each page auto-refreshes every 3 s. A red **BLOCKED** banner appears when the
last push was rejected for that device; it clears when a clean push is applied.

### 4 — JSON status and health endpoints

Replace `8082` with the target device's port.

```
GET http://127.0.0.1:8082/status
```

Returns:

```json
{
  "device_id": "device-001-field",
  "role": "field_tech",
  "fleet_schema_version": "4.2.0",
  "capability_set": ["diagnostics_run", "sensitive_data_read", "telemetry_collect", "update_receive"],
  "manifest_path": "manifests/example_field_tech.yaml",
  "manifest_last_modified": "2025-07-14T10:23:45+00:00",
  "last_rejection": null
}
```

`manifest_last_modified` is read from the file's mtime at request time — it
updates automatically after a manifest swap without restarting the agent.
`last_rejection` is `null` when no rejection sidecar exists; it contains the
full `violations` and `recommendations` payload when a push has been rejected
for this device and not yet cleared by a subsequent passing push.

```
GET http://127.0.0.1:8082/healthz
```

Returns `{"status": "ok", "device_id": "...", "role": "...", ...}`.

### 5 — Run a single agent manually

```bash
security-agent --manifest manifests/example_field_tech.yaml --port 8082
```

---

## Batch fleet update

The batch layer simulates a security engineer pushing a single universal update
file to the entire fleet. The dispatcher runs the deploy-gate check for every
device independently and atomically applies the manifest only for devices that
pass. Devices that fail are never touched.

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

Or use the default mixed-result demo file (1 pass, 2 fail):

```bash
python batch_push_client.py
```

The fleet roster is defined in `batch_targets.yaml`. Per-device results
(`APPLIED` / `REJECTED` / `SKIPPED` with violation messages and fix
suggestions) are printed to the terminal and returned as JSON by `POST /push`.
An `[ERR]` marker in the summary table indicates a device that was skipped due
to a misconfiguration (bad manifest path, malformed update file), distinct from
a `[FAIL]` which is a genuine RBAC violation.

### Retrieve the last push report without re-running

```bash
curl http://127.0.0.1:8743/recommendations
```

---

## Pre-deploy gate (single manifest check)

```bash
deploy-gate check \
  --current  manifests/example_monitor.yaml \
  --proposed updates/update_v4.2.1.yaml
```

Exit codes: `0` = pass, `1` = fail, `2` = admin token required.

JSON output mode:

```bash
deploy-gate check \
  --current  manifests/example_monitor.yaml \
  --proposed updates/update_v4.2.1.yaml \
  --output json
```

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
> is rejected with exit code `2` before any manifest is loaded.

---

## Running tests

```bash
cd security-agent
pytest
```

**Current result: 125 tests, all passing.**

| Test file | Coverage |
|---|---|
| `test_manifest_schema.py` | `AgentManifest` validation — valid, invalid, empty/duplicate capability_set |
| `test_role_validator.py` | `assert_capabilities_within_role` — exact match, subset, excess, unknown role |
| `test_startup.py` | `run_startup_check` — clean boot, excess capability aborts, missing role aborts |
| `test_capability_router.py` | Route gating — only declared capability routes registered |
| `test_gate.py` | `check_manifest_update` — all three rules, `--allow-role-change` with/without token |
| `test_updater.py` | `apply_update` — valid update applied, privilege escalation rejected, .tmp cleanup |
| `test_batch_dispatcher.py` | `_run_batch` — rejected device untouched, passing device applied, mixed-batch counters, sidecar lifecycle |
| `test_recommender.py` | `build_recommendations` — one recommendation per violation type, safe_capability_set accuracy |
| `test_recommendations_endpoint.py` | `GET /recommendations` — empty sentinel, post-rejection content, clean-push clears list |
| `test_status_endpoint.py` | `/status` fields/types/values, `manifest_last_modified` after swap, `last_rejection` sidecar lifecycle, HTML dashboard |
| `test_operator_edge_cases.py` | Operator hardening: bad manifest paths, malformed YAML, missing schema fields, duplicate capabilities, stale rejection sidecar recovery, same-role multi-version fleets, capability downgrade, concurrent atomic writes |

The `test_operator_edge_cases.py` suite (16 tests) is specifically designed to
cover real-world misconfigurations beyond the happy path. It confirms that
missing files, syntax errors, empty capability sets, and duplicate capability
entries all produce clean structured error results rather than uncaught
exceptions, and that concurrent pushes never produce a corrupt manifest on disk.

---

## Demo scenarios

### Scenario A — Clean fleet-wide update (all 3 devices pass)

Update file: `updates/update_v4.2.0_batch.yaml`

This manifest only asserts `telemetry_collect` — a capability permitted by
every role. All three devices pass the gate and their manifests are updated to
`fleet_schema_version: "4.2.0"`.

```bash
python batch_dispatcher.py                                           # Terminal 1
python batch_push_client.py updates/update_v4.2.0_batch.yaml        # Terminal 2
```

Expected output:

```
  DEVICE ID                    ROLE           RESULT    ACTION
  device-001-field             field_tech     [PASS]    APPLIED
  device-099-monitor           monitor        [PASS]    APPLIED
  device-042-readonly          read_only      [PASS]    APPLIED
  Applied: 3   Rejected: 0   Errored: 0   Total: 3
```

### Scenario B — Unsafe update (role violation, rejected with fix suggestion)

Update file: `updates/update_v4.2.1.yaml`

This manifest requests `update_receive` for a `monitor`-role device. The
`monitor` role's maximum capability set is `{telemetry_collect, diagnostics_run}`
— `update_receive` is not permitted. The gate rejects the push, the device
manifest is never touched, and a structured recommendation is returned.

Check it directly with the CLI:

```bash
deploy-gate check \
  --current  manifests/example_monitor.yaml \
  --proposed updates/update_v4.2.1.yaml
```

Expected output:

```
GATE FAIL — violations detected:
  • Role 'monitor' does not permit the following capabilities: ['update_receive'].
    Remove them from capability_set or use a role that allows them.
```

Exit code: `1`

Or push it through the batch dispatcher to see the full recommendation:

```bash
python batch_push_client.py updates/update_batch_demo.yaml
```

Expected output (mixed fleet — 1 pass, 2 fail):

```
  DEVICE ID                    ROLE           RESULT    ACTION
  device-001-field             field_tech     [PASS]    APPLIED
  device-099-monitor           monitor        [FAIL]    REJECTED
      FIX: Remove capability 'update_receive' from the capability_set ...
  device-042-readonly          read_only      [FAIL]    REJECTED
      FIX: Remove capabilities 'diagnostics_run', 'update_receive' from ...
  Applied: 1   Rejected: 2   Errored: 0   Total: 3
```

After the rejection, the `monitor` device's dashboard (`http://127.0.0.1:8083/`)
will display the **BLOCKED** banner until a corrected update is pushed and
accepted.

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
