# Security Agent — Repo Structure & Safe Deploy Plan

## Top-Level Overview

Design and scaffold a mission-critical, Python-based security agent that:
- Collects telemetry and diagnostics from deployed devices
- Receives updates and field-level access to sensitive data
- Has its privileges strictly bounded by the **device owner role** baked into each deployment manifest
- Declares a shared `fleet_schema_version` string in every manifest so the collector and update receiver know which protocol version the device is running
- Enforces role-based access through two safety layers:
  1. **Startup privilege assertion** — checks capabilities against the role map on every boot and post-update restart
  2. **Pre-deploy safety gate** — static manifest diff that rejects any update that would escalate privileges beyond what the role permits

The output is a clean, production-ready Python repo scaffold (not a running service), with every structural decision documented so that rapid updates can be made safely.

---

## Sub-Task 1 — Repo Scaffold & Project Layout

**Intent**  
Establish the top-level directory structure, packaging metadata, and tooling config. Everything else builds inside this scaffold. Getting this right first prevents structural debt.

**Expected Outcomes**
- A `pyproject.toml` (or `setup.cfg`) with project metadata and dependencies declared
- A `README.md` explaining the repo layout
- Top-level directories created: `agent/`, `manifests/`, `roles/`, `deploy_gate/`, `tests/`
- A `.gitignore` appropriate for Python projects

**Todo List**
1. Create `pyproject.toml` with project name `security-agent`, Python `>=3.11`, and dependency stubs for `fastapi`, `uvicorn`, `pydantic`, `pyyaml`, `tomllib` (stdlib 3.11+), `typer` (for the gate CLI)
2. Create top-level `README.md` describing the repo layout, the role-privilege model, and the `fleet_schema_version` contract
3. Create empty `__init__.py` files in each package directory
4. Create `.gitignore`

**Relevant Context**
- Stack: Python 3.11+, YAML/TOML config, asyncio/FastAPI
- No external secret stores — manifests are static files baked at deploy time

**Status**: [x] done

---

## Sub-Task 2 — Manifest Schema (YAML/TOML)

**Intent**  
Define the canonical shape of a device deployment manifest. This is the single source of truth for what an agent is allowed to do on a given device. All other components validate against this schema.

**Expected Outcomes**
- A `manifests/schema.py` module containing a `Pydantic` model (`AgentManifest`) that fully describes a valid manifest
- An example manifest file `manifests/example_field_tech.yaml` demonstrating a `field_tech` role
- An example manifest file `manifests/example_read_only.yaml` demonstrating a `read_only` role
- Schema validation rejects any manifest missing required fields or declaring unknown capability keys

**Todo List**
1. Define `AgentManifest` Pydantic model with fields:
   - `fleet_schema_version: str` — e.g. `"4.2"`, checked at startup
   - `device_id: str` — unique device identifier
   - `device_owner_role: str` — must match a key in the role-capability map
   - `capability_set: list[str]` — list of capability identifiers this agent instance is permitted to use
   - `environment: str` — e.g. `production`, `staging`
2. Add a `@validator` (or `model_validator`) that ensures `capability_set` is not empty
3. Write `example_field_tech.yaml` with representative capabilities: `telemetry_collect`, `diagnostics_run`, `update_receive`, `sensitive_data_read`
4. Write `example_read_only.yaml` with only `telemetry_collect`
5. Add a `manifests/loader.py` utility that loads either `.yaml` or `.toml` manifests and returns a validated `AgentManifest`

**Relevant Context**
- `fleet_schema_version` is a static string — not computed at runtime. It must appear in every manifest and is included in the capability report sent to the collector
- Roles are defined in Sub-Task 3; the manifest references a role by name only

**Status**: [x] done

---

## Sub-Task 3 — Role-to-Capability Map

**Intent**  
Define, statically, which capabilities each device owner role is permitted to hold. This is the authoritative privilege boundary. Both the startup assertion and the pre-deploy gate compare against this map.

**Expected Outcomes**
- A `roles/role_map.yaml` file that lists every role and its maximum allowed capability set
- A `roles/loader.py` that loads this file and returns a typed `dict[str, set[str]]`
- A `roles/validator.py` with a single function `assert_capabilities_within_role(role, capability_set, role_map)` that raises a typed `PrivilegeViolationError` if any capability in `capability_set` is not permitted for the role

**Todo List**
1. Define at least three roles in `roles/role_map.yaml`:
   - `field_tech`: `telemetry_collect`, `diagnostics_run`, `update_receive`, `sensitive_data_read`
   - `monitor`: `telemetry_collect`, `diagnostics_run`
   - `read_only`: `telemetry_collect`
2. Implement `roles/loader.py` — reads `role_map.yaml`, returns `dict[str, set[str]]`
3. Implement `roles/validator.py`:
   - Define `PrivilegeViolationError(Exception)` with message listing the excess capabilities
   - Implement `assert_capabilities_within_role(role, capability_set, role_map)` — raises `PrivilegeViolationError` on any excess capability
