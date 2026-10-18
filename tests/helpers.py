"""Test helpers: a Fabric repository on disk, fakes for Fabric and OneLake, assertions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

CONFIG = """\
core:
  workspace:
    DEV: sales-dev
    PROD: sales-prod
  repository_directory: src
  parameter: src/parameter.yml
  item_types_in_scope: [Notebook]
unpublish:
  skip: false
"""

PLATFORM = """\
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
  "metadata": {"type": "Notebook", "displayName": "nb"},
  "config": {"version": "2.0", "logicalId": "11111111-1111-1111-1111-111111111111"}
}
"""


def platform_json(item_type: str, display_name: str, logical_id: str) -> str:
    """.platform text for one item."""
    return json.dumps(
        {
            "metadata": {"type": item_type, "displayName": display_name},
            "config": {"version": "2.0", "logicalId": logical_id},
        }
    )


@dataclass
class FabricRepo:
    """A repository on disk with config.yml at its root."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.yml"

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def find_replace(find: str | None, **values: Any) -> str:
    """parameter.yml text with one find_replace entry.

    find_replace("x", DEV="y", PROD="z") -> find_value "x", replace_value {DEV: y, PROD: z}.
    find=None leaves find_value out.
    """
    entry: dict[str, Any] = {} if find is None else {"find_value": find}
    entry["replace_value"] = values
    return yaml.safe_dump({"find_replace": [entry]}, sort_keys=False)


def assert_reported(messages: list[str], *fragments: str) -> None:
    """At least one message contains every fragment."""
    assert any(all(f in m for f in fragments) for m in messages), (
        f"no message containing {fragments} in {messages}"
    )


class FakeCredential:
    """A credential that hands out a dummy token."""

    def get_token(self, *_: Any) -> Any:
        return SimpleNamespace(token="token")


class FakeOneLake:
    """A OneLake file system in memory: files as '<workspace>/<path>' -> bytes, plus folders.

    fail_upload_on fails a write whose path contains it.
    fail_rename_to fails the first rename whose destination ends with it.
    """

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}
        self.folders: set[str] = set()
        self.fail_upload_on: str | None = None
        self.fail_rename_to: str | None = None

    def exists(self, path: str) -> bool:
        return path in self.folders or any(key.startswith(f"{path}/") for key in self.written)

    def get_file_system_client(self, workspace_id: str) -> Any:
        def get_file_client(path: str) -> Any:
            full = f"{workspace_id}/{path}"

            def upload_data(data: Any, overwrite: bool) -> None:
                assert overwrite, "uploads must overwrite"
                if self.fail_upload_on and self.fail_upload_on in path:
                    raise RuntimeError(f"upload of {path} failed")
                self.written[full] = data.read()

            def create_file() -> None:
                self.written[full] = b""

            return SimpleNamespace(
                upload_data=upload_data,
                create_file=create_file,
                exists=lambda: full in self.written,
            )

        def get_directory_client(path: str) -> Any:
            full = f"{workspace_id}/{path}"

            def inside(key: str) -> bool:
                return key.startswith(f"{full}/")

            def create_directory() -> None:
                self.folders.add(full)

            def delete_directory() -> None:
                if not self.exists(full):
                    raise ResourceNotFoundError(f"{path} not found")
                self.written = {k: v for k, v in self.written.items() if not inside(k)}
                self.folders = {f for f in self.folders if f != full and not inside(f)}

            def rename_directory(new_name: str) -> None:
                if self.fail_rename_to and new_name.endswith(self.fail_rename_to):
                    self.fail_rename_to = None
                    raise RuntimeError(f"rename to {new_name} failed")
                if self.exists(new_name):
                    raise ResourceExistsError(f"{new_name} already exists")

                def move(key: str) -> str:
                    return new_name + key[len(full) :] if key == full or inside(key) else key

                self.written = {move(k): v for k, v in self.written.items()}
                self.folders = {move(f) for f in self.folders}

            return SimpleNamespace(
                exists=lambda: self.exists(full),
                create_directory=create_directory,
                delete_directory=delete_directory,
                rename_directory=rename_directory,
            )

        def get_paths(path: str, recursive: bool = True) -> list[Any]:
            assert not recursive, "only direct children are listed"
            prefix = f"{workspace_id}/{path}/"
            children = {
                key[len(prefix) :].split("/")[0]
                for key in [*self.written, *self.folders]
                if key.startswith(prefix)
            }
            return [SimpleNamespace(name=f"{path}/{child}") for child in sorted(children)]

        return SimpleNamespace(
            get_file_client=get_file_client,
            get_directory_client=get_directory_client,
            get_paths=get_paths,
        )


CONN = "eeeeeeee-0000-0000-0000-000000000005"
LAKEHOUSE_NAME = "lh_landing"
LAKEHOUSE_ID = "cccccccc-0000-0000-0000-000000000003"


class FakeFabric:
    """Stands in for Fabric and OneLake, and records what fabkit asked of them."""

    def __init__(self) -> None:
        self.deploys: list[dict[str, Any]] = []
        self.connection_lookups = 0
        self.visible_connections = {CONN: "warehouse"}
        self.workspaces = [{"id": "ws-dev-guid", "displayName": "sales-dev"}]
        self.lakehouses = [{"id": LAKEHOUSE_ID, "displayName": LAKEHOUSE_NAME}]
        self.onelake = FakeOneLake()

    def get_json(self, url: str, _token: str) -> dict[str, Any]:
        """Workspace and lakehouse listings, one page each."""
        if url.endswith("/lakehouses"):
            return {"value": self.lakehouses}
        return {"value": self.workspaces}
