"""Semantic model takeover before a deploy."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fabkit import workspaces
from fabkit.config import DeployConfig
from fabkit.errors import FabkitError, Findings
from fabkit.platform_files import read_items
from fabkit.workspaces import FABRIC_API_ROOT, POWER_BI_API_ROOT, POWER_BI_SCOPE


def take_over_semantic_models(
    credential: Any,
    config: DeployConfig,
    environment: str,
    resolve_workspace: Callable[[], str],
) -> Findings:
    """Make the deploy identity owner of the repository's semantic models in the workspace."""
    if not config.in_scope(environment, "SemanticModel"):
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

    names = select_published(config, environment)
    if not names:
        print("No semantic models to take over.")
        return Findings()

    workspace_id = resolve_workspace()
    deployed = {
        str(item["displayName"]): str(item["id"])
        for item in workspaces.get_all(
            credential, f"{FABRIC_API_ROOT}/workspaces/{workspace_id}/items?type=SemanticModel"
        )
    }
    token = workspaces.get_token(credential, POWER_BI_SCOPE)
    findings = Findings()
    taken = 0
    for name in names:
        if name not in deployed:
            print(f"Semantic model {name}: not in the workspace yet, skipped.")
            continue
        dataset = f"{POWER_BI_API_ROOT}/groups/{workspace_id}/datasets/{deployed[name]}"
        try:
            workspaces.request_json(f"{dataset}/Default.TakeOver", token, body={})
            taken += 1
        except FabkitError as problem:
            reason = " ".join([problem.summary, *problem.details])
            findings.warnings.append(f"Semantic model {name}: takeover failed: {reason}")

    in_workspace = sum(1 for name in names if name in deployed)
    print(f"Took over {taken} of {in_workspace} deployed semantic models.")
    return findings


def select_published(config: DeployConfig, environment: str) -> list[str]:
    """The semantic models fabric-cicd publishes, by publish.exclude_regex and items_to_include."""
    exclude = config.value("publish", "exclude_regex", environment)
    include = config.value("publish", "items_to_include", environment)
    included = None if include is None else {str(item).lower() for item in include}
    names = {
        item.name
        for item in read_items(config.repository_directory(environment))
        if item.type == "SemanticModel"
    }
    return [
        name
        for name in sorted(names)
        if not (exclude and re.match(exclude, name))
        and (included is None or f"{name}.SemanticModel".lower() in included)
    ]
