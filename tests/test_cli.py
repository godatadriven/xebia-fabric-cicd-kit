import argparse
from importlib.metadata import version

import pytest
import yaml
from helpers import CONFIG, PLATFORM, FabricRepo, find_replace

from fabkit.cli import main, run
from fabkit.errors import FabkitError


# validate runs every check and passes a valid repository, also with --config elsewhere.
@pytest.mark.parametrize(
    "args",
    [["validate"], ["validate", "--config", "deploy/config.yml"]],
)
def test_validate_passes(fabric_repo: FabricRepo, args: list[str]) -> None:
    fabric_repo.write("deploy/config.yml", CONFIG.replace("src", "../src"))

    assert main(args) == 0


# validate fails, without a traceback, on a problem from any check.
@pytest.mark.parametrize(
    "files",
    [
        pytest.param(
            {"config.yml": CONFIG.replace("unpublish:\n  skip: false\n", "")}, id="config"
        ),
        pytest.param(
            {"src/parameter.yml": find_replace("nb", DEV="TODO", PROD="y")}, id="parameter"
        ),
        pytest.param({"src/other.Notebook/.platform": PLATFORM}, id="logical-id"),
        pytest.param(
            {"src/vars.VariableLibrary/valueSets/PROD.json": '{"variableOverrides": [{"a": 1}]}'},
            id="value-set",
        ),
        *(
            pytest.param({"src/parameter.yml": text}, id=f"malformed-{index}")
            for index, text in enumerate(
                ["find_replace: [abc]\n", "semantic_model_binding:\n  models: [abc]\n"]
            )
        ),
        pytest.param(
            {"config.yml": yaml.safe_dump({**yaml.safe_load(CONFIG), "unpublish": "x"})},
            id="section-not-mapping",
        ),
    ],
)
def test_validate_fails(
    fabric_repo: FabricRepo, files: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    for path, text in files.items():
        fabric_repo.write(path, text)

    assert main(["validate"]) == 1
    assert "Traceback" not in capsys.readouterr().err


# A missing or non-UTF-8 config file exits 1.
def test_validate_unreadable_config_fails(fabric_repo: FabricRepo) -> None:
    fabric_repo.config.write_bytes(CONFIG.encode() + b"# \x93quoted\x94\n")

    assert main(["validate"]) == 1
    assert main(["validate", "--config", "nope.yml"]) == 1


# The pre-commit hooks check the current directory.
@pytest.mark.parametrize(
    ("command", "path", "content"),
    [
        ("check-logical-ids", "src/other.Notebook/.platform", PLATFORM),
        (
            "check-value-sets",
            "src/vars.VariableLibrary/valueSets/PROD.json",
            '{"variableOverrides": [{"name": "a", "value": "1", "note": "x"}]}',
        ),
    ],
)
def test_hook_fails_on_problem(
    fabric_repo: FabricRepo, command: str, path: str, content: str
) -> None:
    assert main([command]) == 0

    fabric_repo.write(path, content)

    assert main([command]) == 1


# --version prints the installed package version.
def test_version_prints_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"fabkit {version('xebia-fabric-cicd-kit')}"


# A FabkitError is printed with its details, without a traceback, and exits 1.
def test_fabkit_error_prints_message_and_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    def fail(_: object) -> int:
        raise FabkitError("the check failed", ["first detail"])

    parser = argparse.ArgumentParser()
    parser.add_subparsers(dest="command", required=True).add_parser("boom").set_defaults(func=fail)

    assert run(parser, ["boom"]) == 1
    err = capsys.readouterr().err
    assert "the check failed\n- first detail" in err
    assert "Traceback" not in err


# An invalid --connection-variables regex is a usage error.
def test_invalid_connection_variables_regex_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["deploy", "--environment", "DEV", "--connection-variables", "("])

    assert exit_info.value.code == 2
    assert "--connection-variables" in capsys.readouterr().err
