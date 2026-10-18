import argparse
from pathlib import Path

import pytest
from helpers import FakeOneLake, assert_reported

from fabkit.errors import FabkitError
from fabkit.uploads import (
    UPLOAD_MARKER,
    Upload,
    check_uploads,
    parse_upload,
    plan_files,
    replace_folder,
    upload_warnings,
)

LAKEHOUSE = "lh_landing"
WORKSPACE = "dddddddd-0000-0000-0000-000000000004"


def tree(root: Path, *paths: str) -> Path:
    """Empty files at the given paths under root."""
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    return root


def planned(upload: Upload) -> list[str]:
    return [target for _, target in plan_files(upload)]


# A well-formed spec parses into local path, lakehouse name and target.
def test_parse_upload_splits_three_parts() -> None:
    upload = parse_upload(f"lib/={LAKEHOUSE}:Files/_lib/lib/")

    assert upload == Upload(Path("lib"), LAKEHOUSE, "Files/_lib/lib")


# A spec missing a part is a usage error.
@pytest.mark.parametrize("spec", ["lib", f"lib={LAKEHOUSE}", f"={LAKEHOUSE}:Files/x"])
def test_parse_upload_rejects_malformed(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_upload(spec)


# The target must be a folder under Files/, not Files/ itself.
@pytest.mark.parametrize("target", ["Tables/x", "Files", "Files/"])
def test_parse_upload_rejects_target_outside_files(target: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="Files/"):
        parse_upload(f"lib/={LAKEHOUSE}:{target}")


# A target with a .. segment is a usage error.
def test_parse_upload_rejects_parent_segment() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=r"\.\."):
        parse_upload(f"lib/={LAKEHOUSE}:Files/../Tables/x")


# A missing local path fails before anything is published.
def test_check_uploads_names_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FabkitError) as raised:
        check_uploads([Upload(tmp_path / "nope", LAKEHOUSE, "Files/x")])

    assert any("nope" in detail for detail in raised.value.details), raised.value.details


# A folder maps recursively, keeping subfolders and skipping __pycache__ and .git.
def test_plan_folder_skips_pycache_and_git(tmp_path: Path) -> None:
    lib = tree(tmp_path / "lib", "a/b.py", "__pycache__/b.pyc", "a/__pycache__/c.pyc", ".git/HEAD")

    assert planned(Upload(lib, LAKEHOUSE, "Files/lib")) == ["a/b.py"]


# A single file lands in the target folder under its own name.
def test_plan_file_lands_in_target_folder(tmp_path: Path) -> None:
    tree(tmp_path, "sources.yml")

    assert planned(Upload(tmp_path / "sources.yml", LAKEHOUSE, "Files/_config")) == ["sources.yml"]


# An exclude pattern that matches a folder skips everything in it.
def test_exclude_folder_pattern(tmp_path: Path) -> None:
    lib = tree(tmp_path / "lib", "m.py", "docs/a.md", "docs/img/b.png")

    assert planned(Upload(lib, LAKEHOUSE, "Files/lib", excludes=("docs",))) == ["m.py"]


# An exclude pattern is a glob over the path relative to the source.
def test_exclude_glob_pattern(tmp_path: Path) -> None:
    lib = tree(tmp_path / "lib", "m.py", "notes.md", "a/readme.md")

    assert planned(Upload(lib, LAKEHOUSE, "Files/lib", excludes=("*.md",))) == ["m.py"]


# A dbt project skips its generated and local files without being told.
def test_dbt_project_skips_defaults(tmp_path: Path) -> None:
    dbt = tree(
        tmp_path / "dbt",
        "dbt_project.yml",
        "models/a.sql",
        "dbt_packages/dbt_utils/macros/x.sql",
        "target/manifest.json",
        "logs/dbt.log",
        "profiles.yml",
        ".user.yml",
    )

    assert planned(Upload(dbt, LAKEHOUSE, "Files/dbt")) == [
        "dbt_packages/dbt_utils/macros/x.sql",
        "dbt_project.yml",
        "models/a.sql",
    ]


# Folders that are dbt defaults are kept outside a dbt project.
def test_non_dbt_folder_keeps_target_and_logs(tmp_path: Path) -> None:
    lib = tree(tmp_path / "lib", "target/a.py", "logs/b.py")

    assert planned(Upload(lib, LAKEHOUSE, "Files/lib")) == ["logs/b.py", "target/a.py"]


# A dbt project with packages.yml but no dbt_packages/ is warned about.
def test_dbt_project_without_installed_packages_warns(tmp_path: Path) -> None:
    dbt = tree(tmp_path / "dbt", "dbt_project.yml", "packages.yml")

    assert_reported(upload_warnings(Upload(dbt, LAKEHOUSE, "Files/dbt")), "dbt deps")


