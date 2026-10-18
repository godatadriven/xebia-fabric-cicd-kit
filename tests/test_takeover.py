from typing import Any

import pytest
from helpers import CONFIG, FabricRepo, FakeCredential, platform_json

from fabkit import takeover
from fabkit.config import load_deploy_config
from fabkit.connections import FABRIC_API_ROOT, POWER_BI_API_ROOT
from fabkit.errors import FabkitError
from fabkit.takeover import take_over_semantic_models

ITEMS_URL = f"{FABRIC_API_ROOT}/workspaces/ws-guid/items?type=SemanticModel"


class FakeApi:
    """Workspace items by page URL, and the takeover URLs posted."""

    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.posted: list[str] = []
        self.failing: set[str] = set()

    def get_json(self, url: str, _token: str) -> dict[str, Any]:
        return self.pages[url]

    def post_json(self, url: str, _token: str, _body: dict[str, Any]) -> None:
        if url in self.failing:
            raise FabkitError(f"POST {url} failed with HTTP 401.", ["no access"])
        self.posted.append(url)


def takeover_url(item_id: str) -> str:
    return f"{POWER_BI_API_ROOT}/groups/ws-guid/datasets/{item_id}/Default.TakeOver"


def add_model(repo: FabricRepo, folder: str, name: str, logical_id: str) -> None:
    content = platform_json("SemanticModel", name, logical_id)
    repo.write(f"src/{folder}.SemanticModel/.platform", content)


