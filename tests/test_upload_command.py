import pytest
from helpers import LAKEHOUSE_ID, LAKEHOUSE_NAME, FabricRepo, FakeFabric

from fabkit import workspaces
from fabkit.cli import main
from fabkit.uploads import UPLOAD_MARKER

# A dbt repository's config.yml: the workspace per environment, nothing else
WORKSPACES_ONLY = "core:\n  workspace:\n    DEV: sales-dev\n    PROD: sales-prod\n"

LIVE = f"ws-dev-guid/{LAKEHOUSE_ID}/Files/dbt"


@pytest.fixture
def dbt_repo(fabric_repo: FabricRepo) -> FabricRepo:
    """A dbt project under dbt/, with a config.yml that only maps workspaces."""
    fabric_repo.write("config.yml", WORKSPACES_ONLY)
    fabric_repo.write("dbt/dbt_project.yml", "name: sales\n")
    fabric_repo.write("dbt/models/orders.sql", "select 1")
    fabric_repo.write("dbt/target/manifest.json", "{}")
    return fabric_repo


def upload(*extra: str) -> int:
    args = ["upload", "--environment", "DEV", "--lakehouse", LAKEHOUSE_NAME]
    return main([*args, "--source", "dbt", "--target", "Files/dbt", *extra])


def uploaded(fabric: FakeFabric) -> list[str]:
    """The files in the target folder, without the upload marker."""
    files = (k[len(LIVE) + 1 :] for k in fabric.onelake.written if k.startswith(f"{LIVE}/"))
    return sorted(f for f in files if f != UPLOAD_MARKER)


# upload needs only the workspace mapping in config.yml, and skips dbt's generated files.
def test_upload_with_workspaces_only(dbt_repo: FabricRepo, fabric: FakeFabric) -> None:
    assert upload() == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]
    assert fabric.deploys == []


# A dbt project upload says which files it skips.
def test_upload_names_dbt_defaults(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    upload()

    assert "dbt project: skipping target, logs, profiles.yml, .user.yml" in capsys.readouterr().out


# --exclude skips matching files.
def test_upload_exclude(dbt_repo: FabricRepo, fabric: FakeFabric) -> None:
    dbt_repo.write("dbt/docs/overview.md", "# docs")

    assert upload("--exclude", "docs", "--exclude", "*.yml") == 0
    assert uploaded(fabric) == ["models/orders.sql"]


# An environment that config.yml does not define fails.
def test_upload_unknown_environment_fails(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["upload", "--environment", "TEST", "--lakehouse", LAKEHOUSE_NAME]

    assert main([*args, "--source", "dbt", "--target", "Files/dbt"]) == 1
    assert "'TEST' is not in config.yml" in capsys.readouterr().err


# A missing source fails before login.
def test_upload_missing_source_fails_before_login(
    dbt_repo: FabricRepo, fabric: FakeFabric, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_login() -> None:
        raise AssertionError("must not log in")

    monkeypatch.setattr(workspaces, "default_credential", no_login)
    args = ["upload", "--environment", "DEV", "--lakehouse", LAKEHOUSE_NAME]

    assert main([*args, "--source", "nope", "--target", "Files/dbt"]) == 1


# An unknown lakehouse fails without a traceback, and nothing is written.
def test_upload_unknown_lakehouse_fails(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.lakehouses = []

    assert upload() == 1
    err = capsys.readouterr().err
    assert f"Lakehouse {LAKEHOUSE_NAME!r} not found" in err
    assert "Traceback" not in err
    assert fabric.onelake.written == {}


# A target outside Files/ is a usage error.
def test_upload_target_outside_files_is_usage_error(dbt_repo: FabricRepo) -> None:
    args = ["upload", "--environment", "DEV", "--lakehouse", LAKEHOUSE_NAME]

    with pytest.raises(SystemExit) as raised:
        main([*args, "--source", "dbt", "--target", "Tables/dbt"])

    assert raised.value.code == 2


# A dbt project with packages.yml but no dbt_packages/ is warned about, and still uploaded.
def test_upload_warns_about_missing_packages(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    dbt_repo.write("dbt/packages.yml", "packages: []\n")

    assert upload() == 0
    assert "warning:" in capsys.readouterr().err
    assert "packages.yml" in uploaded(fabric)


# A lakehouse given as a GUID is used without a lookup.
def test_upload_lakehouse_guid_skips_lookup(dbt_repo: FabricRepo, fabric: FakeFabric) -> None:
    fabric.lakehouses = []
    args = ["upload", "--environment", "DEV", "--lakehouse", LAKEHOUSE_ID]

    assert main([*args, "--source", "dbt", "--target", "Files/dbt"]) == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]


# A folder with files fabkit did not upload is kept, unless --replace-existing is passed.
def test_upload_replace_existing(
    dbt_repo: FabricRepo, fabric: FakeFabric, capsys: pytest.CaptureFixture[str]
) -> None:
    fabric.onelake.written = {f"{LIVE}/data.csv": b"keep"}

    assert upload() == 1
    assert "did not upload" in capsys.readouterr().err
    assert upload("--replace-existing") == 0
    assert uploaded(fabric) == ["dbt_project.yml", "models/orders.sql"]
