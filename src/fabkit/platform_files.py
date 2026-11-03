"""Fabric items on disk, and the logicalId uniqueness check."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

from fabkit.errors import Findings, Issue, display_path

# The logicalId Fabric's export API writes when an item has none
PLACEHOLDER_LOGICAL_ID = "00000000-0000-0000-0000-000000000000"


class Item(NamedTuple):
    """One Fabric item in the repository."""

    folder: Path
    type: str
    name: str


def read_json(path: Path) -> tuple[Any, str | None]:
    """The JSON content of a file, or None and the reason it cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, str(error)


def platform_files(root: Path) -> list[Path]:
    """Every .platform file under root, outside .git."""
    return sorted(p for p in root.rglob(".platform") if ".git" not in p.relative_to(root).parts)


def read_items(root: Path) -> list[Item]:
    """The items under root. A .platform without a type and displayName is skipped."""
    items = []
    for path in platform_files(root):
        content, _ = read_json(path)
        metadata = content.get("metadata") if isinstance(content, dict) else None
        if not isinstance(metadata, dict):
            continue
        item_type, name = metadata.get("type"), metadata.get("displayName")
        if isinstance(item_type, str) and isinstance(name, str):
            items.append(Item(path.parent, item_type, name))
    return items


def check_logical_ids(root: Path) -> Findings:
    """Every .platform file has a string config.logicalId, and no two share one.

    A shared logicalId is reported on each file that has it.
    """
    errors = []
    files_by_id: dict[str, list[Path]] = defaultdict(list)
    for path in platform_files(root):
        content, problem = read_json(path)
        config = content.get("config") if isinstance(content, dict) else None
        logical_id = config.get("logicalId") if isinstance(config, dict) else None
        if problem:
            errors.append(Issue(path, f"invalid JSON: {problem}"))
        elif not isinstance(logical_id, str):
            errors.append(Issue(path, "config.logicalId is missing or not a string."))
        else:
            files_by_id[logical_id].append(path)

    for logical_id, paths in sorted(files_by_id.items()):
        if len(paths) < 2:
            continue
        for path in paths:
            others = ", ".join(display_path(other) for other in paths if other != path)
            message = f"logicalId {logical_id} is also used by {others}."
            if logical_id == PLACEHOLDER_LOGICAL_ID:
                message += (
                    " This is the placeholder Fabric's export writes. Give each item a unique "
                    "logicalId; other items cannot reference it otherwise."
                )
            errors.append(Issue(path, message))
    return Findings(errors=errors)
