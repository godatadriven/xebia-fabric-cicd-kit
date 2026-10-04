import pytest
from helpers import CONFIG, FabricRepo, assert_reported

from fabkit.config import check_config, load_deploy_config
from fabkit.errors import FabkitError

WORKSPACE_NAMES = "  workspace:\n    DEV: sales-dev\n    PROD: sales-prod\n"


def config_errors(repo: FabricRepo) -> list[str]:
    return check_config(load_deploy_config(repo.config)).errors


# Environments are the keys of the workspace mapping.
def test_environments_from_workspace_mapping(fabric_repo: FabricRepo) -> None:
    assert load_deploy_config(fabric_repo.config).environments == ["DEV", "PROD"]


# A workspace_id mapping names the environments too.
def test_environments_from_workspace_id_mapping(fabric_repo: FabricRepo) -> None:
    fabric_repo.write(
        "config.yml",
        CONFIG.replace(
            WORKSPACE_NAMES,
            "  workspace_id:\n"
            "    DEV: 11111111-1111-1111-1111-111111111111\n"
            "    PROD: 22222222-2222-2222-2222-222222222222\n",
        ),
    )

    assert load_deploy_config(fabric_repo.config).environments == ["DEV", "PROD"]


# Paths resolve relative to the config file, like fabric-cicd does.
def test_paths_resolve_from_config_folder(fabric_repo: FabricRepo) -> None:
    config = load_deploy_config(fabric_repo.config)

    assert config.repository_directory("DEV") == (fabric_repo.root / "src").resolve()
    assert config.parameter_file("DEV") == (fabric_repo.root / "src/parameter.yml").resolve()


# A valid config passes.
def test_valid_config_passes(fabric_repo: FabricRepo) -> None:
    assert config_errors(fabric_repo) == []


# A config file that is not a mapping fails.
def test_non_mapping_config_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", "- a\n- b\n")

    with pytest.raises(FabkitError, match="mapping"):
        load_deploy_config(fabric_repo.config)


# A workspace given as one string fails: fabkit needs one workspace per environment.
def test_workspace_string_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace(WORKSPACE_NAMES, "  workspace: sales\n"))

    assert_reported(config_errors(fabric_repo), "map each environment")


# A missing unpublish.skip fails: orphan removal must be chosen explicitly.
def test_missing_unpublish_skip_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("unpublish:\n  skip: false\n", ""))

    assert_reported(config_errors(fabric_repo), "unpublish.skip")


# unpublish.skip per environment must name every environment.
def test_unpublish_skip_missing_environment_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("  skip: false\n", "  skip:\n    DEV: false\n"))

    assert_reported(config_errors(fabric_repo), "unpublish.skip", "PROD")


# A parameter.yml that config.yml does not reference fails: fabric-cicd ignores it.
def test_unreferenced_parameter_file_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("  parameter: src/parameter.yml\n", ""))

    assert_reported(config_errors(fabric_repo), "parameter.yml", "ignores")


# fabric-cicd's own validation runs: a missing repository directory fails.
def test_fabric_cicd_validation_runs(fabric_repo: FabricRepo) -> None:
    fabric_repo.write(
        "config.yml", CONFIG.replace("repository_directory: src", "repository_directory: nope")
    )

    assert_reported(config_errors(fabric_repo), "fabric-cicd rejected")