# A dbt project with installed packages is not warned about.
def test_dbt_project_with_installed_packages_passes(tmp_path: Path) -> None:
    dbt = tree(tmp_path / "dbt", "dbt_project.yml", "packages.yml", "dbt_packages/x/a.sql")

    assert upload_warnings(Upload(dbt, LAKEHOUSE, "Files/dbt")) == []


LAKEHOUSE_ID = "cccccccc-0000-0000-0000-000000000003"
LIVE = f"{WORKSPACE}/{LAKEHOUSE_ID}/Files/lib"

# A target folder that an earlier fabkit upload created
MARKED = {f"{LIVE}/{UPLOAD_MARKER}": b""}


def replace(onelake: FakeOneLake, source: Path, replace_existing: bool = False) -> int:
    upload = Upload(source, LAKEHOUSE, "Files/lib")
    return replace_folder(onelake, WORKSPACE, LAKEHOUSE_ID, upload, replace_existing)


def files_under(onelake: FakeOneLake, folder: str) -> dict[str, bytes]:
    """The files in a folder, without the upload marker."""
    prefix = f"{folder}/"
    return {
        k[len(prefix) :]: v
        for k, v in onelake.written.items()
        if k.startswith(prefix) and k != f"{prefix}{UPLOAD_MARKER}"
    }


# A new upload leaves exactly the new files in the target folder, and no staging or old copy.
def test_replace_leaves_only_new_files(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {**MARKED, f"{LIVE}/a.py": b"old", f"{LIVE}/b.py": b"old"}
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/a.py").write_text("new")
    (tmp_path / "lib/c.py").write_text("new")

    count = replace(onelake, tmp_path / "lib")

    assert count == 2
    assert files_under(onelake, LIVE) == {"a.py": b"new", "c.py": b"new"}
    assert not onelake.exists(f"{LIVE}.staging") and not onelake.exists(f"{LIVE}.old")


# The first upload creates the target folder.
def test_replace_creates_missing_target(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    tree(tmp_path / "lib", "m.py")

    replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"m.py": b""}


# A failed upload leaves the live folder untouched and removes the staging folder.
def test_failed_upload_keeps_live_folder(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {**MARKED, f"{LIVE}/a.py": b"old"}
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/c.py").write_text("new")
    onelake.fail_upload_on = "c.py"

    with pytest.raises(RuntimeError):
        replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"a.py": b"old"}
    assert not onelake.exists(f"{LIVE}.staging")


# A failed swap puts the old folder back.
def test_failed_swap_restores_old_folder(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {**MARKED, f"{LIVE}/a.py": b"old"}
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/a.py").write_text("new")
    onelake.fail_rename_to = f"{LAKEHOUSE_ID}/Files/lib"

    with pytest.raises(RuntimeError, match="rename to"):
        replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"a.py": b"old"}
    assert not onelake.exists(f"{LIVE}.old")


# Leftovers of an earlier failed run are removed before the upload.
def test_replace_removes_leftovers(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {f"{LIVE}.staging/x.py": b"left", f"{LIVE}.old/y.py": b"left"}
    tree(tmp_path / "lib", "m.py")

    replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"m.py": b""}
    assert not onelake.exists(f"{LIVE}.staging") and not onelake.exists(f"{LIVE}.old")


# An empty source folder leaves an empty target folder.
def test_replace_with_empty_folder(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {**MARKED, f"{LIVE}/a.py": b"old"}
    (tmp_path / "lib").mkdir()

    replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {}
    assert onelake.exists(LIVE)


# An upload leaves the marker in the folder, so the next upload replaces it without the flag.
def test_upload_marks_folder(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    tree(tmp_path / "lib", "m.py")

    replace(onelake, tmp_path / "lib")
    replace(onelake, tmp_path / "lib")

    assert f"{LIVE}/{UPLOAD_MARKER}" in onelake.written


# A non-empty folder without the marker is not replaced, and nothing is uploaded.
def test_unmarked_folder_is_not_replaced(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {f"{LIVE}/data.csv": b"keep"}
    tree(tmp_path / "lib", "m.py")

    with pytest.raises(FabkitError, match="did not upload"):
        replace(onelake, tmp_path / "lib")

    assert onelake.written == {f"{LIVE}/data.csv": b"keep"}


# replace_existing replaces a non-empty folder without the marker.
def test_replace_existing_replaces_unmarked_folder(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {f"{LIVE}/data.csv": b"old"}
    tree(tmp_path / "lib", "m.py")

    replace(onelake, tmp_path / "lib", replace_existing=True)

    assert files_under(onelake, LIVE) == {"m.py": b""}


# An empty folder without the marker is replaced.
def test_empty_unmarked_folder_is_replaced(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.folders = {LIVE}
    tree(tmp_path / "lib", "m.py")

    replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"m.py": b""}
