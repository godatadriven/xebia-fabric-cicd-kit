"""logicalId uniqueness check."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fabkit.errors import Findings


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
            errors.append(f"logicalId {logical_id} is used by {listed}.")
    return Findings(errors=errors)
