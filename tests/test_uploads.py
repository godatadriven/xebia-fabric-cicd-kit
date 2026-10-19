import argparse
from pathlib import Path

import pytest
from helpers import (
    LAKEHOUSE_ID,
    LAKEHOUSE_NAME,
    WORKSPACE_ID,
    FabricRepo,
    FakeFabric,
    FakeOneLake,
)

from fabkit import workspaces
from fabkit.cli import main
from fabkit.uploads import UPLOAD_MARKER, Upload, parse_upload, plan_files, replace_folder

LIVE = f"{WORKSPACE_ID}/{LAKEHOUSE_ID}/Files/lib"


def tree(root: Path, *paths: str) -> Path:
    """Empty files at the given paths under root."""
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    return root


def files_under(onelake: FakeOneLake, folder: str) -> dict[str, bytes]:
    """The files in a folder, without the upload marker."""
    prefix = f"{folder}/"
    return {
        key[len(prefix) :]: value
        for key, value in onelake.written.items()
        if key.startswith(prefix) and key != f"{prefix}{UPLOAD_MARKER}"
    }


# A spec parses into local path, lakehouse and target folder.
def test_parse_upload() -> None:
    upload = parse_upload(f"lib/={LAKEHOUSE_NAME}:Files/_lib/lib/")

    assert upload == Upload(Path("lib"), LAKEHOUSE_NAME, "Files/_lib/lib")