4. Write unit tests in `tests/test_role_validator.py` covering: exact match, subset allowed, excess capability rejected, unknown role rejected

**Relevant Context**
- This module is imported by both the agent startup assertion (Sub-Task 4) and the pre-deploy safety gate (Sub-Task 5)
- Treat `role_map.yaml` as append-only in normal operations — removing a capability from a role is a breaking change that must go through the gate

**Status**: [x] done

---

## Sub-Task 4 — Startup Privilege Assertion

**Intent**  
The agent must verify its own manifest against the role map on every startup and every post-update restart. If the check fails, the agent must abort immediately and emit a structured alert — it must never run with excess privileges.

**Expected Outcomes**
- An `agent/startup.py` module with an `assert_startup_privileges(manifest, role_map)` function
- On success, logs a structured startup record including `fleet_schema_version`, `device_id`, `device_owner_role`, and active capabilities
- On failure, raises `PrivilegeViolationError` (from Sub-Task 3) and the agent process exits with code `1`
- `agent/main.py` calls `assert_startup_privileges` as the very first action before any module initialisation

**Todo List**
1. Implement `agent/startup.py`:
   - Load manifest via `manifests/loader.py`
   - Load role map via `roles/loader.py`
   - Call `assert_capabilities_within_role` from `roles/validator.py`
   - On success, emit a structured log dict: `{"event": "startup_ok", "fleet_schema_version": ..., "device_id": ..., "role": ..., "capabilities": [...]}`
   - On failure, emit `{"event": "startup_blocked", "reason": str(e)}` then `sys.exit(1)`
2. Implement `agent/main.py` as the asyncio entry point:
   - Parse `--manifest` CLI argument (path to manifest file)
   - Call `assert_startup_privileges` before `asyncio.run()`
   - Then launch the FastAPI app (Sub-Task 5)
3. Write `tests/test_startup.py` covering: clean startup passes, manifest with excess capability aborts, missing role aborts

**Relevant Context**
- `main.py` is the single entry point — no other path should start the agent
- The structured log output is intentionally machine-readable so a fleet collector can parse startup events

**Status**: [ ] pending

---

## Sub-Task 5 — Agent Runtime Modules (Capability-Gated)

**Intent**  
Implement the agent's operational surface: telemetry collection, diagnostics, update receiver, and sensitive data access. Each module is only loaded if its corresponding capability is present in the manifest. This enforces the privilege model at the code level, not just at startup.

**Expected Outcomes**
- `agent/modules/telemetry.py` — telemetry collection stub, only active when `telemetry_collect` is in capabilities
- `agent/modules/diagnostics.py` — diagnostics stub, only active when `diagnostics_run` is in capabilities
- `agent/modules/updater.py` — update receiver stub, only active when `update_receive` is in capabilities
- `agent/modules/sensitive_data.py` — sensitive field access stub, only active when `sensitive_data_read` is in capabilities
- `agent/capability_router.py` — loads and wires only the modules permitted by the manifest's `capability_set`
- FastAPI routes are only registered for active modules

**Todo List**
1. Create `agent/modules/__init__.py` and one Python file per capability, each exposing a `router` (FastAPI `APIRouter`) and an async `startup()` hook
2. Implement `agent/capability_router.py`:
   - Takes the validated `capability_set` from the manifest
   - Imports and registers only the routers corresponding to declared capabilities
   - Logs which modules were loaded and which were skipped
3. Wire `capability_router.py` into `agent/main.py` after the privilege assertion passes
4. Write `tests/test_capability_router.py` — assert that a manifest with only `telemetry_collect` does not register the diagnostics, updater, or sensitive_data routes

**Relevant Context**
- Capability gating at the module level means even if a manifest is somehow misconfigured, routes for ungated capabilities simply do not exist in the running process
- The `updater` module is involved in the update flow described in Sub-Task 6

**Status**: [ ] pending

---

## Sub-Task 6 — Pre-Deploy Safety Gate (Static Manifest Diff)

**Intent**  
Before any update manifest is pushed to a device, a CLI tool (`deploy_gate`) diffs the current manifest against the proposed manifest and rejects the update if:
- The `device_owner_role` changes to a more privileged role
- Any capability in the new `capability_set` is not permitted for the declared role
- The `fleet_schema_version` declared in the new manifest is not supported

This gate must run as a required CI/CD step and can also be run manually.

**Expected Outcomes**
- `deploy_gate/gate.py` — core diff logic, returns a `GateResult` (pass/fail + reasons)
- `deploy_gate/cli.py` — `typer`-based CLI: `deploy-gate check --current manifest_old.yaml --proposed manifest_new.yaml`
- Exit code `0` for pass, `1` for fail, with human-readable and JSON output modes
- `tests/test_gate.py` covering: no-change pass, capability reduction pass, capability escalation fail, role escalation fail, unsupported schema version fail

