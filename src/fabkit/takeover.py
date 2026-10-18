"""Semantic model takeover before a deploy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fabkit.config import DeployConfig
from fabkit.connections import (
    FABRIC_API_ROOT,
    FABRIC_SCOPE,
    POWER_BI_API_ROOT,
    POWER_BI_SCOPE,
    get_json,
    post_json,
)
from fabkit.errors import FabkitError, Findings


def take_over_semantic_models(
    credential: Any,
    config: DeployConfig,
    environment: str,
    resolve_workspace: Callable[[], str],
) -> Findings:
    """Make the deploy identity owner of the repository's semantic models in the workspace."""
    item_types = config.item_types(environment)
    if item_types is not None and "SemanticModel" not in item_types:
        print("Semantic model takeover skipped: SemanticModel is not in item_types_in_scope.")
        return Findings()
    if config.value("publish", "skip", environment):
        print("Semantic model takeover skipped: publish.skip is set.")
        return Findings()
    for key in ("folder_exclude_regex", "folder_path_to_include"):
        if config.value("publish", key, environment):
            return Findings(
                warnings=[f"Semantic model takeover skipped: publish.{key} is not supported."]
            )

    found, warnings = find_semantic_models(config.repository_directory(environment))
    names = select_published(found, config, environment)
    findings = Findings(warnings=warnings)
    if not names:
        print("No semantic models to take over.")
        return findings

    workspace_id = resolve_workspace()
    deployed = list_deployed_semantic_models(credential, workspace_id)
    token = credential.get_token(POWER_BI_SCOPE).token
    taken = 0
    for name in names:
        item_id = deployed.get(name)
        if item_id is None:
            print(f"Semantic model {name}: not in the workspace yet, skipped.")
            continue
        url = f"{POWER_BI_API_ROOT}/groups/{workspace_id}/datasets/{item_id}/Default.TakeOver"
        try:
            post_json(url, token, {})
        except FabkitError as problem:
            reason = " ".join([problem.summary, *problem.details])
            findings.warnings.append(f"Semantic model {name}: takeover failed: {reason}")
            continue
        taken += 1

    in_workspace = sum(1 for name in names if name in deployed)
    print(f"Took over {taken} of {in_workspace} deployed semantic models.")
    return findings


def find_semantic_models(root: Path) -> tuple[list[str], list[str]]:
    """Display names of the SemanticModel items under root, sorted, and warnings."""
    names: set[str] = set()
    warnings: list[str] = []
    for path in sorted(root.rglob(".platform")):
        if ".git" in path.relative_to(root).parts:
            continue
        metadata = _metadata(path)
        if metadata is None:
            warnings.append(f"{path.parent}: unreadable .platform, takeover skipped.")
        elif metadata.get("type") == "SemanticModel":
            name = metadata.get("displayName")
            if isinstance(name, str) and name:
                names.add(name)
            else:
                warnings.append(f"{path.parent}: no displayName in .platform, takeover skipped.")
    return sorted(names), warnings


def select_published(names: list[str], config: DeployConfig, environment: str) -> list[str]:
    """The names fabric-cicd publishes, by publish.exclude_regex and publish.items_to_include."""
    exclude = config.value("publish", "exclude_regex", environment)
    include = config.value("publish", "items_to_include", environment)
    included = None if include is None else {str(item).lower() for item in include}
    return [
        name
        for name in names
        if not (exclude and re.match(exclude, name))
        and (included is None or f"{name}.SemanticModel".lower() in included)
    ]


def list_deployed_semantic_models(credential: Any, workspace_id: str) -> dict[str, str]:
    """Semantic models in the workspace, display name -> item ID."""
    token = credential.get_token(FABRIC_SCOPE).token
    found: dict[str, str] = {}
    url: str | None = f"{FABRIC_API_ROOT}/workspaces/{workspace_id}/items?type=SemanticModel"
    while url:
        body = get_json(url, token)
        for item in body.get("value", []):
            found[str(item["displayName"])] = str(item["id"])
        url = body.get("continuationUri") or None
    return found


def _metadata(path: Path) -> dict[str, Any] | None:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    metadata = content.get("metadata") if isinstance(content, dict) else None
    return metadata if isinstance(metadata, dict) else None
