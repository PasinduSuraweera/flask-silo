"""Tests for FileStore per-session file management.

Covers: directory creation, save (bytes + file-like), get_path, list_files,
cleanup (individual + all), total_size, and cross-session isolation.
"""

import io
import os

import pytest
from flask_silo import FileStore


@pytest.fixture
def file_store(tmp_path):
    return FileStore(str(tmp_path / "uploads"))


class TestFileStore:
    def test_session_dir_created(self, file_store):
        d = file_store.session_dir("sid-001")
        assert os.path.isdir(d)

    def test_save_bytes(self, file_store):
        path = file_store.save("sid-001", "test.txt", b"hello world")
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "hello world"

    def test_save_file_object(self, file_store):
        data = io.BytesIO(b"file content")
        path = file_store.save("sid-001", "data.bin", data)
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == b"file content"

    def test_get_path_existing(self, file_store):
        file_store.save("sid-001", "test.txt", b"data")
        path = file_store.get_path("sid-001", "test.txt")
        assert path is not None
        assert os.path.isfile(path)

    def test_get_path_nonexistent(self, file_store):
        assert file_store.get_path("sid-001", "nope.txt") is None

    def test_list_files(self, file_store):
        file_store.save("sid-001", "a.txt", b"a")
        file_store.save("sid-001", "b.txt", b"b")
        files = file_store.list_files("sid-001")
        assert set(files) == {"a.txt", "b.txt"}

    def test_list_files_empty_session(self, file_store):
        assert file_store.list_files("sid-001") == []

    def test_cleanup_session(self, file_store):
        file_store.save("sid-001", "test.txt", b"data")
        d = file_store.session_dir("sid-001")
        assert os.path.isdir(d)
        file_store.cleanup("sid-001")
        assert not os.path.isdir(d)

    def test_cleanup_nonexistent_is_noop(self, file_store):
        file_store.cleanup("nonexistent")  # should not raise

    def test_cleanup_all(self, file_store):
        file_store.save("sid-001", "a.txt", b"a")
        file_store.save("sid-002", "b.txt", b"b")
        file_store.cleanup_all()
        assert file_store.list_files("sid-001") == []
        assert file_store.list_files("sid-002") == []

    def test_total_size_bytes(self, file_store):
        file_store.save("sid-001", "test.txt", b"hello")
        assert file_store.total_size_bytes == 5

    def test_sessions_isolated(self, file_store):
        file_store.save("sid-001", "test.txt", b"data-1")
        file_store.save("sid-002", "test.txt", b"data-2")
        p1 = file_store.get_path("sid-001", "test.txt")
        p2 = file_store.get_path("sid-002", "test.txt")
        assert p1 != p2
        with open(p1) as f:
            assert f.read() == "data-1"
        with open(p2) as f:
            assert f.read() == "data-2"
