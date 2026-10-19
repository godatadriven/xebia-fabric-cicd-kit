"""VariableLibrary value-set checks."""

from __future__ import annotations

from pathlib import Path

from fabkit.config import DeployConfig
from fabkit.errors import Findings
from fabkit.platform_files import read_json

# The value-set schema version this check implements
KNOWN_VALUE_SET_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/variableLibrary"
    "/definition/valueSet/1.0.0/schema.json"
)

# The only keys Fabric accepts on a value-set override
ALLOWED_OVERRIDE_KEYS = {"name", "value"}


def check_value_sets(root: Path) -> Findings:
    """Value-set overrides carry only name and value. Fabric rejects the library otherwise."""
    errors = []
    for path in sorted(root.rglob("*.VariableLibrary/valueSets/*.json")):
        if ".git" in path.relative_to(root).parts:
            continue
        content, problem = read_json(path)
        schema = content.get("$schema") if isinstance(content, dict) else None
        if problem:
            errors.append(f"{path}: invalid JSON: {problem}")
        elif not isinstance(content, dict):
            errors.append(f"{path}: expected a JSON object.")
        elif schema is not None and schema != KNOWN_VALUE_SET_SCHEMA:
            errors.append(
                f"{path}: declares schema {schema!r}; this check implements "
                f"{KNOWN_VALUE_SET_SCHEMA!r}. Update the check against the new schema."
            )
        else:
            for override in content.get("variableOverrides") or []:
                if not isinstance(override, dict):
                    errors.append(f"{path}: override must be an object.")
                    continue
                unexpected = sorted(set(override) - ALLOWED_OVERRIDE_KEYS)
                if unexpected:
                    errors.append(
                        f"{path}: override {override.get('name', '<unnamed>')!r} has key(s) "
                        f"{unexpected}; Fabric accepts only {sorted(ALLOWED_OVERRIDE_KEYS)}."
                    )
    return Findings(errors=errors)


def check_value_set_environments(config: DeployConfig) -> Findings:
    """Every variable library in scope has a value set for every environment.

    Without one, fabric-cicd activates the default value set and reports success.
    """
    errors = []
    for environment in config.environments:
        if not config.in_scope(environment, "VariableLibrary"):
            continue
        for library in sorted(config.repository_directory(environment).rglob("*.VariableLibrary")):
            settings_file = library / "settings.json"
            if not settings_file.is_file():
                errors.append(f"{library.name}: settings.json is missing.")
                continue
            settings, problem = read_json(settings_file)
            order = settings.get("valueSetsOrder") if isinstance(settings, dict) else None
            if problem:
                errors.append(f"{settings_file}: invalid JSON: {problem}")
            elif not isinstance(order, list) or environment not in order:
                errors.append(
                    f"{library.name}: no value set for {environment!r}. Add one, empty to keep "
                    "the default values."
                )
    return Findings(errors=list(dict.fromkeys(errors)))
