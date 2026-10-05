import json
from io import BytesIO
from typing import Any
from urllib import error

import pytest
import yaml
from azure.core.exceptions import ClientAuthenticationError
from helpers import CONFIG, FabricRepo, FakeCredential, assert_reported

from fabkit import connections
from fabkit.config import load_deploy_config
from fabkit.connections import (
    DEFAULT_CONNECTION_VARIABLES,
    collect_connection_ids,
    get_json,
    verify_connections,
)
from fabkit.errors import FabkitError

CONN_A = "aaaaaaaa-0000-0000-0000-000000000001"
CONN_B = "bbbbbbbb-0000-0000-0000-000000000002"


def collect(
    repo: FabricRepo, environment: str, pattern: str = DEFAULT_CONNECTION_VARIABLES
) -> dict[str, list[str]]:
    return collect_connection_ids(load_deploy_config(repo.config), environment, pattern)


def verify(repo: FabricRepo, environment: str) -> None:
    verify_connections(
        FakeCredential(),
        load_deploy_config(repo.config),
        environment,
        DEFAULT_CONNECTION_VARIABLES,
    )


def write_default_binding(repo: FabricRepo) -> None:
    binding = {"default": {"connection_id": {"DEV": CONN_A, "PROD": CONN_B}}}
    repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))


def include_variable_libraries(repo: FabricRepo) -> None:
    repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, VariableLibrary]"))


def write_variable_library(repo: FabricRepo) -> None:
    include_variable_libraries(repo)
    variables = [{"name": "SqlConnectionID", "value": CONN_A}, {"name": "Other", "value": "x"}]
    repo.write("src/vars.VariableLibrary/variables.json", json.dumps({"variables": variables}))
    overrides = [{"name": "SqlConnectionID", "value": CONN_B}]
    repo.write(
        "src/vars.VariableLibrary/valueSets/PROD.json",
        json.dumps({"name": "PROD", "variableOverrides": overrides}),
    )


# The default binding is collected for the target environment only.
def test_collects_default_binding_for_target(fabric_repo: FabricRepo) -> None:
    write_default_binding(fabric_repo)

    assert list(collect(fabric_repo, "PROD")) == [CONN_B]


# A per-model binding under _ALL_ applies to every environment and names the model.
def test_collects_model_binding_under_all(fabric_repo: FabricRepo) -> None:
    models = [{"semantic_model_name": "sales", "connection_id": {"_ALL_": CONN_A}}]
    fabric_repo.write(
        "src/parameter.yml", yaml.safe_dump({"semantic_model_binding": {"models": models}})
    )

    found = collect(fabric_repo, "PROD")

    assert found == {CONN_A: ["parameter.yml: semantic_model_binding.models (sales)"]}


# Variable-library values matching the pattern are collected, with value-set overrides applied.
def test_collects_variable_library_values(fabric_repo: FabricRepo) -> None:
    write_variable_library(fabric_repo)

    assert list(collect(fabric_repo, "DEV")) == [CONN_A]
    assert list(collect(fabric_repo, "PROD")) == [CONN_B]


# The variable name pattern is configurable.
def test_custom_variable_pattern(fabric_repo: FabricRepo) -> None:
    write_variable_library(fabric_repo)

    assert list(collect(fabric_repo, "DEV", "^Other$")) == ["x"]


# An ID the service principal cannot see stops the deploy, naming the ID and where it is set.
def test_unresolved_id_fails(fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    write_default_binding(fabric_repo)
    monkeypatch.setattr(connections, "list_connections", lambda _credential: {})

    with pytest.raises(FabkitError) as raised:
        verify(fabric_repo, "PROD")

    assert_reported(raised.value.details, CONN_B)
    assert_reported(raised.value.details, "parameter.yml")


# Resolved IDs pass.
def test_resolved_ids_pass(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_default_binding(fabric_repo)
    monkeypatch.setattr(connections, "list_connections", lambda _credential: {CONN_B: "warehouse"})

    verify(fabric_repo, "PROD")

    assert f"Connection resolved: {CONN_B} (warehouse)" in capsys.readouterr().out


# Nothing configured means no API call.
def test_nothing_configured_skips_api(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_credential: Any) -> dict[str, str]:
        raise AssertionError("must not call the API")

    monkeypatch.setattr(connections, "list_connections", fail)

    verify(fabric_repo, "PROD")


# list_connections follows continuationUri across pages.
def test_list_connections_follows_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        f"{connections.FABRIC_API_ROOT}/connections": {
            "value": [{"id": "1", "displayName": "a"}],
            "continuationUri": "next",
        },
        "next": {"value": [{"id": "2", "displayName": "b"}]},
    }
    monkeypatch.setattr(connections, "get_json", lambda url, _token: pages[url])

    assert connections.list_connections(FakeCredential()) == {"1": "a", "2": "b"}


# An HTTP error from the Fabric API becomes a FabkitError with the status and body.
def test_get_json_http_error_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_request: Any, **_: Any) -> Any:
        raise error.HTTPError("https://x", 403, "Forbidden", {}, BytesIO(b"no access"))  # type: ignore[arg-type]

    monkeypatch.setattr(connections.request, "urlopen", forbidden)

    with pytest.raises(FabkitError, match="HTTP 403") as raised:
        get_json("https://x", "token")
    assert raised.value.details == ["no access"]


