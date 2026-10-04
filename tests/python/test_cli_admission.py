from __future__ import annotations

import json
import os
import tracemalloc
from pathlib import Path

import pytest

from geometrize_py.cli_jobs import Admission, PublicationError, preflight_paths, publish
from geometrize_py.json_memory import json_memory_bytes


def test_wrapped_scalar_admission_is_linear_and_does_not_build_a_regex_stack() -> None:
    url = "DATA:IMAGE/PNG;BASE64," + "AAAA\r\n" * 150_000
    raw = json.dumps({"source": {"data_url": url}, "preview_data_url": url}).encode()
    tracemalloc.start()
    charge = json_memory_bytes(raw, image_fields=(b"data_url", b"preview_data_url"))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(raw) * 4 <= charge < len(raw) * 5
    assert peak < 256 * 1024


@pytest.mark.parametrize(
    "raw",
    [
        b'{"data_url":"data:image/png;base64,AAAA\\uD83D\\uDE00"}',
        '{"name":"😀","data_url":"data:image/png;base64,AAAA"}'.encode(),
        b'{"data_url":"data:image/png;base64,AAAA\\"\\uD83D\\uDE00"}',
    ],
)
def test_unicode_and_escaped_quote_candidates_receive_generic_admission(raw: bytes) -> None:
    raw = raw.replace(b"AAAA", b"A" * 20_000)
    assert json_memory_bytes(raw, image_fields=(b"data_url",)) == len(raw) * 32


def test_json_parse_is_admitted_before_loader_and_unicode_fallback(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    raw = json.dumps({"other": [0] * 50_000}).encode()
    path.write_bytes(raw)
    called = []
    with Admission(1, 1) as admission, pytest.raises(ValueError, match="memory budget"):
        admission.read_json(path, lambda text: called.append(text))
    assert called == [] and admission.budget.usage() == (0, 0)


def test_bounded_read_rejects_oversized_files_before_opening(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "palette.json"
    path.write_bytes(b" " * 100)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("read should not occur"))
    with Admission(1, 2) as admission, pytest.raises(ValueError, match="exceeds"):
        admission.read_json(path, limit=99)


def test_hardlinks_allow_read_aliases_but_reject_all_writers(tmp_path: Path) -> None:
    source = tmp_path / "source"
    alias = tmp_path / "alias"
    source.write_bytes(b"original")
    os.link(source, alias)
    reads = [("first", source), ("second", alias)]
    preflight_paths(reads, [])
    with pytest.raises(ValueError, match="path must differ"):
        preflight_paths(reads, [("output", alias)])
    with pytest.raises(ValueError, match="path must differ"):
        preflight_paths([], [("first output", source), ("second output", alias)])
    assert source.read_bytes() == b"original"


def test_file_parent_and_planned_file_directory_conflicts_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="not a directory"):
        preflight_paths([("input", source)], [("PNG", source / "result.png")])
    future_file = tmp_path / "new"
    with pytest.raises(ValueError, match="parent directories"):
        preflight_paths([], [("first", future_file), ("second", future_file / "result.png")])


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem alias rules")
@pytest.mark.parametrize("name", ["result.png.", "result.png ", "CON.png", "result.png:stream"])
def test_absent_windows_output_aliases_and_devices_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Windows component"):
        preflight_paths([], [("output", tmp_path / name)])


def test_atomic_stage_failure_preserves_all_old_files(tmp_path: Path, monkeypatch) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"old1")
    two.write_bytes(b"old2")
    calls = []
    original_fsync = os.fsync

    def fail_second(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("disk full")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_second)
    with pytest.raises(PublicationError) as error:
        publish({one: b"new1", two: b"new2"})
    assert error.value.committed == []
    assert one.read_bytes() == b"old1" and two.read_bytes() == b"old2"
    assert list(tmp_path.glob(".geometrize-*.tmp")) == []


def test_partial_replacement_reports_only_committed_paths_and_cleans_closed_temps(tmp_path: Path, monkeypatch) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"old1")
    two.write_bytes(b"old2")
    original_replace = os.replace

    def fail_second(source: Path, target: Path) -> None:
        # Reopen before replacement: NamedTemporaryFile must already be closed
        # on Windows, including the temp that encounters replacement failure.
        with source.open("r+b"):
            pass
        if target == two:
            raise OSError("replacement failed")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(PublicationError) as error:
        publish({one: b"new1", two: b"new2"})
    assert error.value.committed == [str(one.resolve())]
    assert one.read_bytes() == b"new1" and two.read_bytes() == b"old2"
    assert list(tmp_path.glob(".geometrize-*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows denies replacement and cleanup of an open temp")
def test_locked_temp_cleanup_keeps_original_partial_commit_metadata(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"old1")
    two.write_bytes(b"old2")
    held = []

    def lock_second_temp() -> None:
        staged = next(path for path in tmp_path.glob(".geometrize-*.tmp") if path.read_bytes() == b"new2")
        held.append(staged.open("rb"))

    try:
        with pytest.raises(PublicationError) as error:
            publish({one: b"new1", two: b"new2"}, recheck=lock_second_temp)
        assert error.value.committed == [str(one.resolve())]
        assert len(error.value.temporary_paths) == 1
        assert error.value.temporary_paths[0] in str(error.value)
        assert one.read_bytes() == b"new1" and two.read_bytes() == b"old2"
    finally:
        for stream in held:
            stream.close()
        for staged in tmp_path.glob(".geometrize-*.tmp"):
            staged.unlink()
