from __future__ import annotations

import pytest

from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import ObjectNotFoundError


def test_local_object_store_blocks_parent_path_escape(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="escapes store root"):
        store.write_text("../outside.txt", "blocked")

    assert not (tmp_path / "outside.txt").exists()


def test_local_object_store_blocks_windows_separator_escape(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="escapes store root"):
        store.read_text("..\\outside.txt")


def test_local_object_store_reads_and_lists_only_inside_root(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")

    store.write_text("workspaces/default/manifest.json", "{}")

    assert store.read_text("workspaces/default/manifest.json") == "{}"
    assert store.list_keys("workspaces/default") == ["workspaces/default/manifest.json"]
    with pytest.raises(ObjectNotFoundError):
        store.read_text("workspaces/default/missing.json")
