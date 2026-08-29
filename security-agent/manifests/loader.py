"""
Manifest loader — reads a .yaml or .toml deployment manifest from disk
and returns a validated AgentManifest instance.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

from manifests.schema import AgentManifest


def load_manifest(path: str | Path) -> AgentManifest:
    """Load and validate a manifest file.

    Supports .yaml / .yml and .toml formats.

    Args:
        path: Filesystem path to the manifest file.

    Returns:
        A validated AgentManifest instance.

    Raises:
        ValueError: If the file extension is not supported.
        pydantic.ValidationError: If the manifest fails schema validation.
        FileNotFoundError: If the path does not exist.
    """
    path = Path(path)

    if path.suffix in {".yaml", ".yml"}:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    elif path.suffix == ".toml":
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    else:
        raise ValueError(
            f"Unsupported manifest format '{path.suffix}'. "
            "Expected .yaml, .yml, or .toml."
        )

    if not isinstance(raw, dict):
        raise ValueError(f"Manifest at '{path}' must be a YAML/TOML mapping, got {type(raw).__name__}")

    return AgentManifest(**raw)
