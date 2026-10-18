import logging
import shutil
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from helpers import CONFIG, FabricRepo, FakeCredential, FakeOneLake, find_replace, platform_json

from fabkit import connections, deploy, takeover
from fabkit.cli import main
from fabkit.config import load_deploy_config
from fabkit.connections import FABRIC_API_ROOT, POWER_BI_API_ROOT
from fabkit.errors import FabkitError

LAKEHOUSE = "cccccccc-0000-0000-0000-000000000003"
CONN = "eeeeeeee-0000-0000-0000-000000000005"


class FakeFabric:
    """Stands in for Fabric and OneLake, and records what fabkit asked of them."""

    def __init__(self) -> None:
        self.deploys: list[dict[str, Any]] = []
        self.connection_lookups = 0
        self.visible_connections = {CONN: "warehouse"}
        self.workspaces = [{"id": "ws-dev-guid", "displayName": "sales-dev"}]
        self.onelake = FakeOneLake()


@pytest.fixture
def fabric(monkeypatch: pytest.MonkeyPatch) -> FakeFabric:
    """Replace every Fabric, OneLake and login call. The rest of fabkit runs for real."""
    fake = FakeFabric()
    monkeypatch.delenv("RUNNER_DEBUG", raising=False)
    monkeypatch.setattr(deploy, "_credential", FakeCredential)
    monkeypatch.setattr(deploy, "_deploy_with_config", lambda **kwargs: fake.deploys.append(kwargs))
    monkeypatch.setattr(deploy, "onelake_client", lambda _credential: fake.onelake)
    monkeypatch.setattr(deploy, "get_json", lambda _url, _token: {"value": fake.workspaces})

    def list_connections(_credential: Any) -> dict[str, str]:
        fake.connection_lookups += 1
        return fake.visible_connections

    monkeypatch.setattr(connections, "list_connections", list_connections)
    return fake


def bind_connection(repo: FabricRepo) -> None:
    binding = {"default": {"connection_id": {"_ALL_": CONN}}}
    repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))


# A clean deploy checks connections, deploys the target environment and exits 0.
def test_deploy_checks_connections_then_deploys(
    fabric_repo: FabricRepo, fabric: FakeFabric
) -> None:
    bind_connection(fabric_repo)

    code = main(["deploy", "--environment", "DEV"])

    assert code == 0
    assert fabric.connection_lookups == 1
    assert [d["environment"] for d in fabric.deploys] == ["DEV"]


