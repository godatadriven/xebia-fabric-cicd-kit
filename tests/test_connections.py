import json
from typing import Any

import pytest
import yaml
from helpers import CONFIG, CONN, FabricRepo, FakeCredential, FakeFabric, assert_reported

from fabkit.config import load_deploy_config
from fabkit.connections import (
    DEFAULT_CONNECTION_VARIABLES,
    collect_connection_ids,
    verify_connections,
)
from fabkit.errors import FabkitError

CONN_A = "aaaaaaaa-0000-0000-0000-000000000001"
CONN_B = "bbbbbbbb-0000-0000-0000-000000000002"
LIBRARY = "src/vars.VariableLibrary"


def binding(value: Any) -> str:
    return yaml.safe_dump({"semantic_model_binding": value})


def collect(
    repo: FabricRepo, environment: str, pattern: str = DEFAULT_CONNECTION_VARIABLES
) -> dict[str, list[str]]:
    return collect_connection_ids(load_deploy_config(repo.config), environment, pattern)


# Bindings are collected for the target environment, named by where they are set.
@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param(
            {
                "src/parameter.yml": binding(
                    {"default": {"connection_id": {"DEV": CONN_A, "PROD": CONN_B}}}
                )
            },
            {CONN_B: ["parameter.yml: semantic_model_binding.default"]},
            id="default",
        ),
        pytest.param(
            {
                "src/parameter.yml": binding(
                    {
                        "models": [
                            "abc",
                            {"semantic_model_name": "sales", "connection_id": {"_ALL_": CONN_A}},
                        ]
                    }
                )
            },
            {CONN_A: ["parameter.yml: semantic_model_binding.models[2] (sales)"]},
            id="model-under-all",
        ),
        pytest.param(
            {
                "src/parameter.yml": binding(
                    [{"semantic_model_name": "sales", "connection_id": CONN_A}]
                )
            },
            {CONN_A: ["parameter.yml: semantic_model_binding[1] (sales)"]},
            id="legacy",
        ),
        pytest.param(
            {
                "src/template.yml": binding({"default": {"connection_id": {"_ALL_": CONN_B}}}),
                "src/parameter.yml": "extend:\n  - template.yml\n",
            },
            {CONN_B: ["parameter.yml: semantic_model_binding.default"]},
            id="extend-template",
        ),
    ],
)
def test_collects_bindings(
    fabric_repo: FabricRepo, files: dict[str, str], expected: dict[str, list[str]]
) -> None:
    for path, text in files.items():
        fabric_repo.write(path, text)

    assert collect(fabric_repo, "PROD") == expected


# A $ENV: connection_id is collected as the value of its environment variable.
def test_collects_env_connection_id_value(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabric_repo.write(
        "config.yml", CONFIG + "features: [enable_environment_variable_replacement]\n"
    )
    fabric_repo.write(
        "src/parameter.yml", binding({"default": {"connection_id": {"_ALL_": "$ENV:SM"}}})
    )
    monkeypatch.setenv("SM", CONN_A)

    assert list(collect(fabric_repo, "PROD")) == [CONN_A]


def variable_library(
    repo: FabricRepo, item_types: str, variables: dict[str, Any], prod: dict[str, Any]
) -> None:
    repo.write("config.yml", CONFIG.replace("[Notebook]", item_types))
    repo.write(
        f"{LIBRARY}/variables.json",
        json.dumps({"variables": [{"name": k, "value": v} for k, v in variables.items()]}),
    )
    overrides = [{"name": k, "value": v} for k, v in prod.items()]
    repo.write(
        f"{LIBRARY}/valueSets/PROD.json",
        json.dumps({"name": "PROD", "variableOverrides": overrides}),
    )


# Variable-library values whose name matches are collected, with the value set applied.
@pytest.mark.parametrize(
    ("item_types", "variables", "prod", "pattern", "expected"),
    [
        pytest.param(
            "[VariableLibrary]",
            {"SqlConnectionID": CONN_A, "Other": "x"},
            {"SqlConnectionID": CONN_B},
            DEFAULT_CONNECTION_VARIABLES,
            [CONN_B],
            id="override",
        ),
        pytest.param(
            "[VariableLibrary]",
            {"SqlConnectionID": CONN_A, "Other": "x"},
            {},
            "^Other$",
            ["x"],
            id="custom-pattern",
        ),
        pytest.param(
            "[VariableLibrary]",
            {"AConnectionID": None, "BConnectionID": ""},
            {},
            DEFAULT_CONNECTION_VARIABLES,
            [],
            id="empty-values",
        ),
        pytest.param(
            "[VariableLibrary]",
            {"SqlConnectionID": CONN_A},
            {"SqlConnectionID": None},
            DEFAULT_CONNECTION_VARIABLES,
            [],
            id="null-override",
        ),
        pytest.param(
            "[Notebook]",
            {"SqlConnectionID": CONN_A},
            {},
            DEFAULT_CONNECTION_VARIABLES,
            [],
            id="out-of-scope",
        ),
    ],
)
def test_collects_variable_library_values(
    fabric_repo: FabricRepo,
    item_types: str,
    variables: dict[str, Any],
    prod: dict[str, Any],
    pattern: str,
    expected: list[str],
) -> None:
    variable_library(fabric_repo, item_types, variables, prod)

    assert list(collect(fabric_repo, "PROD", pattern)) == expected


# A malformed parameter.yml or variables.json is reported by file name.
@pytest.mark.parametrize(
    ("path", "text", "message"),
    [
        ("src/parameter.yml", "a: [unclosed", "parameter.yml: invalid YAML"),
        (f"{LIBRARY}/variables.json", "{not json", "variables.json: invalid JSON"),
    ],
)
def test_malformed_file_fails(fabric_repo: FabricRepo, path: str, text: str, message: str) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[VariableLibrary]"))
    fabric_repo.write(path, text)

    with pytest.raises(FabkitError, match=message):
        collect(fabric_repo, "DEV")


def verify(repo: FabricRepo) -> None:
    verify_connections(
        FakeCredential(), load_deploy_config(repo.config), "PROD", DEFAULT_CONNECTION_VARIABLES
    )


# An ID the service principal cannot see fails, naming the ID and where it is set.
def test_unresolved_id_fails(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric_repo.write(
        "src/parameter.yml", binding({"default": {"connection_id": {"_ALL_": CONN_B}}})
    )

    with pytest.raises(FabkitError, match="Connection check failed") as raised:
        verify(fabric_repo)

    assert_reported(raised.value.details, CONN_B)
    assert_reported(raised.value.details, "parameter.yml: semantic_model_binding.default")


# A resolved ID passes and is printed; with nothing configured, the API is not called.
def test_resolved_or_absent_ids_pass(
    fabric_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    verify(fabric_repo)
    assert fabric.connection_lookups == 0

    fabric_repo.write("src/parameter.yml", binding({"default": {"connection_id": {"_ALL_": CONN}}}))
    verify(fabric_repo)

    assert f"Connection resolved: {CONN} (warehouse)" in capsys.readouterr().out
