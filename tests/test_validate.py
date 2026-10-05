import pytest
import yaml
from helpers import CONFIG, FabricRepo, find_replace

from fabkit.cli import main


# A valid repository passes.
def test_valid_repository_passes(fabric_repo: FabricRepo) -> None:
    assert main(["validate"]) == 0


# A config problem fails validate.
def test_config_error_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("config.yml", CONFIG.replace("unpublish:\n  skip: false\n", ""))
    assert main(["validate"]) == 1


# A parameter problem fails validate.
def test_parameter_error_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("src/parameter.yml", find_replace("x", DEV="TODO", PROD="y"))
    fabric_repo.write("src/nb.Notebook/notebook-content.py", "x")

    assert main(["validate"]) == 1


# A duplicate logicalId fails validate.
def test_duplicate_logical_id_fails(fabric_repo: FabricRepo) -> None:
    other = fabric_repo.root / "src/nb.Notebook/.platform"
    fabric_repo.write("src/other.Notebook/.platform", other.read_text())
    assert main(["validate"]) == 1


# --config points at a config file elsewhere.
def test_config_flag(fabric_repo: FabricRepo) -> None:
    fabric_repo.write("deploy/config.yml", CONFIG.replace("src", "../src"))
    assert main(["validate", "--config", "deploy/config.yml"]) == 0


# A missing config file fails with exit code 1, not a traceback.
def test_missing_config_fails(fabric_repo: FabricRepo) -> None:
    assert main(["validate", "--config", "nope.yml"]) == 1


# A malformed parameter shape fails validate without a traceback.
@pytest.mark.parametrize(
    "parameter_text",
    [
        "find_replace: abc\n",
        "find_replace: [abc]\n",
        "key_value_replace: abc\n",
        "spark_pool: [1]\n",
        "semantic_model_binding:\n  models: [abc]\n",
    ],
)
def test_malformed_parameter_shape_fails(fabric_repo: FabricRepo, parameter_text: str) -> None:
    fabric_repo.write("src/parameter.yml", parameter_text)

    assert main(["validate"]) == 1


# A config section that is not a mapping fails validate without a traceback.
@pytest.mark.parametrize("section", ["core", "unpublish"])
def test_non_mapping_config_section_fails(
    fabric_repo: FabricRepo, section: str, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = yaml.safe_load(CONFIG)
    raw[section] = "abc"
    fabric_repo.write("config.yml", yaml.safe_dump(raw))

    assert main(["validate"]) == 1
    assert f"{section} must be a mapping" in capsys.readouterr().err


# A config file that is not UTF-8 fails validate without a traceback.
def test_non_utf8_config_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.config.write_bytes(CONFIG.encode() + b"# \x93quoted\x94\n")

    assert main(["validate"]) == 1
