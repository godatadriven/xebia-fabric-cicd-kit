"""Connection pre-flight."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fabkit.config import DeployConfig
from fabkit.errors import FabkitError
from fabkit.parameters import (
    binding_entries,
    environment_value,
    load_parameters,
    resolve_env_references,
)
from fabkit.platform_files import read_json
from fabkit.workspaces import FABRIC_API_ROOT, get_all

# Variable-library variables whose name matches this hold connection IDs
DEFAULT_CONNECTION_VARIABLES = "ConnectionID$"


def collect_connection_ids(
    config: DeployConfig, environment: str, connection_variables: str
) -> dict[str, list[str]]:
    """Connection IDs the deploy binds in one environment, mapped to where they are set."""
    found: dict[str, list[str]] = {}
    parameter_file = config.parameter_file(environment)
    if parameter_file is not None and parameter_file.is_file():
        for description, entry in binding_entries(load_parameters(parameter_file)):
            raw = entry.get("connection_id")
            # A string connection_id applies to every environment
            value = environment_value(raw, environment) if isinstance(raw, dict) else raw
            for cid in (value if isinstance(value, list) else [value]) if value else []:
                cid = resolve_env_references(config, environment, str(cid))
                found.setdefault(cid, []).append(f"{parameter_file.name}: {description}")
    if config.in_scope(environment, "VariableLibrary"):
        directory = config.repository_directory(environment)
        for cid, source in _variable_library_ids(directory, environment, connection_variables):
            found.setdefault(cid, []).append(source)
    return found


def _read_object(path: Path) -> dict[str, Any]:
    content, problem = read_json(path)
    if problem:
        raise FabkitError(f"{path.name}: invalid JSON.", [problem])
    return content if isinstance(content, dict) else {}


def _variable_library_ids(
    repository_directory: Path, environment: str, connection_variables: str
) -> list[tuple[str, str]]:
    """Variable values whose name matches, with the environment's value-set overrides applied."""
    pattern = re.compile(connection_variables)
    found = []
    for library in sorted(repository_directory.rglob("*.VariableLibrary")):
        variables_file = library / "variables.json"
        if not variables_file.is_file():
            continue
        effective = {
            str(variable.get("name", "")): (variable.get("value"), "variables.json")
            for variable in _read_object(variables_file).get("variables") or []
        }
        for value_set_file in sorted(library.glob("valueSets/*.json")):
            value_set = _read_object(value_set_file)
            if str(value_set.get("name", "")) == environment:
                for override in value_set.get("variableOverrides") or []:
                    source = f"valueSets/{value_set_file.name}"
                    effective[str(override.get("name", ""))] = (override.get("value"), source)
        found += [
            (str(value), f"{library.name}/{source}: {name}")
            for name, (value, source) in effective.items()
            if pattern.search(name) and value is not None and str(value) != ""
        ]
    return found


def list_connections(credential: Any) -> dict[str, str]:
    """The connections the deploy service principal can see, as ID -> display name."""
    connections = get_all(credential, f"{FABRIC_API_ROOT}/connections")
    return {c["id"]: c.get("displayName", "") for c in connections}


def verify_connections(
    credential: Any, config: DeployConfig, environment: str, connection_variables: str
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
            details += [cid, *(f"  configured in {source}" for source in configured[cid])]
        raise FabkitError(
            "Connection check failed: the deploy service principal cannot see these "
            "connections. The ID is wrong, or the connection is not shared with it.",
            details,
        )
    for cid in sorted(configured):
        print(f"Connection resolved: {cid} ({available[cid]})")
