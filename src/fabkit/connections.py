"""Connection pre-flight."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib import error, request

import yaml

from fabkit.config import DeployConfig
from fabkit.errors import FabkitError
from fabkit.parameters import ALL_ENVIRONMENTS_KEY, enumerate_mappings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

FABRIC_API_ROOT = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWER_BI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
HTTP_TIMEOUT_SECONDS = 30

# Variable-library variables whose name matches this hold connection IDs
DEFAULT_CONNECTION_VARIABLES = "ConnectionID$"


def collect_connection_ids(
    config: DeployConfig, environment: str, connection_variables: str
) -> dict[str, list[str]]:
    """Connection IDs the deploy binds in one environment, mapped to where they are set."""
    found: dict[str, list[str]] = {}
    parameter_file = config.parameter_file(environment)
    if parameter_file is not None and parameter_file.is_file():
        for cid, source in _semantic_model_ids(parameter_file, environment):
            found.setdefault(cid, []).append(source)
    item_types = config.item_types(environment)
    if item_types is not None and "VariableLibrary" not in item_types:
        return found
    pattern = re.compile(connection_variables)
    for cid, source in _variable_library_ids(
        config.repository_directory(environment), environment, pattern
    ):
        found.setdefault(cid, []).append(source)
    return found


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as problem:
        raise FabkitError(f"{path.name}: invalid YAML.", [str(problem)]) from problem


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as problem:
        raise FabkitError(f"{path.name}: invalid JSON.", [str(problem)]) from problem


def _environment_value(block: dict[str, Any], environment: str) -> Any:
    if environment in block:
        return block[environment]
    for key, value in block.items():
        if str(key).upper() == ALL_ENVIRONMENTS_KEY:
            return value
    return None


def _semantic_model_ids(parameter_file: Path, environment: str) -> list[tuple[str, str]]:
    parameters = _read_yaml(parameter_file)
    binding = parameters.get("semantic_model_binding") if isinstance(parameters, dict) else None
    blocks: list[tuple[str, Any]] = []
    if isinstance(binding, list):
        for index, entry in enumerate_mappings(binding):
            name = entry.get("semantic_model_name", "<unnamed>")
            blocks.append((f"semantic_model_binding[{index}] ({name})", entry.get("connection_id")))
    elif isinstance(binding, dict):
        default = binding.get("default")
        if isinstance(default, dict):
            blocks.append(("semantic_model_binding.default", default.get("connection_id")))
        for _, model in enumerate_mappings(binding.get("models")):
            name = model.get("semantic_model_name", "<unnamed>")
            blocks.append((f"semantic_model_binding.models ({name})", model.get("connection_id")))

    found = []
    for source, block in blocks:
        # A string connection_id applies to every environment
        raw = _environment_value(block, environment) if isinstance(block, dict) else block
        if not raw:
            continue
        for item in raw if isinstance(raw, list) else [raw]:
            found.append((str(item), f"{parameter_file.name}: {source}"))
    return found


def _variable_library_ids(
    repository_directory: Path, environment: str, pattern: re.Pattern[str]
) -> list[tuple[str, str]]:
    found = []
    for library in sorted(repository_directory.rglob("*.VariableLibrary")):
        variables_file = library / "variables.json"
        if not variables_file.is_file():
            continue
        variables = _read_json(variables_file)
        effective: dict[str, tuple[Any, str]] = {}
        for variable in variables.get("variables") or []:
            name = str(variable.get("name", ""))
            if pattern.search(name):
                effective[name] = (variable.get("value"), "variables.json")
        for value_set_file in sorted(library.glob("valueSets/*.json")):
            value_set = _read_json(value_set_file)
            if str(value_set.get("name", "")) != environment:
                continue
            for override in value_set.get("variableOverrides") or []:
                name = str(override.get("name", ""))
                if pattern.search(name):
                    effective[name] = (
                        override.get("value"),
                        f"valueSets/{value_set_file.name}",
                    )
        for name, (value, source) in effective.items():
            if value is None or str(value) == "":
                continue
            found.append((str(value), f"{library.name}/{source}: {name}"))
    return found


def get_json(url: str, token: str) -> dict[str, Any]:
    """One authenticated GET against the Fabric API."""
    http_request = request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    try:
        with request.urlopen(http_request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8", errors="replace")
        raise FabkitError(
            f"GET {url} failed with HTTP {http_error.code}.", [detail]
        ) from http_error
    except OSError as network_error:
        raise FabkitError(f"GET {url} failed: {network_error}.") from network_error
    try:
        body = raw.decode("utf-8")
        return json.loads(body) if body.strip() else {}
    except ValueError as problem:
        raise FabkitError(f"Unexpected response from {url}.", [str(problem)]) from problem


def post_json(url: str, token: str, body: dict[str, Any]) -> None:
    """One authenticated POST with a JSON body. The response body is ignored."""
    http_request = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(http_request, timeout=HTTP_TIMEOUT_SECONDS):
            pass
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8", errors="replace")
        raise FabkitError(
            f"POST {url} failed with HTTP {http_error.code}.", [detail]
        ) from http_error
    except OSError as network_error:
        raise FabkitError(f"POST {url} failed: {network_error}.") from network_error


def list_connections(credential: TokenCredential) -> dict[str, str]:
    """The connections the deploy service principal can see, as ID -> display name."""
    from azure.core.exceptions import ClientAuthenticationError

    try:
        token = credential.get_token(FABRIC_SCOPE).token
    except ClientAuthenticationError as problem:
        raise FabkitError(
            "Azure login failed; the deploy could not get a Fabric token.", [str(problem)]
        ) from problem
    connections: dict[str, str] = {}
    url: str | None = f"{FABRIC_API_ROOT}/connections"
    while url:
        body = get_json(url, token)
        for connection in body.get("value", []):
            connections[connection["id"]] = connection.get("displayName", "")
        url = body.get("continuationUri") or None
    return connections


def verify_connections(
    credential: TokenCredential, config: DeployConfig, environment: str, connection_variables: str
) -> None:
    """Stop before publishing when a configured connection ID does not resolve."""
    configured = collect_connection_ids(config, environment, connection_variables)
    if not configured:
        print(f"No connection IDs configured for {environment}; skipping connection check.")
        return

    available = list_connections(credential)
    missing = sorted(cid for cid in configured if cid not in available)
    if missing:
        details = []
        for cid in missing:
            details.append(cid)
            details.extend(f"  configured in {source}" for source in configured[cid])
        raise FabkitError(
            "Connection check failed: the deploy service principal cannot see these "
            "connections. The ID is wrong, or the connection is not shared with it.",
            details,
        )
    for cid in sorted(configured):
        print(f"Connection resolved: {cid} ({available[cid]})")