# An environment not in config.yml fails before anything is contacted.
def test_unknown_environment_fails(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    assert main(["deploy", "--environment", "TEST"]) == 1
    assert fabric.connection_lookups == 0
    assert fabric.deploys == []


# A connection the service principal cannot see blocks the deploy.
def test_unresolved_connection_blocks_deploy(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    bind_connection(fabric_repo)
    fabric.visible_connections = {}

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert fabric.deploys == []


# A PLACEHOLDER for the target environment fails before login.
def test_pending_value_blocks_deploy(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric_repo.write("src/nb.Notebook/notebook-content.py", "x")
    fabric_repo.write("src/parameter.yml", find_replace("x", DEV="y", PROD="PLACEHOLDER"))

    assert main(["deploy", "--environment", "PROD"]) == 1
    assert fabric.connection_lookups == 0
    assert fabric.deploys == []


# A missing upload source fails before anything is published.
def test_missing_upload_blocks_deploy(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    code = main(["deploy", "--environment", "DEV", "--upload", f"nope/={LAKEHOUSE}:Files/x"])

    assert code == 1
    assert fabric.connection_lookups == 0
    assert fabric.deploys == []


# Uploads run after the deploy, into the workspace resolved by name.
def test_uploads_land_in_resolved_workspace(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric_repo.write("lib/m.py", "x = 1")

    code = main(["deploy", "--environment", "DEV", "--upload", f"lib/={LAKEHOUSE}:Files/_lib/lib/"])

    assert code == 0
    assert list(fabric.onelake.written) == [f"ws-dev-guid/{LAKEHOUSE}/Files/_lib/lib/m.py"]


# --skip-connection-check skips the pre-flight.
def test_skip_connection_check(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    bind_connection(fabric_repo)

    assert main(["deploy", "--environment", "DEV", "--skip-connection-check"]) == 0
    assert fabric.connection_lookups == 0


# A fabric-cicd binding warning during the deploy fails it.
def test_binding_warning_fails_deploy(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def deploy_with_warning(**_: Any) -> None:
        logging.getLogger("fabric_cicd.items").warning(
            "Connection ID 'a' not found for semantic model 'm'"
        )

    monkeypatch.setattr(deploy, "_deploy_with_config", deploy_with_warning)

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert "Connection ID 'a' not found" in capsys.readouterr().err


# An exception from deploy_with_config exits 1 with its message and no traceback.
def test_deploy_exception_fails(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_: Any) -> None:
        raise RuntimeError("workspace not found")

    monkeypatch.setattr(deploy, "_deploy_with_config", fail)

    assert main(["deploy", "--environment", "DEV"]) == 1
    err = capsys.readouterr().err
    assert "workspace not found" in err
    assert "Traceback" not in err


# RUNNER_DEBUG=1 turns on fabric-cicd debug logging.
def test_runner_debug_enables_debug_logging(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    levels: list[str] = []
    monkeypatch.setenv("RUNNER_DEBUG", "1")
    monkeypatch.setattr(deploy, "_change_log_level", levels.append)

    main(["deploy", "--environment", "DEV"])

    assert levels == ["DEBUG"]


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

    monkeypatch.setattr(deploy, "get_json", fail)

    workspace_id = deploy.resolve_workspace_id(
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
    monkeypatch.setattr(deploy, "get_json", lambda url, _token: pages[url])

    workspace_id = deploy.resolve_workspace_id(
        FakeCredential(), load_deploy_config(fabric_repo.config), "DEV"
    )

    assert workspace_id == "ws-dev-guid"


# An unknown workspace name fails, naming the workspace.
def test_resolve_unknown_workspace_fails(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric.workspaces = []

    with pytest.raises(FabkitError, match="sales-dev"):
        deploy.resolve_workspace_id(FakeCredential(), load_deploy_config(fabric_repo.config), "DEV")


# A fabric-cicd warning during the deploy skips the uploads.
def test_fatal_warning_skips_uploads(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabric_repo.write("lib/m.py", "x = 1")

    def deploy_with_warning(**_: Any) -> None:
        logging.getLogger("fabric_cicd.items").warning(
            "Connection ID 'a' not found for semantic model 'm'"
        )

    monkeypatch.setattr(deploy, "_deploy_with_config", deploy_with_warning)

    code = main(["deploy", "--environment", "DEV", "--upload", f"lib/={LAKEHOUSE}:Files/x/"])

    assert code == 1
    assert fabric.onelake.written == {}


# An error from OneLake fails the upload with a message and no traceback.
def test_upload_error_fails_without_traceback(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fabric_repo.write("lib/m.py", "x = 1")

    class Boom:
        def get_file_system_client(self, _workspace_id: str) -> Any:
            raise RuntimeError("403 forbidden")

    monkeypatch.setattr(deploy, "onelake_client", lambda _credential: Boom())

    code = main(["deploy", "--environment", "DEV", "--upload", f"lib/={LAKEHOUSE}:Files/x/"])

    err = capsys.readouterr().err
    assert code == 1
    assert "Upload failed: 403 forbidden" in err
    assert "Traceback" not in err


# Warnings logged before the deploy raised are kept in the output.
def test_deploy_exception_keeps_logged_messages(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_: Any) -> None:
        logging.getLogger("fabric_cicd.items").warning(
            "Connection ID 'a' not found for semantic model 'm'"
        )
        raise RuntimeError("publish stopped")

    monkeypatch.setattr(deploy, "_deploy_with_config", fail)

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert "Connection ID 'a' not found" in capsys.readouterr().err


# A returned status other than COMPLETED fails the deploy.
def test_failed_result_status_fails_deploy(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fabric_cicd import DeploymentStatus

    monkeypatch.setattr(
        deploy,
        "_deploy_with_config",
        lambda **_: SimpleNamespace(status=DeploymentStatus.FAILED, message="boom"),
    )

    assert main(["deploy", "--environment", "DEV"]) == 1


# A duplicate logicalId fails the deploy before login.
def test_duplicate_logical_id_blocks_deploy(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = fabric_repo.root / "src/nb.Notebook/.platform"
    fabric_repo.write("src/other.Notebook/.platform", other.read_text())

    def no_login() -> None:
        raise AssertionError("must not log in")

    monkeypatch.setattr(deploy, "_credential", no_login)

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert fabric.deploys == []


def add_semantic_model(repo: FabricRepo) -> None:
    repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]"))
    repo.write(
        "src/sales.SemanticModel/.platform",
        platform_json("SemanticModel", "sales", "a1111111-1111-1111-1111-111111111111"),
    )


@pytest.fixture
def takeover_calls(fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Takeover POST URLs, recorded in order with the deploy calls as 'deploy'."""
    calls: list[str] = []
    items = {"value": [{"id": "sm-1", "displayName": "sales"}]}
    monkeypatch.setattr(takeover, "get_json", lambda _url, _token: items)
    monkeypatch.setattr(takeover, "post_json", lambda url, _token, _body: calls.append(url))
    monkeypatch.setattr(deploy, "_deploy_with_config", lambda **_: calls.append("deploy"))
    return calls


# Without --take-over-semantic-models no takeover call is made.
def test_takeover_off_by_default(fabric_repo: FabricRepo, takeover_calls: list[str]) -> None:
    add_semantic_model(fabric_repo)

    assert main(["deploy", "--environment", "DEV"]) == 0
    assert takeover_calls == ["deploy"]


# With --take-over-semantic-models the takeover runs before deploy_with_config.
def test_takeover_runs_before_deploy(fabric_repo: FabricRepo, takeover_calls: list[str]) -> None:
    add_semantic_model(fabric_repo)

    code = main(["deploy", "--environment", "DEV", "--take-over-semantic-models"])

    assert code == 0
    assert takeover_calls == [
        f"{POWER_BI_API_ROOT}/groups/ws-dev-guid/datasets/sm-1/Default.TakeOver",
        "deploy",
    ]


# A failed takeover prints a warning and the deploy still succeeds.
def test_failed_takeover_does_not_fail_deploy(
    fabric_repo: FabricRepo,
    takeover_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_semantic_model(fabric_repo)

    def refuse(url: str, _token: str, _body: dict[str, Any]) -> None:
        raise FabkitError(f"POST {url} failed with HTTP 401.", ["no access"])

    monkeypatch.setattr(takeover, "post_json", refuse)

    code = main(["deploy", "--environment", "DEV", "--take-over-semantic-models"])

    assert code == 0
    assert takeover_calls == ["deploy"]
    assert "warning: Semantic model sales: takeover failed" in capsys.readouterr().err


# An environment without a value set fails the deploy before publishing.
def test_missing_value_set_blocks_deploy(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, VariableLibrary]"))
    fabric_repo.write("src/vars.VariableLibrary/variables.json", '{"variables": []}')
    fabric_repo.write("src/vars.VariableLibrary/settings.json", '{"valueSetsOrder": ["DEV"]}')

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert fabric.deploys == []


# A repository with no items fails the deploy before login while unpublish removes orphans.
def test_empty_repository_blocks_deploy(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.rmtree(fabric_repo.root / "src/nb.Notebook")

    def no_login() -> None:
        raise AssertionError("must not log in")

    monkeypatch.setattr(deploy, "_credential", no_login)

    assert main(["deploy", "--environment", "DEV"]) == 1
    assert fabric.deploys == []
