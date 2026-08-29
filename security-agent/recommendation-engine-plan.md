# Recommendation Engine — Scale-Ready Deploy-Gate Feedback Plan

## Top-Level Overview

The deploy-gate already catches every class of misconfiguration deterministically. The violation
messages it produces contain **structured data** (role name, excess capability names, version
strings) embedded in plain-English sentences. The goal of this plan is to mine that structure — 
without changing any existing logic — and surface actionable fix-it recommendations in three
places:

1. **`/push` JSON response** (batch dispatcher) — each `REJECTED` device entry gains a
   `recommendations` list alongside its `violations` list.
2. **`GET /recommendations` endpoint** (batch dispatcher) — a dedicated endpoint returning the
   last push's full recommendation report, useful for scripting / CI polling.
3. **Agent dashboard `/`** — if the agent's `manifest_last_modified` has **not** changed after a
   push attempt, a "stale — blocked" badge and the last known recommendation surface in the
   browser.

### What "no new logic" means here

The gate already computes `excess_capabilities` (a `set[str]`), catches unsupported versions
(a string comparison against `SUPPORTED_VERSIONS`), and catches role changes (a string
comparison). The recommendation engine is a **pure translation layer** — it reads those already-
computed artefacts and formats human-readable + machine-readable fix-it advice. No new gating
rules, no new manifest parsing, no new role-map loading.

### Scale note

Everything below is stateless per-request or held in a small in-memory dict on the dispatcher
process. At real scale (hundreds of devices) the `_run_batch` loop already iterates every device
independently — the recommendation layer adds one dictionary lookup per device, not a new network
call or file read.

---

## Sub-Task 1 — `deploy_gate/recommender.py` (pure translation layer)

**Intent**  
Create a single new module that translates a `GateResult` + a proposed `AgentManifest` + the
`role_map` into a structured `Recommendation` object. This is the only new file that contains
logic. Every other sub-task consumes it.

**Expected Outcomes**
- `deploy_gate/recommender.py` exported symbols:
  - `Recommendation` dataclass with fields:
    - `violation_type: str` — one of `"capability_boundary"`, `"unsupported_version"`,
      `"role_change"`
    - `message: str` — one sentence, human-readable
    - `fix: str` — concrete action the security engineer should take
    - `safe_capability_set: list[str] | None` — for capability violations, the subset of
      the proposed capability_set that IS permitted for the role; `None` for other violation types
    - `supported_versions: list[str] | None` — for version violations, the list the dispatcher
      accepts; `None` otherwise
  - `build_recommendations(gate_result, proposed_manifest, role_map, supported_versions) ->
    list[Recommendation]` — called once per rejected device; returns one `Recommendation` per
    violation in `gate_result.violations`

**How each violation type maps to a recommendation (no new logic, pure classification):**

| Violation text contains | `violation_type` | `fix` | `safe_capability_set` |
|---|---|---|---|
| `"does not permit the following capabilities"` | `capability_boundary` | "Remove the excess capabilities from the update manifest for this role, or target only devices with a role that permits them." | proposed caps minus excess caps (set difference already done by the gate — recompute from role_map) |
| `"Unsupported fleet_schema_version"` | `unsupported_version` | "Change fleet_schema_version in the update to one of the supported values." | None |
| `"device_owner_role changed"` | `role_change` | "Re-push with --allow-role-change and a valid admin token, or keep the existing role." | None |

For `capability_boundary`, `safe_capability_set` is computed as:
`sorted(set(proposed.capability_set) & role_map[proposed.device_owner_role])` — a single set
intersection using already-loaded data.

**Relevant Context**
- `GateResult` is in `deploy_gate/gate.py` — `violations: list[str]`, `passed: bool`
- `PrivilegeViolationError` in `roles/validator.py` exposes `excess_capabilities: set[str]` but
  the gate catches it and stringifies it. The recommender must **re-derive** excess capabilities
  from `role_map` and `proposed.capability_set` — one set difference, no file I/O.
- `AgentManifest` fields needed: `device_owner_role`, `capability_set`,
  `fleet_schema_version`
- Do not import from `roles.validator` inside the recommender — derive from `role_map` directly
  to keep the recommender a leaf module with no circular deps.

