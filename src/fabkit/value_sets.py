"""VariableLibrary value-set check."""

from __future__ import annotations

import json
from pathlib import Path

from fabkit.config import DeployConfig
from fabkit.errors import Findings

# The value-set schema version this check implements
KNOWN_VALUE_SET_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/variableLibrary"
    "/definition/valueSet/1.0.0/schema.json"
)

# The only keys Fabric accepts on a value-set override
ALLOWED_OVERRIDE_KEYS = {"name", "value"}


def check_value_sets(root: Path) -> Findings:
    """Value-set overrides carry only name and value. Fabric rejects the library otherwise."""
    errors: list[str] = []
    files = sorted(
        p for p in root.rglob("*.VariableLibrary/valueSets/*.json") if ".git" not in p.parts
    )
    for path in files:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            errors.append(f"{path}: invalid JSON: {error}")
            continue

        if not isinstance(content, dict):
            errors.append(f"{path}: expected a JSON object.")
            continue

        schema = content.get("$schema")
        if schema is not None and schema != KNOWN_VALUE_SET_SCHEMA:
            errors.append(
                f"{path}: declares schema {schema!r}; this check implements "
                f"{KNOWN_VALUE_SET_SCHEMA!r}. Update the check against the new schema."
            )
            continue

        for override in content.get("variableOverrides") or []:
            if not isinstance(override, dict):
                errors.append(f"{path}: override must be an object.")
                continue
            unexpected = set(override) - ALLOWED_OVERRIDE_KEYS
            if unexpected:
                errors.append(
                    f"{path}: override {override.get('name', '<unnamed>')!r} has key(s) "
                    f"{sorted(unexpected)}; Fabric accepts only {sorted(ALLOWED_OVERRIDE_KEYS)}."
                )
    return Findings(errors=errors)


def check_value_set_environments(config: DeployConfig) -> Findings:
    """Every variable library in scope has a value set for every environment.

    Without one, fabric-cicd activates the default value set and reports success.
    """
    errors: list[str] = []
    for environment in config.environments:
        item_types = config.item_types(environment)
        if item_types is not None and "VariableLibrary" not in item_types:
            continue
        for library in sorted(config.repository_directory(environment).rglob("*.VariableLibrary")):
            settings_file = library / "settings.json"
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                errors.append(f"{library.name}: settings.json is missing.")
                continue
            except ValueError as error:
                errors.append(f"{settings_file}: invalid JSON: {error}")
                continue
            order = settings.get("valueSetsOrder") if isinstance(settings, dict) else None
            if not isinstance(order, list) or environment not in order:
                errors.append(
                    f"{library.name}: no value set for {environment!r}. Add one, empty to keep "
                    "the default values."
                )
    return Findings(errors=list(dict.fromkeys(errors)))
