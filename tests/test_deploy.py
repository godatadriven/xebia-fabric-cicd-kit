import logging

import pytest
import yaml
from helpers import (
    CONFIG,
    CONN,
    LAKEHOUSE_ID,
    LAKEHOUSE_NAME,
    WORKSPACE_ID,
    FabricRepo,
    FakeFabric,
    find_replace,
    platform_json,
)

from fabkit import deploy, uploads, workspaces
from fabkit.cli import main
from fabkit.workspaces import POWER_BI_API_ROOT

DEPLOY = ["deploy", "--environment", "DEV"]
LIVE = f"{WORKSPACE_ID}/{LAKEHOUSE_ID}/Files/_lib/lib"
UPLOAD = ["--upload", f"lib/={LAKEHOUSE_NAME}:Files/_lib/lib/"]


def bind_connection(repo: FabricRepo) -> None:
    binding = {"default": {"connection_id": {"_ALL_": CONN}}}
    repo.write("src/parameter.yml", yaml.safe_dump({"semantic_model_binding": binding}))


def log_binding_warning() -> None:
    logging.getLogger("fabric_cicd.items").warning(
        "Connection ID 'a' not found for semantic model 'm'"
    )


# A clean deploy checks connections, deploys the target environment, then uploads.
def test_deploy_checks_connections_deploys_and_uploads(
    fabric_repo: FabricRepo, fabric: FakeFabric
) -> None:
    bind_connection(fabric_repo)
    fabric_repo.write("lib/m.py", "x = 1")

    assert main([*DEPLOY, *UPLOAD]) == 0
    assert fabric.connection_lookups == 1
    assert [d["environment"] for d in fabric.deploys] == ["DEV"]
    assert sorted(fabric.onelake.written) == [f"{LIVE}/.fabkit-upload", f"{LIVE}/m.py"]


# A problem the files show stops the deploy before login.
@pytest.mark.parametrize(
    ("args", "files", "message"),
    [
        pytest.param(
            ["deploy", "--environment", "TEST"], {}, "not in config.yml", id="unknown-environment"
        ),
        pytest.param(
            ["deploy", "--environment", "PROD"],
            {"src/parameter.yml": find_replace("Fabric", DEV="y", PROD="PLACEHOLDER")},
            "would be deployed to PROD",
            id="placeholder-for-target",
        ),
        pytest.param(
            [*DEPLOY, "--upload", f"nope/={LAKEHOUSE_NAME}:Files/x"],
            {},
            "Upload source not found",
            id="missing-upload-source",
        ),
        pytest.param(
            DEPLOY,
            {
                "src/other.Notebook/.platform": platform_json(
                    "Notebook", "o", "1" * 8 + "-1111-1111-1111-111111111111"
                )
            },
            "is used by",
            id="duplicate-logical-id",
        ),
        pytest.param(
            DEPLOY,
            {
                "config.yml": CONFIG.replace("[Notebook]", "[Notebook, VariableLibrary]"),
                "src/vars.VariableLibrary/variables.json": '{"variables": []}',
                "src/vars.VariableLibrary/settings.json": '{"valueSetsOrder": ["PROD"]}',
            },
            "no value set for 'DEV'",
            id="missing-value-set",
        ),
        pytest.param(
            DEPLOY,
            {
                "config.yml": CONFIG.replace(
                    "repository_directory: src", "repository_directory: empty"
                ),
                "empty/.keep": "",
            },
            "deploys no items",
            id="empty-repository",
        ),
    ],
)
def test_file_problem_stops_before_login(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    files: dict[str, str],
    message: str,
) -> None:
    for path, text in files.items():
        fabric_repo.write(path, text)

    def no_login() -> None:
        raise AssertionError("must not log in")

    monkeypatch.setattr(workspaces, "default_credential", no_login)

    assert main(args) == 1
    assert message in capsys.readouterr().err
    assert fabric.deploys == []


# A connection the service principal cannot see stops the deploy; the flag skips the check.
def test_connection_check(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    bind_connection(fabric_repo)
    fabric.connections = []

    assert main(DEPLOY) == 1
    assert fabric.deploys == []
    assert main([*DEPLOY, "--skip-connection-check"]) == 0
    assert fabric.connection_lookups == 1


# A fabric-cicd warning or exception fails the deploy, keeps the logged text and skips uploads.
@pytest.mark.parametrize("raises", [False, True])
def test_fabric_cicd_problem_fails_deploy(
    fabric_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str], raises: bool
) -> None:
    fabric_repo.write("lib/m.py", "x = 1")

    def deploy_with_problem() -> None:
        log_binding_warning()
        if raises:
            raise RuntimeError("publish stopped")

    fabric.on_deploy = deploy_with_problem

    assert main([*DEPLOY, *UPLOAD]) == 1
    err = capsys.readouterr().err
    assert "Connection ID 'a' not found" in err
    assert ("publish stopped" in err) == raises
    assert "Traceback" not in err
    assert fabric.onelake.written == {}


# An error from OneLake fails the upload with a message and no traceback.
def test_upload_error_fails_without_traceback(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fabric_repo.write("lib/m.py", "x = 1")

    class Refusing:
        def get_file_system_client(self, _workspace_id: str) -> None:
            raise RuntimeError("403 forbidden")

    monkeypatch.setattr(uploads, "onelake_client", lambda _credential: Refusing())

    assert main([*DEPLOY, *UPLOAD]) == 1
    err = capsys.readouterr().err
    assert "Upload failed: 403 forbidden" in err
    assert "Traceback" not in err


# --upload-replace-existing lets an upload replace a folder that fabkit did not create.
def test_upload_replace_existing(fabric_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric.onelake.written = {f"{LIVE}/data.csv": b"keep"}
    fabric_repo.write("lib/m.py", "x = 1")

    assert main([*DEPLOY, *UPLOAD]) == 1
    assert main([*DEPLOY, *UPLOAD, "--upload-replace-existing"]) == 0
    assert f"{LIVE}/m.py" in fabric.onelake.written


# RUNNER_DEBUG=1 turns on fabric-cicd debug logging.
def test_runner_debug_enables_debug_logging(
    fabric_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    levels: list[str] = []
    monkeypatch.setenv("RUNNER_DEBUG", "1")
    monkeypatch.setattr(deploy, "_change_log_level", levels.append)

    main(DEPLOY)

    assert levels == ["DEBUG"]


TAKEOVER = f"{POWER_BI_API_ROOT}/groups/{WORKSPACE_ID}/datasets/sm-1/Default.TakeOver"


# Takeover runs only with the flag, before the deploy; a failed takeover only warns.
@pytest.mark.parametrize(
    ("flag", "refuse", "calls"),
    [
        (False, False, ["deploy"]),
        (True, False, [TAKEOVER, "deploy"]),
        (True, True, ["deploy"]),
    ],
)
def test_takeover_in_deploy(
    fabric_repo: FabricRepo,
    fabric: FakeFabric,
    capsys: pytest.CaptureFixture[str],
    flag: bool,
    refuse: bool,
    calls: list[str],
) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, SemanticModel]"))
    fabric_repo.write(
        "src/sales.SemanticModel/.platform",
        platform_json("SemanticModel", "sales", "a" * 8 + "-1111-1111-1111-111111111111"),
    )
    fabric.semantic_models = [{"id": "sm-1", "displayName": "sales"}]
    fabric.refuse_posts = refuse

    assert main([*DEPLOY, *(["--take-over-semantic-models"] if flag else [])]) == 0
    assert fabric.calls == calls
    assert ("takeover failed" in capsys.readouterr().err) == refuse
