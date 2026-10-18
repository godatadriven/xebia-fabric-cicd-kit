"""Lakehouse uploads: parsing, file selection, the folder swap and the upload command."""

from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from fabkit import workspaces
from fabkit.config import load_deploy_config, require_environment
from fabkit.errors import FabkitError
from fabkit.parameters import GUID_PATTERN

ONELAKE_URL = "https://onelake.dfs.fabric.microsoft.com"

# Folders never uploaded, wherever they are
SKIPPED_FOLDERS = {"__pycache__", ".git"}

# A source with this file is a dbt project
DBT_PROJECT_FILE = "dbt_project.yml"

# Generated and local files of a dbt project, relative to the project folder
DBT_EXCLUDES = ("target", "logs", "profiles.yml", ".user.yml")

# Written into every folder an upload creates
UPLOAD_MARKER = ".fabkit-upload"


@dataclass(frozen=True)
class Upload:
    """One upload: a local file or folder that replaces a folder in a lakehouse's Files/."""

    local: Path
    lakehouse: str
    target: str
    excludes: tuple[str, ...] = ()


def parse_upload(spec: str) -> Upload:
    """Parse '<local path>=<lakehouse name or GUID>:<Files/ path>'.

    "lib/=lh_landing:Files/_lib/lib/" -> Upload(Path("lib"), "lh_landing", "Files/_lib/lib")
    """
    local, sep, rest = spec.partition("=")
    lakehouse, sep2, target = rest.partition(":")
    if not (sep and sep2 and local and lakehouse and target):
        raise argparse.ArgumentTypeError(
            f"--upload {spec!r}: expected '<local path>=<lakehouse>:<Files/ path>'."
        )
    return Upload(Path(local), lakehouse, check_target(target, f"--upload {spec!r}"))


def check_target(target: str, where: str) -> str:
    """The target without surrounding slashes. It must be a folder under Files/."""
    target = target.strip("/")
    if not target.startswith("Files/"):
        raise argparse.ArgumentTypeError(f"{where}: the target must be a folder under Files/.")
    if ".." in target.split("/"):
        raise argparse.ArgumentTypeError(f"{where}: the target must not contain '..'.")
    return target


def check_uploads(uploads: list[Upload]) -> None:
    """Fail when a local path does not exist. Runs before anything is published."""
    missing = [str(upload.local) for upload in uploads if not upload.local.exists()]
    if missing:
        raise FabkitError("Upload source not found. Nothing was uploaded or published.", missing)


def is_dbt_project(upload: Upload) -> bool:
    """Whether the source is a folder with a dbt_project.yml."""
    return upload.local.is_dir() and (upload.local / DBT_PROJECT_FILE).is_file()


def excludes_of(upload: Upload) -> tuple[str, ...]:
    """The upload's exclude patterns, plus the dbt defaults for a dbt project."""
    return upload.excludes + (DBT_EXCLUDES if is_dbt_project(upload) else ())


def _is_excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    """Whether the path, or one of its folders, matches a pattern.

    "docs" excludes "docs/img/a.png"; "*.md" excludes "a/readme.md".
    """
    parts = relative.split("/")
    if SKIPPED_FOLDERS.intersection(parts[:-1]):
        return True
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(fnmatch(c, p) for c in candidates for p in patterns)


def plan_files(upload: Upload) -> list[tuple[Path, str]]:
    """Every local file of one upload, with its path inside the target folder."""
    if upload.local.is_file():
        return [(upload.local, upload.local.name)]
    patterns = excludes_of(upload)
    planned = []
    for path in sorted(upload.local.rglob("*")):
        relative = path.relative_to(upload.local).as_posix()
        if path.is_file() and not _is_excluded(relative, patterns):
            planned.append((path, relative))
    return planned


def upload_warnings(upload: Upload) -> list[str]:
    """Problems that do not stop the upload."""
    if not is_dbt_project(upload):
        return []
    if (upload.local / "packages.yml").is_file() and not (upload.local / "dbt_packages").is_dir():
        return [
            f"{upload.local}: packages.yml without dbt_packages/. Run 'dbt deps' before the "
            "upload, or the runner needs network access to install the packages."
        ]
    return []


