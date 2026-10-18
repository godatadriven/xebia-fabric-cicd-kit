from typing import Any

import pytest
from helpers import CONFIG, FabricRepo, FakeCredential

from fabkit import workspaces
from fabkit.config import load_deploy_config
from fabkit.connections import FABRIC_API_ROOT
from fabkit.errors import FabkitError


# A configured workspace_id is used without an API call.
def test_resolve_workspace_id_from_config(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabric_repo.write(
        "config.yml",
        CONFIG.replace(
            "  workspace:\n    DEV: sales-dev\n    PROD: sales-prod\n",
            "  workspace_id:\n"
            "    DEV: 11111111-1111-1111-1111-111111111111\n"
            "    PROD: 22222222-2222-2222-2222-222222222222\n",
        ),
    )

    def fail(_url: str, _token: str) -> dict[str, Any]:
        raise AssertionError("must not call the API")

    monkeypatch.setattr(workspaces, "get_json", fail)

    workspace_id = workspaces.resolve_workspace_id(
        FakeCredential(), load_deploy_config(fabric_repo.config), "DEV"
    )

    assert workspace_id == "11111111-1111-1111-1111-111111111111"


# A workspace name is looked up across result pages.
def test_resolve_workspace_name_across_pages(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = {
        f"{FABRIC_API_ROOT}/workspaces": {
            "value": [{"id": "other-guid", "displayName": "other"}],
            "continuationUri": "next",
        },
        "next": {"value": [{"id": "ws-dev-guid", "displayName": "sales-dev"}]},
    }
    monkeypatch.setattr(workspaces, "get_json", lambda url, _token: pages[url])

    workspace_id = workspaces.resolve_workspace_id(
        FakeCredential(), load_deploy_config(fabric_repo.config), "DEV"
    )

    assert workspace_id == "ws-dev-guid"


# An unknown workspace name fails, naming the workspace.
def test_resolve_unknown_workspace_fails(
    fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspaces, "get_json", lambda _url, _token: {"value": []})

    with pytest.raises(FabkitError, match="sales-dev"):
        workspaces.resolve_workspace_id(
            FakeCredential(), load_deploy_config(fabric_repo.config), "DEV"
        )


WORKSPACE = "dddddddd-0000-0000-0000-000000000004"
LAKEHOUSES_URL = f"{FABRIC_API_ROOT}/workspaces/{WORKSPACE}/lakehouses"


# A lakehouse name is looked up in the workspace, across result pages.
def test_resolve_lakehouse_name_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        LAKEHOUSES_URL: {
            "value": [{"id": "other-guid", "displayName": "lh_other"}],
            "continuationUri": "next",
        },
        "next": {"value": [{"id": "lh-guid", "displayName": "lh_landing"}]},
    }
    monkeypatch.setattr(workspaces, "get_json", lambda url, _token: pages[url])

    lakehouse_id = workspaces.resolve_lakehouse_id(FakeCredential(), WORKSPACE, "lh_landing")

    assert lakehouse_id == "lh-guid"


# An unknown lakehouse name fails, listing the lakehouses that exist.
def test_resolve_unknown_lakehouse_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"value": [{"id": "a", "displayName": "lh_a"}, {"id": "b", "displayName": "lh_b"}]}
    monkeypatch.setattr(workspaces, "get_json", lambda _url, _token: body)

    with pytest.raises(FabkitError, match="lh_landing") as raised:
        workspaces.resolve_lakehouse_id(FakeCredential(), WORKSPACE, "lh_landing")

    assert raised.value.details == ["Lakehouses in the workspace: lh_a, lh_b"]
