"""
Role map loader — reads roles/role_map.yaml and returns a typed mapping
of role name → frozenset of permitted capabilities.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Default path relative to this file's location.
_DEFAULT_ROLE_MAP_PATH = Path(__file__).parent / "role_map.yaml"


def load_role_map(path: str | Path | None = None) -> dict[str, frozenset[str]]:
    """Load the role-to-capability map from a YAML file.

    Args:
        path: Path to the role map YAML file.  Defaults to the bundled
              ``roles/role_map.yaml``.

    Returns:
        A dict mapping each role name to a frozenset of permitted capability
        strings.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValueError: If the file structure is invalid.
    """
    resolved = Path(path) if path is not None else _DEFAULT_ROLE_MAP_PATH

    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "roles" not in raw:
        raise ValueError(
            f"Role map at '{resolved}' must be a YAML mapping with a top-level 'roles' key."
        )

    roles_raw = raw["roles"]
    if not isinstance(roles_raw, dict):
        raise ValueError("'roles' must be a YAML mapping of role_name → list[capability].")

    result: dict[str, frozenset[str]] = {}
    for role_name, caps in roles_raw.items():
        if not isinstance(caps, list):
            raise ValueError(
                f"Capabilities for role '{role_name}' must be a list, got {type(caps).__name__}."
            )
        result[role_name] = frozenset(caps)

    return result
