import argparse
from pathlib import Path

import pytest
from helpers import FakeOneLake

from fabkit.errors import FabkitError
from fabkit.uploads import Upload, check_uploads, parse_upload, plan_files, run_uploads

LAKEHOUSE = "cccccccc-0000-0000-0000-000000000003"
WORKSPACE = "dddddddd-0000-0000-0000-000000000004"


# A well-formed spec parses into local path, lakehouse and target.
def test_parse_upload_splits_three_parts() -> None:
    upload = parse_upload(f"lib/={LAKEHOUSE}:Files/_lib/lib/")

    assert upload == Upload(Path("lib"), LAKEHOUSE, "Files/_lib/lib")


# A spec missing a part is a usage error.
@pytest.mark.parametrize("spec", ["lib", f"lib={LAKEHOUSE}", f"={LAKEHOUSE}:Files/x"])
def test_parse_upload_rejects_malformed(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_upload(spec)


# The lakehouse must be a GUID.
def test_parse_upload_rejects_non_guid() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="GUID"):
        parse_upload("lib/=landing:Files/x")


# The target must be under Files/.
def test_parse_upload_rejects_target_outside_files() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="Files/"):
        parse_upload(f"lib/={LAKEHOUSE}:Tables/x")


# A missing local path fails before anything is published.
def test_check_uploads_names_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FabkitError) as raised:
        check_uploads([Upload(tmp_path / "nope", LAKEHOUSE, "Files/x")])

    assert any("nope" in detail for detail in raised.value.details), raised.value.details


# A directory maps recursively, keeping subfolders and skipping __pycache__.
def test_plan_directory_keeps_subfolders_skips_pycache(tmp_path: Path) -> None:
    (tmp_path / "lib/a").mkdir(parents=True)
    (tmp_path / "lib/a/b.py").write_text("")
    (tmp_path / "lib/__pycache__").mkdir()
    (tmp_path / "lib/__pycache__/b.pyc").write_text("")

    plan = plan_files(Upload(tmp_path / "lib", LAKEHOUSE, "Files/_lib/lib"))

    assert plan == [(tmp_path / "lib/a/b.py", "Files/_lib/lib/a/b.py")]


# A single file lands in the target folder under its own name.
def test_plan_file_lands_in_target_folder(tmp_path: Path) -> None:
    (tmp_path / "sources.yml").write_text("")

    plan = plan_files(Upload(tmp_path / "sources.yml", LAKEHOUSE, "Files/_config"))

    assert plan == [(tmp_path / "sources.yml", "Files/_config/sources.yml")]


# Files are written under <workspace>/<lakehouse>/<target>.
def test_run_uploads_writes_under_workspace_and_lakehouse(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/m.py").write_text("x = 1")
    onelake = FakeOneLake()

    count = run_uploads(onelake, WORKSPACE, [Upload(tmp_path / "lib", LAKEHOUSE, "Files/_lib/lib")])

    assert count == 1
    assert onelake.written == {f"{WORKSPACE}/{LAKEHOUSE}/Files/_lib/lib/m.py": b"x = 1"}


# An empty local file is written to OneLake as an empty file.
def test_run_uploads_writes_empty_file(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/__init__.py").write_text("")
    onelake = FakeOneLake()

    run_uploads(onelake, WORKSPACE, [Upload(tmp_path / "lib", LAKEHOUSE, "Files/_lib/lib")])

    assert onelake.written == {f"{WORKSPACE}/{LAKEHOUSE}/Files/_lib/lib/__init__.py": b""}