**Todo List**
1. Create `deploy_gate/recommender.py`
2. Define `Recommendation` as a `dataclass` with the 5 fields above
3. Implement `build_recommendations(gate_result, proposed_manifest, role_map, supported_versions)`
   — iterate `gate_result.violations`, classify each by substring match, build one
   `Recommendation` per violation
4. For `capability_boundary` violations, compute `safe_capability_set` as
   `sorted(set(proposed.capability_set) & role_map.get(proposed.device_owner_role, frozenset()))`
5. Export `Recommendation` and `build_recommendations` from `deploy_gate/__init__.py`

**Status**: [ ] pending

---

## Sub-Task 2 — Wire recommendations into `batch_dispatcher.py` `/push` response

**Intent**  
The `_run_batch` function already builds a `results` list per device. For every `REJECTED`
device, attach a `recommendations` key built by calling `build_recommendations`. The JSON shape
of `/push` gains `recommendations` on rejected entries and stays backward-compatible (passing
devices get `recommendations: []`).

**Expected Outcomes**
- `/push` response per-device entry gains:
  ```json
  {
    "device_id": "...",
    "result": "FAIL",
    "status": "REJECTED",
    "violations": ["..."],
    "recommendations": [
      {
        "violation_type": "capability_boundary",
        "message": "...",
        "fix": "...",
        "safe_capability_set": ["diagnostics_run", "telemetry_collect"],
        "supported_versions": null
      }
    ]
  }
  ```
- Passing devices: `"recommendations": []`
- The `batch_push_client.py` display table prints the first `fix` string under each rejected
  device — one line, truncated to 80 chars if needed. No structural change to the client.

**No changes to:** `deploy_gate/gate.py`, `roles/validator.py`, manifest loading, or the
apply/reject branch logic.

**Relevant Context**
- `_run_batch` in `batch_dispatcher.py` already has `proposed` (the stamped manifest path) and
  `gate_result` in scope — pass them to `build_recommendations` right after the gate check.
- `proposed` is a `Path` to a temp YAML file — load it as `AgentManifest` using
  `manifests.loader.load_manifest` (already imported transitively; add the explicit import).
- `role_map` is already loaded once at the top of `_run_batch` — pass it through.
- `SUPPORTED_VERSIONS` is a module-level constant in `batch_dispatcher.py`.

**Todo List**
1. In `batch_dispatcher.py`, import `build_recommendations` from `deploy_gate.recommender` and
   `load_manifest` from `manifests.loader`
2. In the `_run_batch` loop, after the gate check, call `build_recommendations` for every device
   (pass empty list for passing devices, full result for rejected ones) and convert each
   `Recommendation` to a dict via `dataclasses.asdict`
3. Append `"recommendations"` key to each device result dict
4. In `batch_push_client.py`, after the violation line for a rejected device, print the first
   recommendation's `fix` string (if present), indented, truncated to 80 chars
5. Update the terminal output in `_run_batch` to also print the first `fix` line per rejected
   device

**Status**: [ ] pending

---

## Sub-Task 3 — `GET /recommendations` endpoint on the batch dispatcher

**Intent**  
After a push, the security engineer (or a CI script) can `GET /recommendations` to retrieve the
full structured recommendation report from the last push — no re-run needed. This is a
read-only, in-memory endpoint; it stores nothing to disk.

**Expected Outcomes**
- `GET /recommendations` on the batch dispatcher (`localhost:8743`) returns:
  ```json
  {
    "last_push_file": "update_v4.2.1.yaml",
    "devices_total": 3,
    "rejected": 2,
    "recommendations": [
      {
        "device_id": "device-099-monitor",
        "role": "monitor",
        "violations": ["..."],
        "recommendations": [{ ... }]
      }
    ]
  }
  ```
  or `{"last_push_file": null, "recommendations": []}` if no push has been run yet.
- `GET /health` and `POST /push` are unchanged.

**Relevant Context**
- Store the last push's recommendation report as a module-level variable
  `_last_recommendations: dict | None = None` in `batch_dispatcher.py` — updated at the end of
  `_run_batch`. This is a single assignment, no locking needed (FastAPI/uvicorn runs one event
  loop in one process for this demo).
- Only rejected devices appear in the `recommendations` list — devices that passed need no fix.

