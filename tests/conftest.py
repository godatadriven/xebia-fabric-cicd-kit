from pathlib import Path
from typing import Any

import pytest
from helpers import CONFIG, PLATFORM, FabricRepo, FakeCredential, FakeFabric

from fabkit import connections, deploy, uploads, workspaces


@pytest.fixture
def fabric_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FabricRepo:
    """A repository with config.yml, an empty parameter.yml and one notebook."""
    repo = FabricRepo(tmp_path)
    repo.write("config.yml", CONFIG)
    repo.write("src/parameter.yml", "find_replace: []\n")
    repo.write("src/nb.Notebook/.platform", PLATFORM)
    repo.write("src/nb.Notebook/notebook-content.py", "# Fabric notebook source\n")
    monkeypatch.chdir(tmp_path)
    return repo


@pytest.fixture
def fabric(monkeypatch: pytest.MonkeyPatch) -> FakeFabric:
    """Replace every Fabric, OneLake and login call. The rest of fabkit runs for real."""
    fake = FakeFabric()
    monkeypatch.delenv("RUNNER_DEBUG", raising=False)
    monkeypatch.setattr(workspaces, "default_credential", FakeCredential)
    monkeypatch.setattr(deploy, "_deploy_with_config", lambda **kwargs: fake.deploys.append(kwargs))
    monkeypatch.setattr(uploads, "onelake_client", lambda _credential: fake.onelake)
    monkeypatch.setattr(workspaces, "get_json", fake.get_json)

    def list_connections(_credential: Any) -> dict[str, str]:
        fake.connection_lookups += 1
        return fake.visible_connections

    monkeypatch.setattr(connections, "list_connections", list_connections)
    return fake