@pytest.fixture
def api(fabric_repo: FabricRepo, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Semantic model items in the workspace, with SemanticModel in scope."""
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]"))
    fake = FakeApi({ITEMS_URL: {"value": [{"id": "sm-1", "displayName": "sales"}]}})
    monkeypatch.setattr(takeover, "get_json", fake.get_json)
    monkeypatch.setattr(takeover, "post_json", fake.post_json)
    return fake


def run(repo: FabricRepo) -> Any:
    return take_over_semantic_models(
        FakeCredential(), load_deploy_config(repo.config), "DEV", lambda: "ws-guid"
    )


# A model in the repository and in the workspace is taken over by its workspace item ID.
def test_deployed_model_is_taken_over(fabric_repo: FabricRepo, api: FakeApi) -> None:
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    findings = run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]
    assert findings.warnings == []


# The model name comes from .platform displayName, not the folder name.
def test_name_comes_from_platform(fabric_repo: FabricRepo, api: FakeApi) -> None:
    add_model(fabric_repo, "renamed-folder", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]


# A model not in the workspace yet is skipped without a call.
def test_undeployed_model_is_skipped(
    fabric_repo: FabricRepo, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    add_model(fabric_repo, "new", "new", "a1111111-1111-1111-1111-111111111111")

    findings = run(fabric_repo)

    assert api.posted == []
    assert findings.warnings == []
    assert "Semantic model new: not in the workspace yet, skipped." in capsys.readouterr().out


# A workspace model that is not in the repository is never taken over.
def test_workspace_only_model_is_left_alone(fabric_repo: FabricRepo, api: FakeApi) -> None:
    api.pages[ITEMS_URL]["value"].append({"id": "sm-2", "displayName": "manual"})
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]


# A failed takeover is a warning, and the other models are still taken over.
def test_failed_takeover_warns_and_continues(fabric_repo: FabricRepo, api: FakeApi) -> None:
    api.pages[ITEMS_URL]["value"].append({"id": "sm-2", "displayName": "stock"})
    api.failing.add(takeover_url("sm-1"))
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")
    add_model(fabric_repo, "stock", "stock", "a2222222-2222-2222-2222-222222222222")

    findings = run(fabric_repo)

    assert api.posted == [takeover_url("sm-2")]
    assert len(findings.warnings) == 1
    assert "Semantic model sales: takeover failed" in findings.warnings[0]
    assert "HTTP 401" in findings.warnings[0]
    assert "no access" in findings.warnings[0]


# Deployed models are read across continuation pages.
def test_workspace_items_across_pages(fabric_repo: FabricRepo, api: FakeApi) -> None:
    api.pages[ITEMS_URL] = {"value": [], "continuationUri": "next"}
    api.pages["next"] = {"value": [{"id": "sm-1", "displayName": "sales"}]}
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]


# item_types_in_scope without SemanticModel skips the step without calls.
def test_out_of_scope_skips(fabric_repo: FabricRepo, api: FakeApi) -> None:
    fabric_repo.write("config.yml", CONFIG)
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == []


# Without item_types_in_scope every type is in scope, so takeover runs.
def test_no_item_types_means_all(fabric_repo: FabricRepo, api: FakeApi) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("  item_types_in_scope: [Notebook]\n", ""))
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]


# A repository without semantic models looks up nothing.
def test_no_models_no_calls(fabric_repo: FabricRepo, api: FakeApi) -> None:
    def fail() -> str:
        raise AssertionError("must not look up the workspace")

    take_over_semantic_models(FakeCredential(), load_deploy_config(fabric_repo.config), "DEV", fail)

    assert api.posted == []


# An unreadable .platform is a warning and the folder is skipped.
def test_unreadable_platform_warns(fabric_repo: FabricRepo, api: FakeApi) -> None:
    fabric_repo.write("src/broken.SemanticModel/.platform", "{not json")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    findings = run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]
    assert len(findings.warnings) == 1
    assert "broken.SemanticModel" in findings.warnings[0]


# Items are found by .platform metadata.type, whatever the folder is called.
def test_found_by_platform_type(fabric_repo: FabricRepo, api: FakeApi) -> None:
    content = platform_json("SemanticModel", "sales", "a1111111-1111-1111-1111-111111111111")
    fabric_repo.write("src/models/sales/.platform", content)

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-1")]


def with_publish(repo: FabricRepo, publish: str) -> None:
    config = CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]")
    repo.write("config.yml", f"{config}publish:\n{publish}")


# A model matching publish.exclude_regex is not taken over.
def test_exclude_regex_is_honoured(fabric_repo: FabricRepo, api: FakeApi) -> None:
    with_publish(fabric_repo, "  exclude_regex: ^sal\n")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == []


# With publish.items_to_include, only the listed models are taken over.
def test_items_to_include_is_honoured(fabric_repo: FabricRepo, api: FakeApi) -> None:
    api.pages[ITEMS_URL]["value"].append({"id": "sm-2", "displayName": "stock"})
    with_publish(fabric_repo, "  items_to_include: [stock.SemanticModel]\n")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")
    add_model(fabric_repo, "stock", "stock", "a2222222-2222-2222-2222-222222222222")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-2")]


# publish.items_to_include matches in any casing, as fabric-cicd does.
def test_items_to_include_ignores_case(fabric_repo: FabricRepo, api: FakeApi) -> None:
    api.pages[ITEMS_URL]["value"].append({"id": "sm-2", "displayName": "stock"})
    with_publish(fabric_repo, "  items_to_include: [STOCK.semanticmodel]\n")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")
    add_model(fabric_repo, "stock", "stock", "a2222222-2222-2222-2222-222222222222")

    run(fabric_repo)

    assert api.posted == [takeover_url("sm-2")]


# publish.skip for the environment skips the takeover.
def test_publish_skip_is_honoured(fabric_repo: FabricRepo, api: FakeApi) -> None:
    with_publish(fabric_repo, "  skip:\n    DEV: true\n")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    run(fabric_repo)

    assert api.posted == []


# A folder filter in publish skips the takeover with a warning.
def test_folder_filter_skips_with_warning(fabric_repo: FabricRepo, api: FakeApi) -> None:
    with_publish(fabric_repo, "  folder_exclude_regex: ^/legacy\n")
    add_model(fabric_repo, "sales", "sales", "a1111111-1111-1111-1111-111111111111")

    findings = run(fabric_repo)

    assert api.posted == []
    assert findings.warnings == [
        "Semantic model takeover skipped: publish.folder_exclude_regex is not supported."
    ]
