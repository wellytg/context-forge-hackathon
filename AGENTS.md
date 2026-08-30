# AGENTS.md — Security Agent Pipeline Contract

## What this pipeline does

Given a set of **device roles** and their **allowed capabilities**, this
pipeline generates a working, role-bounded agent runtime, a static pre-deploy
gate, a recommendation engine, and a live per-device dashboard — following the
exact pattern built in `<project-root>/manifests/`, `<project-root>/roles/`,
`<project-root>/deploy_gate/`, and `<project-root>/agent/`.

Every agent instance is granted capabilities strictly bounded by the role baked
into its deployment manifest.  Privilege escalation is blocked by two
independent gates that must both pass before any change takes effect:

1. **Pre-deploy static gate** — diffs the current manifest against the proposed
   update and rejects any capability or role escalation before the update
   reaches any device.
2. **Runtime startup assertion** — re-validates the on-disk manifest against
   the role map on every boot and post-update restart; exits with code `1`
   before the application layer starts if the check fails.

---

## Reusable build sequence

The steps below document how this repo was actually built.  Each step is
phrased so it can be pasted into any coding-assistant session against a **new** project
folder to produce an equivalent pipeline for your own roles and capabilities.

**Step 1 — Define your roles and capability boundaries**

> Define the device roles for your deployment and their permitted capabilities
> in `roles/role_map.yaml`, following the schema in this repo's existing
> `role_map.yaml` as a template.  Each role lists the **maximum** capability
> set an agent manifest may declare for that role.  Treat this file as
> append-only in normal operations — removing a capability from a role is a
> breaking change that must go through the deploy gate.

**Step 2 — Define the manifest schema and example manifests**

> Create `manifests/schema.py` with a Pydantic `AgentManifest` model
> containing: `fleet_schema_version` (static string), `device_id`,
> `device_owner_role` (must match a key in `roles/role_map.yaml`),
> `capability_set` (non-empty list, no duplicates), and `environment`.  AgentManifest
> validates structure only (non-empty string, no duplicate capabilities) — it does
> not check filesystem existence of role_map.yaml. Role-key existence is checked
> separately during role assertion (Step 3).  Add a `manifests/loader.py` that
> loads `.yaml` or `.toml` files and returns a validated `AgentManifest`.  Write
> one example manifest per role you defined in Step 1.

**Step 3 — Implement the role validator (single source of truth)**

> Implement `roles/loader.py` (returns `dict[str, frozenset[str]]`) and
> `roles/validator.py` with `assert_capabilities_within_role(role,
> capability_set, role_map)` that raises `PrivilegeViolationError` listing the
> excess capabilities.  Both the startup assertion (Step 4) and the pre-deploy
> gate (Step 5) import this module — it is the single source of truth for all
> privilege rules.

**Step 4 — Implement the startup privilege assertion**

> Implement `agent/startup.py` with `run_startup_check(manifest_path: str) -> AgentManifest`
> (the role map is loaded internally from its default path): load the manifest,
> load the role map, call `assert_capabilities_within_role`, emit a structured
> startup log on success, and call `sys.exit(1)` after logging
> `{"event": "startup_blocked", ...}` on failure.  Wire this as the **first**
> call in `agent/main.py` before `asyncio.run()` or any FastAPI initialisation.
> This is a minimal entry point that does not yet import capability modules —
> route registration and module wiring happen in Step 6, once
> agent/capability_router.py exists. Importing capability modules before
> Step 6 will raise an ImportError.

**Step 5 — Implement the pre-deploy gate and CLI**

> Implement `deploy_gate/gate.py` with `check_manifest_update(current,
> proposed, role_map, supported_versions)` that returns a `GateResult(passed,
> violations)`.  The three rejection conditions are: `fleet_schema_version` not
> in `supported_versions`, role escalation without an explicit admin override,
> and any proposed capability outside the role's permitted set.  Expose a
> `deploy-gate check --current <path> --proposed <path>` CLI via
> `deploy_gate/cli.py` (typer); exit `0` pass / `1` fail / `2` admin token
> required.  Register `deploy-gate` as a script entry point in
> `pyproject.toml`.

**Step 6 — Implement capability-gated agent modules and the batch layer**

