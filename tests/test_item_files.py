import json
from pathlib import Path

import pytest
from helpers import CONFIG, FabricRepo, assert_reported

from fabkit.config import load_deploy_config
from fabkit.platform_files import check_logical_ids
from fabkit.value_sets import (
    KNOWN_VALUE_SET_SCHEMA,
    check_value_set_environments,
    check_value_sets,
)


def write(root: Path, files: dict[str, object]) -> None:
    """Each file as JSON, or as raw bytes."""
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


def logical_id(value: object) -> dict[str, object]:
    return {"config": {"logicalId": value}}


ZERO = "00000000-0000-0000-0000-000000000000"


# Unique logicalIds pass; files under .git do not count.
def test_unique_logical_ids_pass(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "a.Notebook/.platform": logical_id("1"),
            "b.Notebook/.platform": logical_id("2"),
            ".git/x.Notebook/.platform": logical_id("1"),
        },
    )

    assert check_logical_ids(tmp_path).errors == []


# A shared or unreadable logicalId fails, naming the file.
@pytest.mark.parametrize(
    ("files", "fragments"),
    [
        pytest.param(
            {"a.Notebook/.platform": logical_id("1"), "b.Notebook/.platform": logical_id("1")},
            ("logicalId 1", "a.Notebook", "b.Notebook"),
            id="duplicate",
        ),
        pytest.param(
            {"a.Notebook/.platform": logical_id(ZERO), "b.Notebook/.platform": logical_id(ZERO)},
            ("placeholder", "unique logicalId"),
            id="shared-placeholder",
        ),
        pytest.param({"a.Notebook/.platform": b"{"}, ("invalid JSON",), id="invalid-json"),
        pytest.param({"a.Notebook/.platform": logical_id(1)}, ("not a string",), id="not-string"),
    ],
)
def test_logical_id_problem_fails(
    tmp_path: Path, files: dict[str, object], fragments: tuple[str, ...]
) -> None:
    write(tmp_path, files)

    assert_reported(check_logical_ids(tmp_path).errors, *fragments)


VALUE_SET = "vars.VariableLibrary/valueSets/PROD.json"


def overrides(*items: object, schema: str | None = KNOWN_VALUE_SET_SCHEMA) -> dict[str, object]:
    content: dict[str, object] = {"name": "PROD", "variableOverrides": list(items)}
    return {**content, "$schema": schema} if schema else content


# Overrides with only name and value pass, with or without a $schema.
@pytest.mark.parametrize("schema", [KNOWN_VALUE_SET_SCHEMA, None])
def test_valid_value_set_passes(tmp_path: Path, schema: str | None) -> None:
    write(tmp_path, {VALUE_SET: overrides({"name": "a", "value": "1"}, schema=schema)})

    assert check_value_sets(tmp_path).errors == []


# A value-set file Fabric would reject, or that fabkit cannot read, fails.
@pytest.mark.parametrize(
    ("content", "fragments"),
    [
        pytest.param(
            overrides({"name": "a", "value": "1", "note": "x"}), ("'a'", "['note']"), id="extra-key"
        ),
        pytest.param(
            overrides(schema=KNOWN_VALUE_SET_SCHEMA.replace("1.0.0", "2.0.0")),
            ("2.0.0", "Update the check"),
            id="new-schema",
        ),
        pytest.param(b"{", ("invalid JSON",), id="invalid-json"),
        pytest.param([1], ("expected a JSON object",), id="array"),
        pytest.param(overrides("a"), ("override must be an object",), id="non-object-override"),
    ],
)
def test_value_set_problem_fails(
    tmp_path: Path, content: object, fragments: tuple[str, ...]
) -> None:
    write(tmp_path, {VALUE_SET: content})

    assert_reported(check_value_sets(tmp_path).errors, *fragments)


def library_errors(repo: FabricRepo, item_types: str, settings: object | None) -> list[str]:
    repo.write("config.yml", CONFIG.replace("[Notebook]", item_types))
    write(repo.root / "src", {"vars.VariableLibrary/variables.json": {"variables": []}})
    if settings is not None:
        write(repo.root / "src", {"vars.VariableLibrary/settings.json": settings})
    return check_value_set_environments(load_deploy_config(repo.config)).errors


# A variable library in scope needs a value set for every environment.
@pytest.mark.parametrize(
    ("item_types", "settings", "fragments"),
    [
        pytest.param("[VariableLibrary]", {"valueSetsOrder": ["DEV", "PROD"]}, None, id="complete"),
        pytest.param("[Notebook]", None, None, id="out-of-scope"),
        pytest.param(
            "[VariableLibrary]",
            {"valueSetsOrder": ["DEV"]},
            ("vars.VariableLibrary", "'PROD'"),
            id="missing-environment",
        ),
        pytest.param("[VariableLibrary]", None, ("settings.json is missing",), id="no-settings"),
    ],
)
def test_value_set_environments(
    fabric_repo: FabricRepo,
    item_types: str,
    settings: object | None,
    fragments: tuple[str, ...] | None,
) -> None:
    errors = library_errors(fabric_repo, item_types, settings)

    if fragments is None:
        assert errors == []
    else:
        assert_reported(errors, *fragments)
