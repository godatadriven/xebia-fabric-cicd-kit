"""Login, and workspace and lakehouse lookups."""

from __future__ import annotations

from typing import Any

from fabkit.config import DeployConfig
from fabkit.connections import FABRIC_API_ROOT, FABRIC_SCOPE, get_json
from fabkit.errors import FabkitError


def default_credential() -> Any:
    """DefaultAzureCredential: the azure/login step in CI, or az login locally."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def resolve_workspace_id(credential: Any, config: DeployConfig, environment: str) -> str:
    """The workspace GUID: core.workspace_id, or a lookup of core.workspace by name."""
    workspace_id = config.value("core", "workspace_id", environment)
    if workspace_id:
        return str(workspace_id)
    name = config.value("core", "workspace", environment)
    token = credential.get_token(FABRIC_SCOPE).token
    url: str | None = f"{FABRIC_API_ROOT}/workspaces"
    while url:
        body = get_json(url, token)
        for workspace in body.get("value", []):
            if workspace.get("displayName") == name:
                return str(workspace["id"])
        url = body.get("continuationUri") or None
    raise FabkitError(
        f"Workspace {name!r} not found, or the deploy service principal has no access."
    )


def resolve_lakehouse_id(credential: Any, workspace_id: str, name: str) -> str:
    """The GUID of the lakehouse with this name in the workspace."""
    token = credential.get_token(FABRIC_SCOPE).token
    url: str | None = f"{FABRIC_API_ROOT}/workspaces/{workspace_id}/lakehouses"
    names = []
    while url:
        body = get_json(url, token)
        for lakehouse in body.get("value", []):
            if lakehouse.get("displayName") == name:
                return str(lakehouse["id"])
            names.append(str(lakehouse.get("displayName")))
        url = body.get("continuationUri") or None
    raise FabkitError(
        f"Lakehouse {name!r} not found in workspace {workspace_id}.",
        [f"Lakehouses in the workspace: {', '.join(sorted(names)) or 'none'}"],
    )