> Create one module per capability in `agent/modules/` (telemetry, diagnostics,
> updater, sensitive data), each exposing a FastAPI `APIRouter` and an async
> `startup()` hook.  Implement `agent/capability_router.py` to import and
> register only the routers whose capability is declared in the manifest's
> `capability_set`.  Add `/status` (JSON) and `/` (HTML dashboard) routes that
> surface the current capability set.  For fleet-wide pushes, implement a
> `batch_dispatcher.py` service that runs the gate for every device in a roster
> (`batch_targets.yaml`) and atomically applies the manifest only for devices
> that pass; write a `batch_push_client.py` CLI client that uploads an update
> file to the dispatcher.  Create demo update manifests in updates/ (a clean
> update and one with a deliberate escalation) and, optionally, a multi-process
> simulator script (launch_devices.py) for local demonstration of multiple
> devices at once.

**Step 7 — Add a recommendation engine and operator error isolation**

> Implement `deploy_gate/recommender.py` with
> `build_recommendations(gate_result, proposed_manifest, role_map,
> supported_versions=None)` that returns one structured `Recommendation` per
> violation, carrying: `violation_type`, `message`, `fix`, `safe_capability_set`
> (for capability violations), and `supported_versions` (for version
> violations).  Write a rejection sidecar file per device on gate failure, and
> surface it on the /status and / dashboard routes as a BLOCKED banner.  Expose
> a `GET /recommendations` endpoint on the dispatcher that returns the cached
> rejection report from the last push.  Inside the batch dispatcher's
> per-device evaluation loop, catch `FileNotFoundError`, `ValidationError`, and
> `yaml.YAMLError` per-device so one bad entry never stops evaluation of the
> remaining fleet.

**Step 8 — Write and run the test suite**

> Write a `tests/conftest.py` with shared fixtures (one valid manifest per role,
> the role map).  Write test files covering every module: manifest schema
> validation, role validator, startup assertion, capability router, pre-deploy
> gate, updater, batch dispatcher, recommendation engine, status/dashboard
> endpoints, and operator edge cases (bad manifest paths, malformed YAML, empty
> or duplicate capability sets, concurrent atomic writes).  Run `pytest` from
> inside `<project-root>/`; all tests must pass before the pipeline is
> considered complete.

---

## Safety contract

These constraints must hold regardless of what role/capability schema is plugged
in.  They are not optional and must not be relaxed without an explicit design
review.

1. **Capabilities are role-gated, never inferred at runtime.**  The only
   permitted source of truth for what capabilities a device may hold is
   `roles/role_map.yaml`.  The agent never derives, promotes, or defaults
   capabilities from any other signal.

2. **The pre-deploy gate and the runtime startup assertion are independent, and
   neither trusts the other.**  A manifest that somehow passes the gate is still
   rejected at startup if `assert_capabilities_within_role` fails; a manifest
   that passes startup is not retroactively considered gate-approved.  Both must
   pass independently for a change to take effect.

3. **No override flag bypasses the gate without an explicit admin token.**
   The `--allow-role-change` flag is the only override surface, and it is
   inert unless `DEPLOY_GATE_ADMIN_TOKEN` is set to a non-empty value in the
   environment.  The token check runs before any manifest is loaded so there is
   no partial evaluation.  `DEPLOY_GATE_ADMIN_TOKEN` must never be committed to
   the repository; inject it as a CI secret or set it manually by an operator.

4. **Capability reductions always pass; only escalations are blocked.**
   Downgrades are always safe.  The gate rejects any manifest whose
   `capability_set` is a strict superset of what the declared role permits.

5. **One bad device never stops fleet evaluation.**  Operator
   misconfigurations (missing manifest path, malformed YAML, schema violation)
   are caught per-device and recorded as `SKIPPED` / `ERROR`; the remaining
   devices in the fleet are always evaluated.

---

## Verifying a new schema

After substituting your own roles and capabilities, verify that every privilege
boundary is wired correctly by running the existing test suite:

```bash
cd <project-root>
pytest
```

The 139 tests in this repo validate the reference implementation's specific
roles (field_tech, monitor, read_only). They will not pass unmodified against a
new domain. After substituting your own roles and capabilities, update the
fixtures in tests/conftest.py to mirror your new roles, then confirm your
adapted test suite passes. Do not weaken assertions to force a pass — a failure
means a wiring gap between your role map, manifests, and gate logic.
