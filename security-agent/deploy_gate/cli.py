"""
deploy-gate CLI — pre-deploy manifest safety gate.

Usage:
    deploy-gate check --current <path> --proposed <path> [--output text|json]
                      [--allow-role-change] [--supported-version VER ...]

Exit codes:
    0  Gate passed — update is safe to apply.
    1  Gate failed — one or more violations detected.
    2  Admin authorisation required — --allow-role-change used without
       DEPLOY_GATE_ADMIN_TOKEN environment variable being set.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

import typer

from deploy_gate.gate import GateResult, check_manifest_update
from roles.loader import load_role_map

app = typer.Typer(
    name="deploy-gate",
    help="Pre-deploy manifest safety gate for security agent updates.",
    add_completion=False,
)

_ADMIN_TOKEN_ENV = "DEPLOY_GATE_ADMIN_TOKEN"


def _require_admin_token(flag_name: str) -> None:
    """Exit with code 2 if the admin token env var is not set.

    Called before any manifest loading so there is no partial evaluation.
    """
    token = os.environ.get(_ADMIN_TOKEN_ENV, "").strip()
    if not token:
        typer.echo(
            f"admin token required for {flag_name}. "
            f"Set the {_ADMIN_TOKEN_ENV} environment variable.",
            err=True,
        )
        raise typer.Exit(code=2)


@app.command("check")
def check(
    current: str = typer.Option(..., "--current", help="Path to the currently deployed manifest."),
    proposed: str = typer.Option(..., "--proposed", help="Path to the proposed new manifest."),
    output: str = typer.Option("text", "--output", help="Output format: text or json."),
    allow_role_change: bool = typer.Option(
        False,
        "--allow-role-change",
        help=(
            "Permit the device_owner_role to change. "
            f"Requires {_ADMIN_TOKEN_ENV} to be set."
        ),
    ),
    supported_version: Optional[list[str]] = typer.Option(
        None,
        "--supported-version",
        help="Allowed fleet_schema_version values. Repeatable. Omit to accept any version.",
    ),
) -> None:
    """Check whether a proposed manifest update is safe to deploy."""

    # ── Admin gate: must run BEFORE any manifest loading ─────────────────────
    if allow_role_change:
        _require_admin_token("--allow-role-change")

    # ── Load role map ─────────────────────────────────────────────────────────
    role_map = load_role_map()

    # ── Run the gate ──────────────────────────────────────────────────────────
    result: GateResult = check_manifest_update(
        current_path=current,
        proposed_path=proposed,
        role_map=role_map,
        supported_versions=supported_version if supported_version else None,
        allow_role_change=allow_role_change,
    )

    # ── Output ────────────────────────────────────────────────────────────────
    if output == "json":
        typer.echo(json.dumps({"passed": result.passed, "violations": result.violations}))
    else:
        typer.echo(str(result))

    if not result.passed:
        raise typer.Exit(code=1)