**Todo List**
1. Add `_last_recommendations: dict | None = None` module-level variable in
   `batch_dispatcher.py`
2. At the end of `_run_batch`, build and assign `_last_recommendations` from the results list
   (only rejected entries)
3. Add `GET /recommendations` route that returns `_last_recommendations` or an empty sentinel

**Status**: [ ] pending

---

## Sub-Task 4 — Surface "stale/blocked" state in the agent dashboard `/`

**Intent**  
The agent dashboard already polls `/status` every 3 seconds. If a push was REJECTED for that
device, the manifest file is never touched and `manifest_last_modified` stays the same. The
dashboard can detect this state — not by receiving a push notification, but by comparing
`manifest_last_modified` to a timestamp the page recorded when it last saw a change. If the
timestamp is more than N seconds old relative to a "last push attempted" marker, show a
warning badge.

Because the agent knows nothing about the dispatcher, a simpler and equally correct approach:
the dashboard fetches `/status` every 3 s. If `manifest_last_modified` has not changed in the
last poll cycle, show a neutral "up to date" state. A "stale" indicator is triggered by a
**new query parameter the dispatcher stamps onto the agent's URL when it logs a rejection** — or,
more simply, by adding a `GET /last-rejection` endpoint on the **agent itself** that the
dashboard polls.

**Chosen approach (least code, no new inter-process communication):**

Add `GET /last-rejection` on each agent instance. The agent's `_run_batch`-equivalent is the
`updater` module's `apply_update` call. When a rejection occurs (the updater already returns
`{"event": "update_rejected", "violations": [...]}` from `agent/modules/updater.py`), store
that result as a module-level variable on the agent process.

The dashboard polls `/last-rejection` alongside `/status` and, if a rejection exists and
`manifest_last_modified` matches the rejection's timestamp-of-attempt, shows a ⚠️ badge with
the violation text and the recommendation's `fix` string.

**BUT** — the batch dispatcher pushes directly to the manifest file, bypassing the updater
endpoint. So the agent process itself never sees a rejection event from a batch push.

**Revised approach (honest to the architecture):**

After a batch push, the dispatcher **knows** which devices were rejected and why. It already
has the recommendations. The simplest way to surface this on the agent dashboard without adding
inter-process wiring is:

- The dispatcher writes a small sidecar file `manifests/<device_id>.rejection.json` for each
  rejected device (same directory as the manifest, never loaded by the agent startup, never
  affects gating).
- The agent's `/status` endpoint additionally reads this sidecar (if it exists) and includes
  `"last_rejection": { ... }` in the JSON. If no sidecar, `"last_rejection": null`.
- The dashboard JS checks `d.last_rejection` and renders the ⚠️ badge + fix text.

The sidecar is **written by the dispatcher, read by the agent's `/status` route** — zero changes
to startup, gate, or manifest loading. The dispatcher already knows the manifest path for each
device from `batch_targets.yaml`.

**Expected Outcomes**
- After a rejected push, `GET /status` on the affected agent includes:
  ```json
  {
    ...existing fields...,
    "last_rejection": {
      "timestamp": "2025-07-...",
      "violations": ["..."],
      "recommendations": [{ "fix": "...", "safe_capability_set": [...] }]
    }
  }
  ```
- Devices that were never rejected (or whose rejection sidecar has been cleared) return
  `"last_rejection": null`
- The dashboard shows a ⚠️ **BLOCKED — last push rejected** banner with the first `fix` string
  when `last_rejection` is non-null
- Sidecar is deleted (or overwritten with `null`) when a push subsequently PASSES for that device
  — the dispatcher handles this in its apply branch

**Relevant Context**
- Sidecar path convention: `{manifest_path_stem}.rejection.json` — e.g.
  `manifests/example_monitor.rejection.json` alongside `manifests/example_monitor.yaml`
- `agent/main.py`'s `/status` route already has `manifest_path` in closure scope — derive the
  sidecar path as `Path(manifest_path).with_suffix('.rejection.json')`
- Dispatcher already has the `current` manifest path per device from `batch_targets.yaml` —
  derive the sidecar path the same way
- No new dependencies; `json` and `pathlib` are stdlib

