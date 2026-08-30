"""
tools/parallel_audit.py
=======================

Build-time audit tool that spawns TWO independent subagents in parallel to
audit the security-agent codebase and produces a consolidated findings report.

Subagent A — Privilege-enforcement audit
    Audits every code path that enforces the role/capability boundary:
      - roles/validator.py
      - deploy_gate/gate.py
      - agent/startup.py
    Reports any location where privilege enforcement could theoretically be
    bypassed, or where the "two independent gates" guarantee is not upheld.

Subagent B — Atomic-write audit
    Audits every code path involving file writes:
      - manifest updates via os.replace (updater module)
      - rejection sidecars (batch_dispatcher.py)
      - the atomic os.replace pattern
    Reports any location where a partial or non-atomic write could occur.

The two subagents run concurrently (Bob spawn_subagent, type "explore").
Their individual findings are reconciled into:
    tools/parallel_audit_report.md

Usage (run from repo root):
    python tools/parallel_audit.py

This script does NOT modify any existing source files.
"""

# NOTE: This file documents the audit design.  The actual parallel execution
# is performed by Bob's agent runtime via the spawn_subagent invocations
# recorded at the bottom of this docstring.
#
# Sub-agent A prompt  →  SUBAGENT_A_PROMPT
# Sub-agent B prompt  →  SUBAGENT_B_PROMPT

SUBAGENT_A_PROMPT = """
You are Subagent A — Privilege-Enforcement Auditor.

Audit the following files in the security-agent/ subdirectory of this repo:
  1. security-agent/roles/validator.py
  2. security-agent/deploy_gate/gate.py
  3. security-agent/agent/startup.py
  4. security-agent/deploy_gate/cli.py      (the CLI wrapper)
  5. security-agent/agent/main.py           (entry-point wiring)
  6. security-agent/batch_dispatcher.py     (batch gate invocation)
  7. security-agent/agent/modules/updater.py (per-device gate call)

For each file, read it completely and answer these specific questions:

Q1  Is assert_capabilities_within_role() the ONLY function that enforces the
    capability boundary, and is it the single source of truth for both the
    pre-deploy gate AND the startup assertion?

Q2  Are there any code paths where:
    (a) the gate check is skipped entirely,
    (b) a capability is granted without going through assert_capabilities_within_role,
    (c) the allow_role_change flag bypasses the capability check (not just the
        role-change check), or
    (d) a manifest could be loaded and used without the privilege assertion running?

Q3  In deploy_gate/cli.py — does the admin token check run BEFORE any manifest
    is loaded, as documented?

Q4  In agent/main.py — does run_startup_check() run BEFORE asyncio.run() or
    any FastAPI initialisation?

Q5  In batch_dispatcher.py — does every device path call check_manifest_update,
    and are per-device errors isolated so one bad device cannot skip another
    device's gate check?

Q6  Does the startup assertion re-load the role map independently from the
    pre-deploy gate (i.e., they do not share a cached role_map object)?

For each potential issue found, state:
  - File and line number
  - What the issue is
  - Severity: CRITICAL / MODERATE / INFO
  - Whether it violates the two-gate independence guarantee

End your report with a one-paragraph VERDICT: does the two-gate architecture
hold as documented?
"""

SUBAGENT_B_PROMPT = """
You are Subagent B — Atomic-Write Auditor.

Audit the following files in the security-agent/ subdirectory of this repo:
  1. security-agent/agent/modules/updater.py     (atomic replace in apply_update)
  2. security-agent/batch_dispatcher.py          (_apply_update, _write_rejection_sidecar,
                                                   _clear_rejection_sidecar)

For each file, read it completely and answer these specific questions:

Q1  In updater.py apply_update():
    (a) Is the staged file written to a .tmp path and then renamed via os.replace?
    (b) Is the .tmp file guaranteed to be cleaned up even if os.replace raises?
    (c) After sys.exit(0) is called, is there any window where the .tmp file
        could be left on disk (i.e. is the finally-cleanup in post_apply_update
        called even after sys.exit)?
    (d) Is the staging file (.staged) cleaned up properly in the finally block
        of post_apply_update?

Q2  In batch_dispatcher.py _apply_update():
    (a) Is the pattern shutil.copy2 → os.replace truly atomic on POSIX?
    (b) On Windows (where os.replace is not guaranteed atomic on all
        filesystems), does the code add any additional safeguard?
    (c) If shutil.copy2 succeeds but os.replace raises, is the .tmp file
        left on disk?

Q3  In batch_dispatcher.py _write_rejection_sidecar():
    (a) Does sidecar.write_text() write atomically, or could a crash between
        open and close leave a truncated / partial JSON file?
    (b) Is there a .tmp + os.replace pattern for the sidecar, or does the
        code write directly to the final path?

Q4  In batch_dispatcher.py _clear_rejection_sidecar():
    (a) Is missing_ok=True used so that a missing sidecar does not raise?

Q5  Are there any concurrent-write risks?  (e.g., two batch pushes running
    simultaneously to the same device)

For each potential issue found, state:
  - File and line number
  - What the issue is
  - Severity: CRITICAL / MODERATE / INFO
  - Whether it could result in a reader seeing a partial or corrupted manifest

End your report with a one-paragraph VERDICT: do the atomic-write guarantees
hold as documented?
"""

if __name__ == "__main__":
    print(__doc__)
    print("=" * 60)
    print("Subagent A prompt (privilege enforcement):")
    print(SUBAGENT_A_PROMPT)
    print("=" * 60)
    print("Subagent B prompt (atomic writes):")
    print(SUBAGENT_B_PROMPT)
    print("=" * 60)
    print("See tools/parallel_audit_report.md for the consolidated findings.")
