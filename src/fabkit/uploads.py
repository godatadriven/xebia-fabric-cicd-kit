"""Lakehouse uploads: parsing, checks and the OneLake write."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fabkit.errors import FabkitError
from fabkit.parameters import GUID_PATTERN

ONELAKE_URL = "https://onelake.dfs.fabric.microsoft.com"


@dataclass(frozen=True)
class Upload:
    """One --upload: a local file or folder copied into a lakehouse's Files/."""

    local: Path
    lakehouse_id: str
    target: str


def parse_upload(spec: str) -> Upload:
    """Parse '<local path>=<lakehouse ID>:<Files/ path>'.

    "lib/=<guid>:Files/_lib/lib/" -> Upload(Path("lib"), "<guid>", "Files/_lib/lib")
    """
    local, sep, rest = spec.partition("=")
    lakehouse_id, sep2, target = rest.partition(":")
    if not (sep and sep2 and local and lakehouse_id and target):
        raise argparse.ArgumentTypeError(
            f"--upload {spec!r}: expected '<local path>=<lakehouse ID>:<Files/ path>'."
        )
    if not GUID_PATTERN.match(lakehouse_id):
        raise argparse.ArgumentTypeError(f"--upload {spec!r}: the lakehouse must be a GUID.")
    target = target.strip("/")
    if target != "Files" and not target.startswith("Files/"):
        raise argparse.ArgumentTypeError(f"--upload {spec!r}: the target must be under Files/.")
    if ".." in target.split("/"):
        raise argparse.ArgumentTypeError(f"--upload {spec!r}: the target must not contain '..'.")
    return Upload(Path(local), lakehouse_id, target)


def check_uploads(uploads: list[Upload]) -> None:
    """Fail when a local path does not exist. Runs before anything is published."""
    missing = [str(upload.local) for upload in uploads if not upload.local.exists()]
    if missing:
        raise FabkitError("Upload source not found. Nothing was published.", missing)


def plan_files(upload: Upload) -> list[tuple[Path, str]]:
    """Every local file of one upload, with its path inside the lakehouse."""
    if upload.local.is_file():
        return [(upload.local, f"{upload.target}/{upload.local.name}")]
    return [
        (path, f"{upload.target}/{path.relative_to(upload.local).as_posix()}")
        for path in sorted(upload.local.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]


def run_uploads(client: Any, workspace_id: str, uploads: list[Upload]) -> int:
    """Write every planned file to OneLake. Returns the number of files written."""
    file_system = client.get_file_system_client(workspace_id)
    count = 0
    for upload in uploads:
        for local_path, target in plan_files(upload):
            file_client = file_system.get_file_client(f"{upload.lakehouse_id}/{target}")
            if local_path.stat().st_size == 0:
                # upload_data skips 0-byte data
                file_client.create_file()
            else:
                with local_path.open("rb") as handle:
                    file_client.upload_data(handle, overwrite=True)
            print(f"Uploaded {local_path} -> {upload.lakehouse_id}/{target}")
            count += 1
    return count


def onelake_client(credential: Any) -> Any:
    """An authenticated OneLake client."""
    from azure.storage.filedatalake import DataLakeServiceClient

    return DataLakeServiceClient(account_url=ONELAKE_URL, credential=credential)
