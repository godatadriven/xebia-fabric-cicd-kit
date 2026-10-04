"""VariableLibrary value-set check."""

from __future__ import annotations

import json
from pathlib import Path

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
