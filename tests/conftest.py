from pathlib import Path

import pytest
from helpers import CONFIG, PLATFORM, FabricRepo


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
