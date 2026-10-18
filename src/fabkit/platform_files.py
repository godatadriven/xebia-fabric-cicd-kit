"""logicalId uniqueness check."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fabkit.errors import Findings

# The logicalId Fabric's export API writes when an item has none
PLACEHOLDER_LOGICAL_ID = "00000000-0000-0000-0000-000000000000"


def check_logical_ids(root: Path) -> Findings:
    """Every .platform file has a string config.logicalId, and no two share one."""
    errors: list[str] = []
    files_by_id: dict[str, list[Path]] = defaultdict(list)

    for path in sorted(p for p in root.rglob(".platform") if ".git" not in p.parts):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            errors.append(f"{path}: invalid JSON: {error}")
            continue
        config = content.get("config") if isinstance(content, dict) else None
        logical_id = config.get("logicalId") if isinstance(config, dict) else None
        if not isinstance(logical_id, str):
            errors.append(f"{path}: config.logicalId is missing or not a string.")
            continue
        files_by_id[logical_id].append(path)

    for logical_id, paths in sorted(files_by_id.items()):
        if len(paths) > 1:
            listed = ", ".join(str(p) for p in paths)
            message = f"logicalId {logical_id} is used by {listed}."
            if logical_id == PLACEHOLDER_LOGICAL_ID:
                message += (
                    " This is the placeholder Fabric's export writes. Give each item a unique "
                    "logicalId; other items cannot reference it otherwise."
                )
            errors.append(message)
    return Findings(errors=errors)


def _item_metadata(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every .platform file under root with its metadata block. Unreadable files are skipped."""
    for path in sorted(root.rglob(".platform")):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        metadata = content.get("metadata") if isinstance(content, dict) else None
        if isinstance(metadata, dict):
            yield path, metadata


def item_names(root: Path, item_type: str) -> set[str]:
    """Display names of the items of one type under root."""
    return {
        metadata["displayName"]
        for _, metadata in _item_metadata(root)
        if metadata.get("type") == item_type and isinstance(metadata.get("displayName"), str)
    }


def item_types_present(root: Path) -> set[str]:
    """Item types with at least one item under root."""
    return {
        metadata["type"]
        for _, metadata in _item_metadata(root)
        if isinstance(metadata.get("type"), str)
    }


def item_folders_by_name(root: Path) -> dict[tuple[str, str], list[Path]]:
    """Item folders under root, keyed by (type, display name)."""
    folders: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path, metadata in _item_metadata(root):
        item_type, name = metadata.get("type"), metadata.get("displayName")
        if isinstance(item_type, str) and isinstance(name, str):
            folders[(item_type, name)].append(path.parent)
    return folders
