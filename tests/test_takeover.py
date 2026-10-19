import pytest
from helpers import CONFIG, WORKSPACE_ID, FabricRepo, FakeCredential, FakeFabric, platform_json

from fabkit.config import load_deploy_config
from fabkit.errors import Findings
from fabkit.takeover import take_over_semantic_models
from fabkit.workspaces import POWER_BI_API_ROOT

IN_SCOPE = CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]")


def takeover_url(item_id: str) -> str:
    return f"{POWER_BI_API_ROOT}/groups/{WORKSPACE_ID}/datasets/{item_id}/Default.TakeOver"


def take_over(repo: FabricRepo, config: str = IN_SCOPE) -> Findings:
    repo.write("config.yml", config)
    return take_over_semantic_models(
        FakeCredential(), load_deploy_config(repo.config), "DEV", lambda: WORKSPACE_ID
    )


@pytest.fixture
def models(fabric_repo: FabricRepo, fabric: FakeFabric) -> FakeFabric:
    """Models sales and finance in the repository, in odd folders. sales and hr are deployed."""
    fabric_repo.write(
        "src/folder.SemanticModel/.platform", platform_json("SemanticModel", "sales", "a" * 8)
    )
    fabric_repo.write(
        "src/finance.Report/.platform", platform_json("SemanticModel", "finance", "b" * 8)
    )
    fabric.semantic_models = [
        {"id": "sm-1", "displayName": "sales"},
        {"id": "sm-2", "displayName": "hr"},
    ]
    return fabric


# Models in the repository and the workspace are taken over by item ID; others are left alone.
def test_deployed_models_are_taken_over(
    fabric_repo: FabricRepo, models: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    findings = take_over(fabric_repo)

    assert findings.warnings == []
    assert models.calls == [takeover_url("sm-1")]
    out = capsys.readouterr().out
    assert "Semantic model finance: not in the workspace yet, skipped." in out
    assert "Took over 1 of 1 deployed semantic models." in out


# Without item_types_in_scope every type is in scope, so takeover runs.
def test_no_item_types_means_all(fabric_repo: FabricRepo, models: FakeFabric) -> None:
    take_over(
        fabric_repo, IN_SCOPE.replace("  item_types_in_scope: [Notebook, SemanticModel]\n", "")
    )

    assert models.calls == [takeover_url("sm-1")]


# A failed takeover is a warning, not a failure.
def test_failed_takeover_warns(fabric_repo: FabricRepo, models: FakeFabric) -> None:
    models.refuse_posts = True

    findings = take_over(fabric_repo)

    reason = f"POST {takeover_url('sm-1')} failed with HTTP 401. no access"
    assert findings.warnings == [f"Semantic model sales: takeover failed: {reason}"]


# Takeover is skipped without API calls when the deploy publishes no semantic models.
@pytest.mark.parametrize(
    ("config", "warns"),
    [
        pytest.param(CONFIG, False, id="out-of-scope"),
        pytest.param(IN_SCOPE + "publish:\n  skip: true\n", False, id="publish-skip"),
        pytest.param(
            IN_SCOPE + "publish:\n  folder_exclude_regex: ^/x\n", True, id="folder-filter"
        ),
        pytest.param(
            IN_SCOPE + "publish:\n  exclude_regex: ^(sales|finance)$\n", False, id="all-excluded"
        ),
    ],
)
def test_takeover_skipped(
    fabric_repo: FabricRepo, models: FakeFabric, config: str, warns: bool
) -> None:
    findings = take_over(fabric_repo, config)

    assert models.requested == []
    assert bool(findings.warnings) == warns


# Only the models fabric-cicd publishes are taken over: exclude_regex and items_to_include apply.
@pytest.mark.parametrize(
    "publish",
    [
        "  exclude_regex: ^fin\n",
        "  items_to_include: [sales.SemanticModel]\n",
        "  items_to_include: [SALES.semanticmodel]\n",
    ],
)
def test_publish_filters_apply(fabric_repo: FabricRepo, models: FakeFabric, publish: str) -> None:
    models.semantic_models.append({"id": "sm-3", "displayName": "finance"})

    take_over(fabric_repo, IN_SCOPE + "publish:\n" + publish)

    assert models.calls == [takeover_url("sm-1")]
