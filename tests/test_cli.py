import argparse
from importlib.metadata import version

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
