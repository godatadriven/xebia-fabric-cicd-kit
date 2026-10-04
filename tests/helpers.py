"""Test helpers: a Fabric repository on disk, fakes for Fabric and OneLake, assertions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG = """\
core:
  workspace:
    DEV: sales-dev
    PROD: sales-prod
  repository_directory: src
  parameter: src/parameter.yml
  item_types_in_scope: [Notebook]
unpublish:
  skip: false
"""

PLATFORM = """\
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
  "metadata": {"type": "Notebook", "displayName": "nb"},
  "config": {"version": "2.0", "logicalId": "11111111-1111-1111-1111-111111111111"}
}
"""


@dataclass
class FabricRepo:
    """A repository on disk with config.yml at its root."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.yml"

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def find_replace(find: str | None, **values: Any) -> str:
    """parameter.yml text with one find_replace entry.

    find_replace("x", DEV="y", PROD="z") -> find_value "x", replace_value {DEV: y, PROD: z}.
    find=None leaves find_value out.
    """
    entry: dict[str, Any] = {} if find is None else {"find_value": find}
    entry["replace_value"] = values
    return yaml.safe_dump({"find_replace": [entry]}, sort_keys=False)


def assert_reported(messages: list[str], *fragments: str) -> None:
    """At least one message contains every fragment."""
    assert any(all(f in m for f in fragments) for m in messages), (
        f"no message containing {fragments} in {messages}"
    )
