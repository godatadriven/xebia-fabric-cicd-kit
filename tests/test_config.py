import shutil

import pytest
from helpers import CONFIG, PLATFORM, FabricRepo, assert_reported

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


# A config.yml with invalid YAML is a FabkitError, not a traceback.
def test_invalid_yaml_config_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", "core: [\n")

    with pytest.raises(FabkitError, match="invalid YAML"):
        load_deploy_config(fabric_repo.config)


def remove_notebook(repo: FabricRepo) -> None:
    shutil.rmtree(repo.root / "src/nb.Notebook")


# A repository with no items in scope fails while unpublish removes orphans.
def test_empty_repository_with_unpublish_fails(fabric_repo: FabricRepo) -> None:
    remove_notebook(fabric_repo)

    assert_reported(config_errors(fabric_repo), "DEV", "no items", "Notebook")


# A repository with no items passes when unpublish is skipped.
def test_empty_repository_with_unpublish_skipped_passes(fabric_repo: FabricRepo) -> None:
    remove_notebook(fabric_repo)
    fabric_repo.write("config.yml", CONFIG.replace("skip: false", "skip: true"))

    assert config_errors(fabric_repo) == []


# Items of types outside item_types_in_scope do not count.
def test_only_out_of_scope_items_fail(fabric_repo: FabricRepo) -> None:
    remove_notebook(fabric_repo)
    fabric_repo.write("src/sales.Report/.platform", PLATFORM.replace("Notebook", "Report"))

    assert_reported(config_errors(fabric_repo), "no items")


# Only environments that remove orphans are reported.
def test_empty_repository_reports_unpublishing_environments(fabric_repo: FabricRepo) -> None:
    remove_notebook(fabric_repo)
    fabric_repo.write(
        "config.yml",
        CONFIG.replace("skip: false", "skip:\n    DEV: true\n    PROD: false"),
    )

    errors = config_errors(fabric_repo)

    assert_reported(errors, "PROD", "no items")
    assert not any("DEV" in e for e in errors), errors


# Two items with the same type and name fail: fabric-cicd deploys only one of them.
def test_duplicate_item_name_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("src/archive/nb.Notebook/.platform", PLATFORM.replace("1111", "2222"))

    assert_reported(config_errors(fabric_repo), "Notebook 'nb'", "archive/nb.Notebook")


# The same name on items of different types passes.
def test_same_name_different_type_passes(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("[Notebook]", "[Notebook, DataPipeline]"))
    platform = PLATFORM.replace("1111", "2222").replace("Notebook", "DataPipeline")
    fabric_repo.write("src/nb.DataPipeline/.platform", platform)

    assert config_errors(fabric_repo) == []


# A per-environment item_types_in_scope that leaves out an environment fails: it deploys every type.
def test_item_types_mapping_missing_environment_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write(
        "config.yml",
        CONFIG.replace(
            "item_types_in_scope: [Notebook]", "item_types_in_scope:\n    DEV: [Notebook]"
        ),
    )

    assert_reported(config_errors(fabric_repo), "core.item_types_in_scope", "PROD")


# A per-environment unpublish.exclude_regex that leaves out an environment fails.
def test_unpublish_exclude_mapping_missing_environment_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG + "  exclude_regex:\n    PROD: ^keep_\n")

    assert_reported(config_errors(fabric_repo), "unpublish.exclude_regex", "DEV")


# Per-environment features that leave out an environment fail.
def test_features_mapping_missing_environment_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG + "features:\n  DEV: [enable_hard_delete]\n")

    assert_reported(config_errors(fabric_repo), "features", "PROD")


# A per-environment constant that leaves out an environment fails.
def test_constant_mapping_missing_environment_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write(
        "config.yml",
        CONFIG + "constants:\n  DEFAULT_API_ROOT_URL:\n    DEV: https://api.fabric.microsoft.com\n",
    )

    assert_reported(config_errors(fabric_repo), "constants.DEFAULT_API_ROOT_URL", "PROD")


# Mappings that name every environment pass.
def test_complete_environment_mappings_pass(fabric_repo: FabricRepo) -> None:
    fabric_repo.write(
        "config.yml",
        CONFIG
        + "  exclude_regex:\n    DEV: ^keep_\n    PROD: ^keep_\n"
        + "publish:\n  exclude_regex:\n    DEV: ^tmp_\n    PROD: ^tmp_\n",
    )

    assert config_errors(fabric_repo) == []