# A spec missing a part, or with a target that is not a folder under Files/, is a usage error.
@pytest.mark.parametrize(
    "spec",
    [
        "lib=lh",
        "=lh:Files/x",
        "lib=lh:Tables/x",
        "lib=lh:Files/",
        "lib=lh:Files/../x",
    ],
)
def test_parse_upload_rejects(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_upload(spec)


# The files an upload writes, relative to the target folder.
@pytest.mark.parametrize(
    ("files", "source", "excludes", "expected"),
    [
        pytest.param(
            ["a/b.py", "__pycache__/b.pyc", "a/__pycache__/c.pyc", ".git/HEAD"],
            "",
            (),
            ["a/b.py"],
            id="skips-pycache-and-git",
        ),
        pytest.param(["sources.yml"], "sources.yml", (), ["sources.yml"], id="single-file"),
        pytest.param(
            ["m.py", "docs/img/b.png", "a/readme.md"],
            "",
            ("docs", "*.md"),
            ["m.py"],
            id="folder-exclude",
        ),
        pytest.param(
            [
                "dbt_project.yml",
                "models/a.sql",
                "dbt_packages/u/x.sql",
                "target/manifest.json",
                "logs/dbt.log",
                "profiles.yml",
                ".user.yml",
            ],
            "",
            (),
            ["dbt_packages/u/x.sql", "dbt_project.yml", "models/a.sql"],
            id="dbt-defaults",
        ),
        pytest.param(
            ["target/a.py", "logs/b.py"], "", (), ["logs/b.py", "target/a.py"], id="not-dbt"
        ),
    ],
)
def test_plan_files(
    tmp_path: Path, files: list[str], source: str, excludes: tuple[str, ...], expected: list[str]
) -> None:
    tree(tmp_path / "src", *files)

    upload = Upload(tmp_path / "src" / source, LAKEHOUSE_NAME, "Files/lib", excludes)

    assert [target for _, target in plan_files(upload)] == expected


def replace(onelake: FakeOneLake, source: Path, replace_existing: bool = False) -> int:
    upload = Upload(source, LAKEHOUSE_NAME, "Files/lib")
    return replace_folder(onelake, WORKSPACE_ID, LAKEHOUSE_ID, upload, replace_existing)


# An upload replaces the target folder: only the new files remain, with no staging or old copy.
@pytest.mark.parametrize(
    ("existing", "folders", "replace_existing"),
    [
        pytest.param({}, set(), False, id="new-folder"),
        pytest.param(
            {f"{LIVE}/{UPLOAD_MARKER}": b"", f"{LIVE}/a.py": b"old"}, set(), False, id="marked"
        ),
        pytest.param({f"{LIVE}/data.csv": b"old"}, set(), True, id="replace-existing"),
        pytest.param(
            {f"{LIVE}.staging/x.py": b"left", f"{LIVE}.old/y.py": b"left"},
            set(),
            False,
            id="leftovers",
        ),
    ],
)
def test_replace_folder(
    tmp_path: Path, existing: dict[str, bytes], folders: set[str], replace_existing: bool
) -> None:
    onelake = FakeOneLake()
    onelake.written, onelake.folders = dict(existing), set(folders)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/a.py").write_text("new")
    (tmp_path / "lib/empty.py").write_text("")

    assert replace(onelake, tmp_path / "lib", replace_existing) == 2
    assert files_under(onelake, LIVE) == {"a.py": b"new", "empty.py": b""}
    assert f"{LIVE}/{UPLOAD_MARKER}" in onelake.written
    assert not onelake.exists(f"{LIVE}.staging") and not onelake.exists(f"{LIVE}.old")


# A folder with files fabkit did not upload is not replaced, and nothing is written.
def test_unmarked_folder_is_not_replaced(tmp_path: Path) -> None:
    onelake = FakeOneLake()
    onelake.written = {f"{LIVE}/data.csv": b"keep"}

    with pytest.raises(Exception, match="did not upload"):
        replace(onelake, tree(tmp_path / "lib", "m.py"))

    assert onelake.written == {f"{LIVE}/data.csv": b"keep"}


# A failed write or swap leaves the live folder as it was. The next upload removes leftovers.
@pytest.mark.parametrize("failure", ["write", "swap"])
def test_failed_upload_keeps_live_folder(tmp_path: Path, failure: str) -> None:
    onelake = FakeOneLake()
    onelake.written = {f"{LIVE}/{UPLOAD_MARKER}": b"", f"{LIVE}/a.py": b"old"}
    if failure == "write":
        onelake.fail_upload_on = "a.py"
    else:
        onelake.fail_rename_to = f"{LAKEHOUSE_ID}/Files/lib"
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/a.py").write_text("new")

    with pytest.raises(RuntimeError):
        replace(onelake, tmp_path / "lib")

    assert files_under(onelake, LIVE) == {"a.py": b"old"}
    assert not onelake.exists(f"{LIVE}.old")


DBT_LIVE = f"{WORKSPACE_ID}/{LAKEHOUSE_ID}/Files/dbt"


@pytest.fixture
def dbt_repo(fabric_repo: FabricRepo) -> FabricRepo:
    """A dbt project under dbt/, with a config.yml that only maps workspaces."""
    fabric_repo.write(
        "config.yml", "core:\n  workspace:\n    DEV: sales-dev\n    PROD: sales-prod\n"
    )
    fabric_repo.write("dbt/dbt_project.yml", "name: sales\n")
    fabric_repo.write("dbt/models/orders.sql", "select 1")
    fabric_repo.write("dbt/target/manifest.json", "{}")
    return fabric_repo


def upload(*extra: str, lakehouse: str = LAKEHOUSE_NAME, source: str = "dbt") -> int:
    args = ["upload", "--environment", "DEV", "--lakehouse", lakehouse, "--source", source]
    return main([*args, "--target", "Files/dbt", *extra])


def uploaded(fabric: FakeFabric) -> list[str]:
    return sorted(files_under(fabric.onelake, DBT_LIVE))


# upload needs only the workspace mapping, skips dbt's generated files and says so.
def test_upload_dbt_project(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    assert upload() == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]
    assert "dbt project: skipping target, logs, profiles.yml, .user.yml" in capsys.readouterr().out
    assert fabric.deploys == []


# --exclude skips matching files; packages.yml without dbt_packages/ warns but still uploads.
def test_upload_exclude_and_packages_warning(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    dbt_repo.write("dbt/docs/overview.md", "# docs")
    dbt_repo.write("dbt/packages.yml", "packages: []\n")

    assert upload("--exclude", "docs") == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql", "packages.yml"]
    assert "dbt deps" in capsys.readouterr().err


# A lakehouse given as a GUID is used without a lookup.
def test_upload_lakehouse_guid_skips_lookup(dbt_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric.lakehouses = []

    assert upload(lakehouse=LAKEHOUSE_ID) == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]


# A folder with files fabkit did not upload is kept, unless --replace-existing is passed.
def test_upload_replace_existing(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.onelake.written = {f"{DBT_LIVE}/data.csv": b"keep"}

    assert upload() == 1
    assert "did not upload" in capsys.readouterr().err
    assert upload("--replace-existing") == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]


# A bad environment, source or lakehouse fails without a traceback, and nothing is written.
@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param(["--environment", "TEST"], "'TEST' is not in config.yml", id="environment"),
        pytest.param(["--source", "nope"], "Upload source not found", id="source"),
        pytest.param(["--lakehouse", "lh_x"], "Lakehouse 'lh_x' not found", id="lakehouse"),
    ],
)
def test_upload_problem_fails(
    dbt_repo: FabricRepo,
    fabric: FakeFabric,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    options = {"--environment": "DEV", "--lakehouse": LAKEHOUSE_NAME, "--source": "dbt"}
    options[args[0]] = args[1]
    if args[0] != "--lakehouse":
        monkeypatch.setattr(workspaces, "default_credential", lambda: pytest.fail("logged in"))

    command = [
        "upload",
        *(part for item in options.items() for part in item),
        "--target",
        "Files/dbt",
    ]

    assert main(command) == 1
    err = capsys.readouterr().err
    assert message in err
    assert "Traceback" not in err
    assert fabric.onelake.written == {}
