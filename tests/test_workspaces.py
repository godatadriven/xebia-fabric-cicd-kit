from io import BytesIO
from typing import Any
from urllib import error

import pytest
from azure.core.exceptions import ClientAuthenticationError
from helpers import CONFIG, WORKSPACE_ID, FabricRepo, FakeCredential, FakeFabric

from fabkit import workspaces
from fabkit.config import load_deploy_config
from fabkit.errors import FabkitError
from fabkit.workspaces import get_all, request_json


class Response(BytesIO):
    def __enter__(self) -> "Response":
        return self


def answer(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> list[Any]:
    """Make urlopen return the bytes, or raise the error. Returns the requests sent."""
    sent: list[Any] = []

    def urlopen(http_request: Any, **_: Any) -> Response:
        sent.append(http_request)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)

    monkeypatch.setattr(workspaces.request, "urlopen", urlopen)
    return sent


# A GET returns the JSON body; a POST sends the JSON body with the bearer token.
def test_request_json_get_and_post(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = answer(monkeypatch, b'{"value": [1]}')

    assert request_json("https://x", "token") == {"value": [1]}
    assert request_json("https://x", "token", body={"a": 1}) == {}
    assert sent[1].get_method() == "POST"
    assert sent[1].data == b'{"a": 1}'
    assert sent[1].get_header("Authorization") == "Bearer token"


# A failed request becomes a FabkitError naming the request and the reason.
@pytest.mark.parametrize(
    ("outcome", "body", "message"),
    [
        (
            error.HTTPError("https://x", 403, "Forbidden", {}, BytesIO(b"no access")),
            None,
            "GET https://x failed with HTTP 403",
        ),
        (
            error.HTTPError("https://x", 401, "Unauthorized", {}, BytesIO(b"no access")),
            {},
            "POST https://x failed with HTTP 401",
        ),
        (error.URLError("unreachable"), None, "GET https://x failed: .*unreachable"),
        (b"<html>", None, "Unexpected response"),
    ],
)
def test_request_json_failure(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, body: Any, message: str
) -> None:
    answer(monkeypatch, outcome)

    with pytest.raises(FabkitError, match=message):
        request_json("https://x", "token", body=body)


# A listing is read across its pages.
def test_get_all_follows_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "https://x/1": {"value": [1], "continuationUri": "https://x/2"},
        "https://x/2": {"value": [2]},
    }
    monkeypatch.setattr(workspaces, "request_json", lambda url, _token: pages[url])

    assert get_all(FakeCredential(), "https://x/1") == [1, 2]


# A failed Azure login becomes a FabkitError.
def test_login_failure_fails() -> None:
    class FailingCredential:
        def get_token(self, *_: Any) -> Any:
            raise ClientAuthenticationError("no secret")

    with pytest.raises(FabkitError, match="Azure login failed") as raised:
        get_all(FailingCredential(), "https://x")
    assert raised.value.details == ["no secret"]


# A workspace_id is used as is; a workspace name is looked up, and an unknown one fails.
def test_resolve_workspace_id(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    by_name = load_deploy_config(fabric_repo.config)
    fabric_repo.write("config.yml", CONFIG.replace("workspace:", "workspace_id:"))
    by_id = load_deploy_config(fabric_repo.config)

    assert workspaces.resolve_workspace_id(FakeCredential(), by_id, "DEV") == "sales-dev"
    assert fabric.requested == []
    assert workspaces.resolve_workspace_id(FakeCredential(), by_name, "DEV") == WORKSPACE_ID
    with pytest.raises(FabkitError, match="'sales-prod' not found"):
        workspaces.resolve_workspace_id(FakeCredential(), by_name, "PROD")


# A lakehouse is found by name; an unknown one fails, listing the lakehouses that exist.
def test_resolve_lakehouse_id(fabric: FakeFabric) -> None:
    lakehouse_id = workspaces.resolve_lakehouse_id(FakeCredential(), WORKSPACE_ID, "lh_landing")

    assert lakehouse_id == fabric.lakehouses[0]["id"]
    with pytest.raises(FabkitError, match="'lh_x' not found") as raised:
        workspaces.resolve_lakehouse_id(FakeCredential(), WORKSPACE_ID, "lh_x")
    assert raised.value.details == ["Lakehouses in the workspace: lh_landing"]