**Todo List**
1. Define `GateResult` dataclass: `passed: bool`, `violations: list[str]`
2. Implement `deploy_gate/gate.py` — `check_manifest_update(current, proposed, role_map, supported_versions)`:
   - Load and validate both manifests
   - Check `fleet_schema_version` of proposed is in `supported_versions`
   - Check role has not escalated (new role must not have a superset of capabilities compared to the old role — or simply enforce: new role must equal old role unless explicitly overridden by a flag `--allow-role-change`)
   - Check every capability in proposed `capability_set` is permitted by proposed role via `assert_capabilities_within_role`
   - Return `GateResult`
3. Implement `deploy_gate/cli.py` using `typer`:
   - `--current`, `--proposed`, `--output [text|json]`, `--allow-role-change` flag
   - `--allow-role-change` is only honoured if the environment variable `DEPLOY_GATE_ADMIN_TOKEN` is set and non-empty; if the flag is passed without the token present, the CLI exits with code `2` and prints `"admin token required for --allow-role-change"` — the role-change is never applied
   - The admin token check happens before any manifest loading so there is no partial evaluation
   - Print violations and exit with code `1` on failure
4. Register CLI as a script entry point in `pyproject.toml`: `deploy-gate = "deploy_gate.cli:app"`
5. Write `tests/test_gate.py` — add cases: `--allow-role-change` without token exits `2`, `--allow-role-change` with token permits role change if capabilities are within new role

**Relevant Context**
- The gate uses the same `roles/validator.py` and `roles/loader.py` as the agent — single source of truth for privilege rules
- In a CI pipeline this tool would be invoked against the PR-proposed manifest before merging
- `DEPLOY_GATE_ADMIN_TOKEN` is never committed to the repo — it must be injected as a CI secret or set by an operator manually; document this in `README.md`

**Status**: [ ] pending

---

## Sub-Task 7 — Update Protocol (Safe In-Place Update Flow)

**Intent**  
Define and implement the in-agent update flow. When the `updater` module receives an update package, it must:
1. Write the new manifest to a staging location
2. Run the pre-deploy gate check against the staging manifest
3. Only if the gate passes, atomically replace the active manifest and trigger a controlled agent restart
4. On restart, the startup privilege assertion re-runs — providing a second independent check

This ensures rapid updates remain safe: two independent gates must both pass before any privilege change takes effect.

**Expected Outcomes**
- `agent/modules/updater.py` extended with an `apply_update(new_manifest_path)` async function
- The function calls `deploy_gate/gate.py` directly (Python import, not subprocess) for speed
- On gate failure, the update is rejected, the existing manifest is untouched, and a structured alert is emitted
- On gate pass, the manifest is replaced and `sys.exit(0)` is called (process manager — e.g. systemd — restarts the agent, triggering the startup assertion)
- `tests/test_updater.py` covering: update with valid manifest applies, update with privilege escalation is rejected and old manifest preserved

**Todo List**
1. Extend `agent/modules/updater.py` with `apply_update(new_manifest_path, current_manifest_path, role_map_path)`:
   - Load proposed manifest
   - Call `check_manifest_update` from `deploy_gate/gate.py`
   - On failure: log `{"event": "update_rejected", "violations": [...]}` and return without changing anything
   - On pass: atomically copy new manifest to active manifest path (write to `.tmp` then `os.replace`)
   - Emit `{"event": "update_applied", "new_fleet_schema_version": ...}` then call `sys.exit(0)` for clean restart
2. Add a FastAPI `POST /update` endpoint in the updater module that accepts a manifest file upload and calls `apply_update`
3. Write `tests/test_updater.py`

**Relevant Context**
- `os.replace()` is atomic on POSIX; on Windows use `shutil.move` with the `.tmp` pattern
- The process manager restart is out of scope for this repo — document it in `README.md` as a deployment requirement (systemd `Restart=on-success` or equivalent)

**Status**: [ ] pending

---

## Sub-Task 8 — Tests & Validation Summary

**Intent**  
Ensure test coverage exists for all privilege-critical paths. This sub-task audits and fills any gaps after the prior sub-tasks are done.

**Expected Outcomes**
- All tests in `tests/` pass with `pytest`
- A `tests/conftest.py` providing shared fixtures: a valid `field_tech` manifest, a valid `read_only` manifest, and the role map
- Coverage of: manifest loading (valid + invalid), role validation, startup assertion, capability routing, pre-deploy gate, and update protocol

**Todo List**
1. Write `tests/conftest.py` with shared manifest and role map fixtures
2. Audit each test file for missing edge cases — particularly: empty `capability_set`, `fleet_schema_version` mismatch, unknown role name
3. Ensure all test files have been created as specified in prior sub-tasks
4. Run `pytest` and confirm zero failures

**Relevant Context**
- Tests in sub-tasks 3–7 were specified per-module; this sub-task only fills gaps and adds `conftest.py`

**Status**: [ ] pending
