# Security Agent

A mission-critical, Python-based fleet security agent with strict role-bounded privilege deployment.

## Repository Layout

```
security-agent/
├── agent/                    # Agent runtime
│   ├── main.py               # Single entry point — runs startup assertion before anything else
│   ├── startup.py            # Privilege assertion layer (runs on every boot & post-update restart)
│   ├── capability_router.py  # Loads only modules permitted by the manifest capability_set
│   └── modules/
│       ├── telemetry.py      # Active when: telemetry_collect
│       ├── diagnostics.py    # Active when: diagnostics_run
│       ├── updater.py        # Active when: update_receive
│       └── sensitive_data.py # Active when: sensitive_data_read
├── manifests/
│   ├── schema.py             # AgentManifest Pydantic model — canonical manifest shape
│   ├── loader.py             # Loads .yaml or .toml manifests, returns validated AgentManifest
│   ├── example_field_tech.yaml
│   └── example_read_only.yaml
├── roles/
│   ├── role_map.yaml         # Authoritative role → max capability set mapping
│   ├── loader.py             # Returns dict[str, set[str]]
│   └── validator.py          # assert_capabilities_within_role + PrivilegeViolationError
├── deploy_gate/
│   ├── gate.py               # GateResult diff logic — check_manifest_update()
│   └── cli.py                # deploy-gate CLI (typer)
├── tests/
│   ├── conftest.py           # Shared fixtures
│   ├── test_role_validator.py
│   ├── test_startup.py
│   ├── test_capability_router.py
│   ├── test_gate.py
│   └── test_updater.py
└── pyproject.toml
```

## The Role-Privilege Model

Every agent deployment manifest declares:
- `fleet_schema_version` — protocol version string (e.g. `"4.2"`), checked at startup
- `device_owner_role` — maps to a role in `roles/role_map.yaml`
- `capability_set` — the specific capabilities this agent instance is permitted to use

The `capability_set` must be a **subset** of the maximum capabilities allowed for the declared role. Any excess capability causes an immediate abort.

### Defined Roles

| Role | Permitted Capabilities |
|---|---|
| `field_tech` | `telemetry_collect`, `diagnostics_run`, `update_receive`, `sensitive_data_read` |
| `monitor` | `telemetry_collect`, `diagnostics_run` |
| `read_only` | `telemetry_collect` |

## fleet_schema_version Contract

Every manifest must declare `fleet_schema_version`. The agent checks this at startup and includes it in the capability report emitted to the fleet collector. This is a **static string field** — not computed at runtime. It is the shared protocol handshake across all devices in the fleet.

## Two-Layer Safety Gate

Privilege enforcement is **independent** at two levels so that a misconfigured CI step cannot bypass the runtime:

1. **Pre-Deploy Gate** (`deploy-gate` CLI) — static manifest diff run in CI before any update is pushed. Rejects privilege escalation before deployment.
2. **Startup Assertion** (`agent/startup.py`) — re-validates the manifest against the role map on every agent boot and post-update restart. If this check fails, the process exits with code `1` immediately.

Both gates must pass for an agent to run with updated privileges.

## Running the Agent

```bash
pip install -e ".[dev]"
security-agent --manifest manifests/example_field_tech.yaml
```

## Running the Pre-Deploy Gate

```bash
deploy-gate check --current manifests/current.yaml --proposed manifests/proposed.yaml
```

To permit a role change (requires admin authorisation):

```bash
DEPLOY_GATE_ADMIN_TOKEN=<secret> deploy-gate check \
  --current manifests/current.yaml \
  --proposed manifests/proposed.yaml \
  --allow-role-change
```

> **`DEPLOY_GATE_ADMIN_TOKEN`** must **never** be committed to the repository. Inject it as a CI secret (e.g. GitHub Actions secret, Vault-injected env var) or set it manually by an operator. Without this token, `--allow-role-change` is rejected with exit code `2`.

## Running Tests

```bash
pytest
```

## Deployment Requirements

The agent is designed to be managed by a process supervisor (e.g. **systemd**). Configure the unit with `Restart=on-success` so that when the updater applies a new manifest and calls `sys.exit(0)`, the supervisor restarts the agent — which re-triggers the startup privilege assertion on the new manifest.

Example systemd snippet:
```ini
[Service]
ExecStart=/path/to/venv/bin/security-agent --manifest /etc/security-agent/manifest.yaml
Restart=on-success
RestartSec=2s
```