def replace_folder(
    client: Any, workspace_id: str, lakehouse_id: str, upload: Upload, replace_existing: bool
) -> int:
    """Replace the target folder with the upload's files. Returns the number of files written.

    The files go to '<target>.staging' first. Two renames then swap it into place.
    A failed upload leaves the target folder untouched. A non-empty folder that no upload
    created is only replaced with replace_existing.
    """
    file_system = client.get_file_system_client(workspace_id)
    live = f"{lakehouse_id}/{upload.target}"
    staging, old = f"{live}.staging", f"{live}.old"
    if not replace_existing:
        _check_replaceable(file_system, live, upload)
    _delete_if_present(file_system, staging)
    _delete_if_present(file_system, old)

    try:
        file_system.get_directory_client(staging).create_directory()
        count = _write_files(file_system, staging, upload)
        file_system.get_file_client(f"{staging}/{UPLOAD_MARKER}").create_file()
    except Exception:
        _delete_if_present(file_system, staging)
        raise

    had_live = file_system.get_directory_client(live).exists()
    if had_live:
        file_system.get_directory_client(live).rename_directory(f"{workspace_id}/{old}")
    try:
        file_system.get_directory_client(staging).rename_directory(f"{workspace_id}/{live}")
    except Exception:
        if had_live:
            file_system.get_directory_client(old).rename_directory(f"{workspace_id}/{live}")
        raise
    if had_live:
        file_system.get_directory_client(old).delete_directory()
    return count


def _check_replaceable(file_system: Any, live: str, upload: Upload) -> None:
    """Fail when the folder has files and no upload marker."""
    if not file_system.get_directory_client(live).exists():
        return
    if file_system.get_file_client(f"{live}/{UPLOAD_MARKER}").exists():
        return
    if next(iter(file_system.get_paths(path=live, recursive=False)), None) is None:
        return
    raise FabkitError(
        f"{upload.target} in {upload.lakehouse} has files that fabkit did not upload. "
        "An upload replaces the whole folder.",
        ["Pass --replace-existing to replace it anyway, or choose another target."],
    )


def _write_files(file_system: Any, folder: str, upload: Upload) -> int:
    planned = plan_files(upload)
    for local_path, relative in planned:
        file_client = file_system.get_file_client(f"{folder}/{relative}")
        if local_path.stat().st_size == 0:
            # upload_data skips 0-byte data
            file_client.create_file()
        else:
            with local_path.open("rb") as handle:
                file_client.upload_data(handle, overwrite=True)
    return len(planned)


def _delete_if_present(file_system: Any, folder: str) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    with contextlib.suppress(ResourceNotFoundError):
        file_system.get_directory_client(folder).delete_directory()


def upload_all(
    credential: Any, workspace_id: str, uploads: list[Upload], replace_existing: bool
) -> None:
    """Replace each upload's folder. A lakehouse that is not a GUID is found by name."""
    try:
        client = onelake_client(credential)
        for upload in uploads:
            lakehouse_id = (
                upload.lakehouse
                if GUID_PATTERN.match(upload.lakehouse)
                else workspaces.resolve_lakehouse_id(credential, workspace_id, upload.lakehouse)
            )
            if is_dbt_project(upload):
                print(f"{upload.local} is a dbt project: skipping {', '.join(DBT_EXCLUDES)}.")
            count = replace_folder(client, workspace_id, lakehouse_id, upload, replace_existing)
            target = f"{upload.lakehouse}:{upload.target}"
            print(f"Uploaded {count} file(s) from {upload.local} to {target}")
    except FabkitError:
        raise
    except Exception as error:  # Azure SDK errors vary; all of them fail the upload
        raise FabkitError(f"Upload failed: {error}") from error


def run_upload(args: argparse.Namespace) -> int:
    """Replace a lakehouse folder with a local folder. Needs only the workspaces in config.yml."""
    config = load_deploy_config(Path(args.config))
    require_environment(config, args.environment)
    upload = Upload(Path(args.source), args.lakehouse, args.target, tuple(args.exclude or ()))
    check_uploads([upload])
    for warning in upload_warnings(upload):
        print(f"warning: {warning}", file=sys.stderr)

    credential = workspaces.default_credential()
    workspace_id = workspaces.resolve_workspace_id(credential, config, args.environment)
    upload_all(credential, workspace_id, [upload], args.replace_existing)
    return 0


def onelake_client(credential: Any) -> Any:
    """An authenticated OneLake client."""
    from azure.storage.filedatalake import DataLakeServiceClient

    return DataLakeServiceClient(account_url=ONELAKE_URL, credential=credential)
