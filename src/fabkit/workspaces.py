"""Fabric API calls: login, requests, and workspace and lakehouse lookups."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from fabkit.config import DeployConfig
from fabkit.errors import FabkitError

FABRIC_API_ROOT = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWER_BI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
HTTP_TIMEOUT_SECONDS = 30


def default_credential() -> Any:
    """DefaultAzureCredential: the azure/login step in CI, or az login locally."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def get_token(credential: Any, scope: str = FABRIC_SCOPE) -> str:
    """An access token for the scope."""
    from azure.core.exceptions import ClientAuthenticationError

    try:
        return str(credential.get_token(scope).token)
    except ClientAuthenticationError as problem:
        raise FabkitError("Azure login failed; no access token.", [str(problem)]) from problem


def request_json(url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One authenticated request: a GET, or a POST of body. A POST's response is ignored."""
    method = "GET" if body is None else "POST"
    http_request = request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(http_request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8", errors="replace")
        summary = f"{method} {url} failed with HTTP {http_error.code}."
        raise FabkitError(summary, [detail]) from http_error
    except OSError as network_error:
        raise FabkitError(f"{method} {url} failed: {network_error}.") from network_error
    if body is not None:
        return {}
    try:
        text = raw.decode("utf-8")
        return json.loads(text) if text.strip() else {}
    except ValueError as problem:
        raise FabkitError(f"Unexpected response from {url}.", [str(problem)]) from problem


def get_all(credential: Any, url: str) -> list[dict[str, Any]]:
    """Every entry of a Fabric API listing, across its pages."""
    token = get_token(credential)
    entries: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        page = request_json(next_url, token)
        entries.extend(page.get("value", []))
        next_url = page.get("continuationUri") or None
    return entries


def resolve_workspace_id(credential: Any, config: DeployConfig, environment: str) -> str:
    """The workspace GUID: core.workspace_id, or a lookup of core.workspace by name."""
    workspace_id = config.value("core", "workspace_id", environment)
    if workspace_id:
        return str(workspace_id)
    name = config.value("core", "workspace", environment)
    for workspace in get_all(credential, f"{FABRIC_API_ROOT}/workspaces"):
        if workspace.get("displayName") == name:
            return str(workspace["id"])
    raise FabkitError(
        f"Workspace {name!r} not found, or the deploy service principal has no access."
    )


def resolve_lakehouse_id(credential: Any, workspace_id: str, name: str) -> str:
    """The GUID of the lakehouse with this name in the workspace."""
    lakehouses = get_all(credential, f"{FABRIC_API_ROOT}/workspaces/{workspace_id}/lakehouses")
    for lakehouse in lakehouses:
        if lakehouse.get("displayName") == name:
            return str(lakehouse["id"])
    names = sorted(str(lakehouse.get("displayName")) for lakehouse in lakehouses)
    raise FabkitError(
        f"Lakehouse {name!r} not found in workspace {workspace_id}.",
        [f"Lakehouses in the workspace: {', '.join(names) or 'none'}"],
    )
