import pytest
from helpers import CONFIG, PLATFORM, FabricRepo, assert_reported

from fabkit.config import check_config, load_deploy_config
from fabkit.errors import FabkitError

WORKSPACES = "  workspace:\n    DEV: sales-dev\n    PROD: sales-prod\n"
EMPTY_SRC = CONFIG.replace("repository_directory: src", "repository_directory: empty")
PIPELINE = PLATFORM.replace("1111", "2222").replace("Notebook", "DataPipeline")


def config_errors(repo: FabricRepo, config: str, files: dict[str, str] | None = None) -> list[str]:
    repo.write("config.yml", config)
    for path, text in (files or {}).items():
        repo.write(path, text)
    return check_config(load_deploy_config(repo.config)).errors


# Each problem in config.yml fails, with a message naming it.
@pytest.mark.parametrize(
    ("config", "files", "fragments"),
    [
        pytest.param(
            CONFIG.replace(WORKSPACES, "  workspace: sales\n"),
            {},
            ("map each environment",),
            id="one-workspace",
        ),
        pytest.param(
            CONFIG.replace("unpublish:\n  skip: false\n", ""), {}, ("unpublish.skip",), id="no-skip"
        ),
        pytest.param(
            CONFIG.replace("  skip: false\n", "  skip:\n    DEV: false\n"),
            {},
            ("unpublish.skip", "PROD"),
            id="skip-per-environment",
        ),
        pytest.param(
            CONFIG.replace("  parameter: src/parameter.yml\n", ""),
            {},
            ("parameter.yml", "ignores"),
            id="unreferenced-parameter-file",
        ),
        pytest.param(
            CONFIG.replace("repository_directory: src", "repository_directory: nope"),
            {},
            ("fabric-cicd rejected",),
            id="fabric-cicd",
        ),
        pytest.param(
            CONFIG.replace("[Notebook]", "{DEV: [Notebook]}"),
            {},
            ("core.item_types_in_scope", "PROD"),
            id="item-types",
        ),
        pytest.param(
            CONFIG + "  exclude_regex:\n    PROD: ^keep_\n",
            {},
            ("unpublish.exclude_regex", "DEV"),
            id="exclude-regex",
        ),
        pytest.param(
            CONFIG + "features:\n  DEV: [enable_hard_delete]\n",
            {},
            ("features", "PROD"),
            id="features",
        ),
        pytest.param(
            CONFIG
            + "constants:\n  DEFAULT_API_ROOT_URL:\n    DEV: https://api.fabric.microsoft.com\n",
            {},
            ("constants.DEFAULT_API_ROOT_URL", "PROD"),
            id="constants",
        ),
        pytest.param(
            CONFIG,
            {"src/archive/nb.Notebook/.platform": PLATFORM.replace("1111", "2222")},
            ("Notebook 'nb'", "archive/nb.Notebook"),
            id="duplicate-item",
        ),
        pytest.param(
            EMPTY_SRC, {"empty/.keep": ""}, ("DEV", "no items", "Notebook"), id="empty-repository"
        ),
        pytest.param(
            EMPTY_SRC,
            {"empty/r.Report/.platform": PLATFORM.replace("Notebook", "Report")},
            ("no items",),
            id="items-out-of-scope",
        ),
    ],
)
def test_config_problem_fails(
    fabric_repo: FabricRepo, config: str, files: dict[str, str], fragments: tuple[str, ...]
) -> None:
    assert_reported(config_errors(fabric_repo, config, files), *fragments)


# Valid configs pass.
@pytest.mark.parametrize(
    ("config", "files"),
    [
        pytest.param(CONFIG, {}, id="valid"),
        pytest.param(
            CONFIG.replace(
                WORKSPACES,
                "  workspace_id:\n"
                "    DEV: 11111111-1111-1111-1111-111111111111\n"
                "    PROD: 22222222-2222-2222-2222-222222222222\n",
            ),
            {},
            id="workspace-ids",
        ),
        pytest.param(
            EMPTY_SRC.replace("skip: false", "skip: true"), {"empty/.keep": ""}, id="empty-skipped"
        ),
        pytest.param(
            CONFIG.replace("[Notebook]", "[Notebook, DataPipeline]"),
            {"src/nb.DataPipeline/.platform": PIPELINE},
            id="same-name-other-type",
        ),
        pytest.param(
            CONFIG
            + "  exclude_regex:\n    DEV: ^keep_\n    PROD: ^keep_\n"
            + "publish:\n  exclude_regex:\n    DEV: ^tmp_\n    PROD: ^tmp_\n",
            {},
            id="complete-mappings",
        ),
    ],
)
def test_valid_config_passes(fabric_repo: FabricRepo, config: str, files: dict[str, str]) -> None:
    assert config_errors(fabric_repo, config, files) == []


# An empty repository is reported only for the environments that remove orphans.
def test_empty_repository_reports_unpublishing_environments(fabric_repo: FabricRepo) -> None:
    config = EMPTY_SRC.replace("skip: false", "skip:\n    DEV: true\n    PROD: false")

    errors = config_errors(fabric_repo, config, {"empty/.keep": ""})

    assert_reported(errors, "PROD", "no items")
    assert not any("DEV" in e for e in errors), errors


# Environments are the workspace keys; paths resolve relative to the config file.
def test_environments_and_paths(fabric_repo: FabricRepo) -> None:
    config = load_deploy_config(fabric_repo.config)

    assert config.environments == ["DEV", "PROD"]
    assert config.repository_directory("DEV") == (fabric_repo.root / "src").resolve()
    assert config.parameter_file("DEV") == (fabric_repo.root / "src/parameter.yml").resolve()


# An unreadable config.yml is a FabkitError, not a traceback.
@pytest.mark.parametrize(
    ("content", "message"),
    [(b"core: [\n", "invalid YAML"), (b"\x93core\x94: x\n", "invalid YAML"), (b"- a\n", "mapping")],
)
def test_unreadable_config_fails(fabric_repo: FabricRepo, content: bytes, message: str) -> None:
    fabric_repo.config.write_bytes(content)

    with pytest.raises(FabkitError, match=message):
        load_deploy_config(fabric_repo.config)