**Todo List**
1. In `batch_dispatcher.py` `_run_batch` loop:
   - On REJECTED: write `{manifest_path_stem}.rejection.json` with `timestamp`, `violations`,
     and `recommendations` (already computed in Sub-Task 2) using `json.dumps`
   - On APPLIED: delete `{manifest_path_stem}.rejection.json` if it exists (`Path.unlink(missing_ok=True)`)
2. In `agent/main.py` `/status` route:
   - Derive `sidecar = Path(manifest_path).with_suffix('.rejection.json')`
   - If sidecar exists, read and parse it; include as `"last_rejection"` in the response dict
   - If not, include `"last_rejection": null`
3. In the `/` dashboard HTML/JS:
   - After updating DOM from `/status`, check `d.last_rejection`
   - If non-null: show a `<div id="rejection-banner">` with ⚠️ and `d.last_rejection.recommendations[0].fix`
   - If null: hide the banner
   - Style the banner with inline CSS (red border, amber background — consistent with existing inline style)

**Status**: [ ] pending

---

## Sub-Task 5 — Tests

**Intent**  
Add tests covering all new behaviour. Reuse existing fixture and helper patterns throughout.
Existing 66 tests must still pass; report new total.

**Expected Outcomes**
- `tests/test_recommender.py` — unit tests for `build_recommendations`:
  - capability boundary violation → correct `violation_type`, non-empty `safe_capability_set`
  - unsupported version violation → correct `violation_type`, `supported_versions` populated
  - role change violation → correct `violation_type`
  - passing GateResult → empty recommendations list
  - multi-violation result → one Recommendation per violation

- `tests/test_batch_dispatcher.py` additions (new test class, do not modify existing):
  - `recommendations` key present on every device result
  - rejected device has non-empty `recommendations` with correct `violation_type`
  - passing device has `recommendations: []`

- `tests/test_status_endpoint.py` additions (new test class):
  - `/status` returns `"last_rejection": null` when no sidecar exists
  - `/status` returns structured `last_rejection` when sidecar is present
  - After a simulated PASS (sidecar deleted), `last_rejection` returns to `null`

- `tests/test_recommendations_endpoint.py` (new file):
  - `GET /recommendations` returns empty sentinel before any push
  - After a simulated push with rejections, returns structured data with correct device IDs

**Relevant Context**
- Use `_write_manifest` helper pattern from `test_status_endpoint.py`
- Use `fleet` fixture pattern from `test_batch_dispatcher.py` for dispatcher tests
- `build_recommendations` is pure (no I/O) — test it with in-memory `GateResult` and
  `AgentManifest` objects, same as `test_gate.py` tests the gate

**Status**: [ ] pending

---

## Dependency Graph

```
Sub-Task 1 (recommender.py)
    └── Sub-Task 2 (wire into /push + client display)
            └── Sub-Task 3 (GET /recommendations endpoint)
    └── Sub-Task 4 (sidecar + /status + dashboard badge)
            depends on Sub-Task 2 for recommendation dicts
Sub-Task 5 (tests) — after Sub-Tasks 1–4
```

Sub-Tasks 2 and 4 can be implemented in either order after Sub-Task 1.
Sub-Task 5 is last.

---

## Files Changed / Created

| File | Change |
|---|---|
| `deploy_gate/recommender.py` | **NEW** — pure recommendation logic |
| `deploy_gate/__init__.py` | add exports |
| `batch_dispatcher.py` | import recommender; wire into `_run_batch`; add `_last_recommendations`; add `/recommendations` route; write/delete sidecar files |
| `batch_push_client.py` | print first `fix` line per rejected device |
| `agent/main.py` | `/status` reads sidecar; `/` dashboard shows rejection banner |
| `tests/test_recommender.py` | **NEW** |
| `tests/test_recommendations_endpoint.py` | **NEW** |
| `tests/test_batch_dispatcher.py` | new test class (no edits to existing) |
| `tests/test_status_endpoint.py` | new test class (no edits to existing) |

**Not touched:** `deploy_gate/gate.py`, `roles/validator.py`, `roles/loader.py`,
`manifests/loader.py`, `manifests/schema.py`, `agent/startup.py`,
`agent/capability_router.py`, `agent/modules/*`, `batch_targets.yaml`, any manifest YAML.
