"""
AgentManifest — canonical Pydantic model for a device deployment manifest.

Every agent deployment manifest (YAML or TOML) must conform to this schema.
The manifest is the single source of truth for what an agent is allowed to do
on a given device.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class AgentManifest(BaseModel):
    """Validated representation of a device deployment manifest."""

    # Shared protocol version string declared in every manifest.
    # Checked at startup and included in the capability report sent to the
    # fleet collector.  Static field — never computed at runtime.
    fleet_schema_version: str

    # Unique identifier for the device this agent is deployed on.
    device_id: str

    # Must match a key in roles/role_map.yaml.  Determines the maximum
    # allowed capability set for this deployment.
    device_owner_role: str

    # Subset of capabilities permitted by device_owner_role that this
    # specific agent instance is authorised to use.
    capability_set: list[str]

    # Deployment environment tag — informational, not used for access control.
    environment: str

    @field_validator("fleet_schema_version")
    @classmethod
    def version_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("fleet_schema_version must not be empty")
        return v

    @field_validator("device_id")
    @classmethod
    def device_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("device_id must not be empty")
        return v

    @field_validator("device_owner_role")
    @classmethod
    def role_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("device_owner_role must not be empty")
        return v

    @field_validator("capability_set")
    @classmethod
    def capabilities_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("capability_set must contain at least one capability")
        duplicates = {c for c in v if v.count(c) > 1}
        if duplicates:
            raise ValueError(f"capability_set contains duplicates: {sorted(duplicates)}")
        return v

    @field_validator("environment")
    @classmethod
    def environment_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("environment must not be empty")
        return v
