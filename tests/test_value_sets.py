import json
from pathlib import Path

from helpers import CONFIG, FabricRepo, assert_reported

from fabkit.cli import main
from fabkit.config import load_deploy_config
from fabkit.errors import Findings
from fabkit.value_sets import (
    KNOWN_VALUE_SET_SCHEMA,
    check_value_set_environments,
    check_value_sets,
)


def value_set(root: Path, overrides: list[dict[str, object]], schema: str | None = None) -> Path:
    path = root / "vars.VariableLibrary/valueSets/PROD.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content: dict[str, object] = {"name": "PROD", "variableOverrides": overrides}
    if schema:
        content["$schema"] = schema
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# Overrides with only name and value pass.
def test_valid_overrides_pass(tmp_path: Path) -> None:
    value_set(tmp_path, [{"name": "a", "value": "1"}], KNOWN_VALUE_SET_SCHEMA)
    assert check_value_sets(tmp_path).errors == []


# Overrides pass when the file declares no $schema.
def test_overrides_without_schema_pass(tmp_path: Path) -> None:
    value_set(tmp_path, [{"name": "a", "value": "1"}])
    assert check_value_sets(tmp_path).errors == []


# An override with an extra key fails, naming the key.
def test_extra_key_fails(tmp_path: Path) -> None:
    value_set(tmp_path, [{"name": "a", "value": "1", "note": "x"}])
    assert_reported(check_value_sets(tmp_path).errors, "PROD.json", "note")


# An unknown schema version fails and asks to update the check.
def test_unknown_schema_fails(tmp_path: Path) -> None:
    value_set(tmp_path, [], "https://example.com/valueSet/9.9.9/schema.json")
    assert_reported(check_value_sets(tmp_path).errors, "Update the check")


# Invalid JSON fails.
def test_invalid_json_fails(tmp_path: Path) -> None:
    value_set(tmp_path, []).write_text("{", encoding="utf-8")
    assert len(check_value_sets(tmp_path).errors) == 1


# A value-set file holding a JSON array is reported, not a crash.
def test_array_json_fails(tmp_path: Path) -> None:
    value_set(tmp_path, []).write_text("[]", encoding="utf-8")
    assert_reported(check_value_sets(tmp_path).errors, "PROD.json", "expected a JSON object")


# An override that is not an object is reported, not a crash.
def test_non_object_override_fails(tmp_path: Path) -> None:
    value_set(tmp_path, ["ab"])  # type: ignore[list-item]
    assert_reported(check_value_sets(tmp_path).errors, "PROD.json", "override must be an object")


# A file that is not UTF-8 is reported as invalid, not a crash.
def test_non_utf8_file_fails(tmp_path: Path) -> None:
    value_set(tmp_path, []).write_bytes(b"\xff\xfe")
    assert_reported(check_value_sets(tmp_path).errors, "PROD.json", "invalid JSON")


def library(repo: FabricRepo, value_sets: list[str] | None) -> None:
    """A variable library in scope; value_sets=None leaves out settings.json."""
    repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, VariableLibrary]"))
    repo.write("src/vars.VariableLibrary/variables.json", '{"variables": []}')
    if value_sets is not None:
        repo.write(
            "src/vars.VariableLibrary/settings.json", json.dumps({"valueSetsOrder": value_sets})
        )


def check_environments(repo: FabricRepo) -> Findings:
    return check_value_set_environments(load_deploy_config(repo.config))


# A library with a value set for every environment passes.
def test_value_set_per_environment_passes(fabric_repo: FabricRepo) -> None:
    library(fabric_repo, ["DEV", "PROD"])
    assert check_environments(fabric_repo).errors == []


# An environment without a value set fails, naming the library and the environment.
def test_missing_value_set_fails(fabric_repo: FabricRepo) -> None:
    library(fabric_repo, ["DEV"])
    assert_reported(check_environments(fabric_repo).errors, "vars.VariableLibrary", "'PROD'")


# A library without settings.json fails.
def test_missing_settings_fails(fabric_repo: FabricRepo) -> None:
    library(fabric_repo, None)
    assert_reported(check_environments(fabric_repo).errors, "vars.VariableLibrary", "settings.json")


# A library is not checked when VariableLibrary is not in scope.
def test_out_of_scope_library_passes(fabric_repo: FabricRepo) -> None:
    library(fabric_repo, ["DEV"])
    fabric_repo.write("config.yml", CONFIG)
    assert check_environments(fabric_repo).errors == []


# validate fails on an environment without a value set.
def test_validate_fails_on_missing_value_set(fabric_repo: FabricRepo) -> None:
    library(fabric_repo, ["DEV"])
    assert main(["validate"]) == 1
