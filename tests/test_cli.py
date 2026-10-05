import argparse
from importlib.metadata import version
from pathlib import Path

import pytest

from fabkit.cli import main, run
from fabkit.errors import FabkitError


# --version prints the installed package version.
def test_version_prints_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"fabkit {version('xebia-fabric-cicd-kit')}"


# A FabkitError is printed without a traceback and exits 1.
def test_fabkit_error_prints_message_and_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    def fail(_: object) -> int:
        raise FabkitError("the check failed", ["first detail"])

    parser = argparse.ArgumentParser()
    parser.add_subparsers(dest="command", required=True).add_parser("boom").set_defaults(func=fail)

    code = run(parser, ["boom"])

    err = capsys.readouterr().err
    assert code == 1
    assert "the check failed" in err
    assert "- first detail" in err
    assert "Traceback" not in err


# check-logical-ids exits 1 on a duplicate, from the current directory.
def test_check_logical_ids_command_fails_on_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for item in ("a.Notebook", "b.Notebook"):
        (tmp_path / item).mkdir()
        (tmp_path / item / ".platform").write_text('{"config": {"logicalId": "1"}}')
    monkeypatch.chdir(tmp_path)
    assert main(["check-logical-ids"]) == 1


# An invalid --connection-variables regex is a usage error.
def test_invalid_connection_variables_regex_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["deploy", "--environment", "DEV", "--connection-variables", "("])

    assert exit_info.value.code == 2
    assert "--connection-variables" in capsys.readouterr().err
