"""
Tests for manifests/schema.py (AgentManifest) and manifests/loader.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manifests.loader import load_manifest
from manifests.schema import AgentManifest


class TestAgentManifestSchema:
    def test_valid_manifest_constructs(self):
        m = AgentManifest(
            fleet_schema_version="4.2",
            device_id="dev-001",
            device_owner_role="field_tech",
            capability_set=["telemetry_collect"],
            environment="production",
        )
        assert m.fleet_schema_version == "4.2"
        assert m.device_owner_role == "field_tech"

    def test_empty_fleet_schema_version_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="   ",
                device_id="dev-001",
                device_owner_role="field_tech",
                capability_set=["telemetry_collect"],
                environment="production",
            )

    def test_empty_device_id_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="",
                device_owner_role="field_tech",
                capability_set=["telemetry_collect"],
                environment="production",
            )

    def test_empty_role_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="dev-001",
                device_owner_role="",
                capability_set=["telemetry_collect"],
                environment="production",
            )

    def test_empty_capability_set_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="dev-001",
                device_owner_role="field_tech",
                capability_set=[],
                environment="production",
            )

    def test_duplicate_capabilities_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="dev-001",
                device_owner_role="field_tech",
                capability_set=["telemetry_collect", "telemetry_collect"],
                environment="production",
            )

    def test_empty_environment_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="dev-001",
                device_owner_role="field_tech",
                capability_set=["telemetry_collect"],
                environment="",
            )

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            AgentManifest(
                fleet_schema_version="4.2",
                device_id="dev-001",
                # device_owner_role missing
                capability_set=["telemetry_collect"],
                environment="production",
            )


class TestManifestLoader:
    def test_load_yaml_manifest(self, tmp_path):
        f = tmp_path / "m.yaml"
        f.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'dev-001'\n"
            "device_owner_role: read_only\n"
            "capability_set:\n  - telemetry_collect\n"
            "environment: production\n"
        )
        m = load_manifest(f)
        assert m.device_id == "dev-001"
        assert m.device_owner_role == "read_only"

    def test_load_toml_manifest(self, tmp_path):
        f = tmp_path / "m.toml"
        f.write_bytes(
            b'fleet_schema_version = "4.2"\n'
            b'device_id = "dev-002"\n'
            b'device_owner_role = "monitor"\n'
            b'capability_set = ["telemetry_collect", "diagnostics_run"]\n'
            b'environment = "staging"\n'
        )
        m = load_manifest(f)
        assert m.device_id == "dev-002"
        assert set(m.capability_set) == {"telemetry_collect", "diagnostics_run"}

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "m.json"
        f.write_text("{}")
        with pytest.raises(ValueError, match="Unsupported manifest format"):
            load_manifest(f)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "nonexistent.yaml")

    def test_non_mapping_yaml_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="must be a YAML/TOML mapping"):
            load_manifest(f)

    def test_invalid_schema_raises_validation_error(self, tmp_path):
        from pydantic import ValidationError

        f = tmp_path / "bad.yaml"
        f.write_text(
            "fleet_schema_version: '4.2'\n"
            "device_id: 'dev-001'\n"
            # device_owner_role missing
            "capability_set:\n  - telemetry_collect\n"
            "environment: production\n"
        )
        with pytest.raises(ValidationError):
            load_manifest(f)