# A network failure becomes a FabkitError naming the URL.
def test_get_json_network_error_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(_request: Any, **_: Any) -> Any:
        raise error.URLError("connection reset")

    monkeypatch.setattr(connections.request, "urlopen", unreachable)

    with pytest.raises(FabkitError, match="GET https://x failed: .*connection reset"):
        get_json("https://x", "token")


# A response body that is not JSON becomes a FabkitError.
def test_get_json_non_json_body_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(BytesIO):
        def __enter__(self) -> "Response":
            return self

    monkeypatch.setattr(
        connections.request, "urlopen", lambda *_args, **_kwargs: Response(b"<html>")
    )

    with pytest.raises(FabkitError, match="Unexpected response from https://x"):
        get_json("https://x", "token")


# A malformed parameter.yml is reported by file name.
def test_malformed_parameter_file_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("src/parameter.yml", "a: [unclosed")

    with pytest.raises(FabkitError, match="parameter.yml: invalid YAML"):
        collect(fabric_repo, "DEV")


# A malformed variables.json is reported by file name.
def test_malformed_variables_file_fails(fabric_repo: FabricRepo) -> None:
    include_variable_libraries(fabric_repo)
    fabric_repo.write("src/vars.VariableLibrary/variables.json", "{not json")

    with pytest.raises(FabkitError, match="variables.json: invalid JSON"):
        collect(fabric_repo, "DEV")


# A failed Azure login becomes a FabkitError.
def test_login_failure_fails() -> None:
    class FailingCredential(FakeCredential):
        def get_token(self, *_: Any) -> Any:
            raise ClientAuthenticationError("no secret")

    with pytest.raises(FabkitError, match="Azure login failed") as raised:
        connections.list_connections(FailingCredential())
    assert_reported(raised.value.details, "no secret")


# A null or empty variable value is not collected as a connection ID.
def test_empty_variable_values_are_skipped(fabric_repo: FabricRepo) -> None:
    include_variable_libraries(fabric_repo)
    variables = [
        {"name": "AConnectionID", "value": None},
        {"name": "BConnectionID", "value": ""},
    ]
    fabric_repo.write(
        "src/vars.VariableLibrary/variables.json", json.dumps({"variables": variables})
    )

    assert collect(fabric_repo, "DEV") == {}


# A null override replaces the base value and is skipped, with no fallback to the base value.
def test_null_override_hides_base_value(fabric_repo: FabricRepo) -> None:
    write_variable_library(fabric_repo)
    overrides = [{"name": "SqlConnectionID", "value": None}]
    fabric_repo.write(
        "src/vars.VariableLibrary/valueSets/PROD.json",
        json.dumps({"name": "PROD", "variableOverrides": overrides}),
    )

    assert collect(fabric_repo, "PROD") == {}


# Variable libraries are not scanned when item_types_in_scope leaves them out.
def test_variable_libraries_out_of_scope_are_skipped(fabric_repo: FabricRepo) -> None:
    write_variable_library(fabric_repo)
    fabric_repo.write("config.yml", CONFIG)

    assert collect(fabric_repo, "DEV") == {}


# A legacy binding list applies its connection ID to every environment.
def test_collects_legacy_binding(fabric_repo: FabricRepo) -> None:
    binding = [{"connection_id": CONN_A, "semantic_model_name": "sales"}]
    fabric_repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))

    found = collect(fabric_repo, "PROD")

    assert found == {CONN_A: ["parameter.yml: semantic_model_binding[1] (sales)"]}


# A legacy binding with a per-environment mapping is collected for the target only.
def test_collects_legacy_binding_mapping_for_target(fabric_repo: FabricRepo) -> None:
    binding = [
        {"connection_id": {"DEV": CONN_A, "PROD": CONN_B}, "semantic_model_name": "sales"},
        "not an entry",
    ]
    fabric_repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))

    assert list(collect(fabric_repo, "PROD")) == [CONN_B]


# Non-mapping models entries are skipped, not a traceback.
def test_non_mapping_model_entries_are_skipped(fabric_repo: FabricRepo) -> None:
    binding = {"models": ["abc", {"semantic_model_name": "m", "connection_id": {"_ALL_": CONN_A}}]}
    fabric_repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))

    assert list(collect(fabric_repo, "DEV")) == [CONN_A]
