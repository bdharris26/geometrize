from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import threading
import urllib.request
import uuid
import zlib
from contextlib import contextmanager
from http.client import RemoteDisconnected
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from geometrize_py import native, web
from geometrize_py.images import image_to_data_url, open_image_bytes
from geometrize_py.native import native_available
from geometrize_py.resources import WorkBudget
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer


class _SourceHandler(GeometrizeRequestHandler):
    def do_POST(self) -> None:
        try:
            super().do_POST()
        finally:
            request_id = self.headers.get("X-Test-Request-ID")
            if request_id:
                self.server.completions[request_id].set()


@pytest.fixture
def source_server():
    server = GeometrizeServer(("127.0.0.1", 0), _SourceHandler)
    server.completions = {}
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def _response(server, path, payload):
    request_id = uuid.uuid4().hex
    completion = threading.Event()
    server.completions[request_id] = completion
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}", json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json", "X-Test-Request-ID": request_id}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            yield response
    finally:
        assert completion.wait(5), "Server did not release source/request admission"
        server.completions.pop(request_id)


def _post(server, path, payload, *, stream=False):
    with _response(server, path, payload) as response:
        return [json.loads(line) for line in response] if stream else json.load(response)


def _error(server, path, payload, code, status=400):
    with pytest.raises(HTTPError) as error:
        _post(server, path, payload)
    assert error.value.code == status
    result = json.load(error.value)
    assert result["code"] == code
    return result


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _animation():
    def pixels(width, height, color):
        return zlib.compress(b"".join(b"\0" + bytes(color) * width for _ in range(height)))

    raw = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
    raw += _chunk(b"acTL", struct.pack(">II", 2, 0))
    raw += _chunk(b"IDAT", pixels(4, 4, (255, 255, 255, 255)))
    raw += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 4, 4, 0, 0, 1, 10, 0, 0))
    raw += _chunk(b"fdAT", struct.pack(">I", 1) + pixels(4, 4, (255, 0, 0, 255)))
    raw += _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 2, 2, 2, 0, 0, 9, 100, 0, 1))
    raw += _chunk(b"fdAT", struct.pack(">I", 3) + pixels(2, 2, (0, 255, 0, 128)))
    return raw + _chunk(b"IEND", b"")


def _url(raw, mime="application/octet-stream"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _options(**changes):
    return {"steps": 1, "max_threads": 1, "shape_types": ["rectangle"], "shape_count": 8, "mutations": 8,
            "seed": 123, "max_size": 32, "source": {"frame": 2, "matte": [255, 255, 255]},
            "background": [9, 19, 29], **changes}


def test_prepare_identifies_original_apng_and_returns_correct_static_composed_preview_without_native(
    source_server, monkeypatch,
) -> None:
    monkeypatch.setattr(native, "_native", None)
    raw = _animation()
    result = _post(source_server, "/api/source/prepare", {"image": _url(raw), "source": _options()["source"]})
    assert result["source"] == _options()["source"]
    assert result["mime_type"] == "image/apng"
    assert result["default_image"] is True
    assert result["frame_count"] == 3 and result["frames_truncated"] is False
    assert result["frame_duration_ms"] == 90
    assert (result["width"], result["height"], result["preview_width"], result["preview_height"]) == (4, 4, 4, 4)
    with open_image_bytes(web.image_data_url_bytes(result["preview_data_url"])) as preview:
        assert preview.getpixel((0, 0)) == (127, 128, 0, 255)
        assert preview.getpixel((3, 3)) == (255, 0, 0, 255)
    assert not source_server.sessions and source_server.work_budget.usage() == (0, 0)


def test_prepare_preview_is_bounded_but_reports_full_selected_dimensions_and_legacy_rgba(source_server) -> None:
    image = Image.new("RGBA", (2048, 2), (20, 40, 60, 128))
    result = _post(source_server, "/api/source/prepare", {"image": image_to_data_url(image), "source": None})
    assert result["source"] == {"frame": 0, "matte": None}
    assert (result["width"], result["height"]) == (2048, 2)
    assert (result["preview_width"], result["preview_height"]) == (1024, 1)
    assert result["frame_count"] == 1 and result["frame_duration_ms"] is None
    with open_image_bytes(web.image_data_url_bytes(result["preview_data_url"])) as preview:
        assert preview.getpixel((0, 0))[3] == 128


@pytest.mark.parametrize("source", [False, [], {"frame": True}, {"frame": 256}, {"matte": [0, False, 0]}])
def test_prepare_invalid_source_is_rejected_before_base64_decoding(source_server, monkeypatch, source) -> None:
    monkeypatch.setattr(web, "image_data_url_bytes", lambda *_args: pytest.fail("Invalid options decoded source"))
    _error(source_server, "/api/source/prepare", {"image": "bad", "source": source}, "invalid_options")
    assert source_server.work_budget.usage() == (0, 0)


def test_prepare_rejects_unavailable_frame_and_unknown_keys_before_pillow(source_server, monkeypatch) -> None:
    monkeypatch.setattr(web, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("Unavailable frame was opened"))
    _error(source_server, "/api/source/prepare", {"image": _url(_animation()), "source": {"frame": 3}}, "invalid_image")
    _error(source_server, "/api/source/prepare", {"image": "bad", "background": [0, 0, 0]}, "invalid_request")
    assert source_server.work_budget.usage() == (0, 0)


def test_prepare_worker_admission_precedes_container_probe_and_releases_body_lease(source_server, monkeypatch) -> None:
    budget = source_server.work_budget
    busy = budget.try_reserve(budget.workers, 1)
    assert busy is not None
    monkeypatch.setattr(web, "probe_source", lambda *_args: pytest.fail("Busy source container was scanned"))
    try:
        _error(source_server, "/api/source/prepare", {"image": _url(_animation())}, "renderer_busy", 429)
        assert budget.usage() == (budget.workers, 1)
    finally:
        budget.release(busy)


def test_prepare_request_admission_precedes_body_read(source_server, monkeypatch) -> None:
    source_server.work_budget = WorkBudget(1, 32 * 1024)
    monkeypatch.setattr(_SourceHandler, "_read_json", lambda _self: pytest.fail("Unadmitted body was read"))
    _error(source_server, "/api/source/prepare", {}, "memory_limit")
    assert source_server.work_budget.usage() == (0, 0)


def test_new_run_failed_worker_resize_preserves_busy_status_and_releases_probe_lease(
    source_server, monkeypatch,
) -> None:
    source_server.work_budget = WorkBudget(2, 32 * 1024 * 1024)
    actual_probe = web.probe_source
    competing = []

    def probe(*args):
        result = actual_probe(*args)
        lease = source_server.work_budget.try_reserve(1, 1)
        assert lease is not None
        competing.append(lease)
        return result

    monkeypatch.setattr(web, "probe_source", probe)
    monkeypatch.setattr(web, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("Busy run decoded pixels"))
    try:
        _error(source_server, "/api/run/stream", {"image": _url(_animation()), "options": _options(max_threads=2)},
               "renderer_busy", 429)
        assert source_server.work_budget.usage() == (1, 1)
        assert not source_server.sessions and not source_server.active_runs
    finally:
        for lease in competing:
            source_server.work_budget.release(lease)


@pytest.mark.parametrize("metadata_kind", [b"eXIf", b"tEXt"])
@pytest.mark.parametrize("path", ["/api/source/prepare", "/api/run/stream", "/api/palette", "/api/restore"])
def test_logical_exif_expansion_is_admitted_before_any_pillow_decoder(
    source_server, monkeypatch, metadata_kind, path,
) -> None:
    if path == "/api/restore" and not native_available():
        pytest.skip("native replay validation is unavailable")
    aliases, size = 100, 262144
    offset = 8 + 2 + 12 * (aliases + 1) + 4
    entries = struct.pack("<HHII", 274, 3, 1, 1)
    entries += b"".join(struct.pack("<HHII", 1000 + i, 7, size, offset) for i in range(aliases))
    exif = b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", aliases + 1) + entries + b"\0" * 4 + b"A" * size
    if metadata_kind == b"tEXt":
        exif = b"exif\0Exif\0\0" + exif
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="PNG")
    raw = output.getvalue()
    raw = raw[:33] + _chunk(metadata_kind, exif) + raw[33:]
    payload = {"image": _url(raw), "options": {"max_threads": 1},
               "result": {"width": 1, "height": 1, "background": [0, 0, 0, 255], "shapes": []}}
    if path in {"/api/source/prepare", "/api/palette"}:
        payload = {"image": payload["image"]}
    source_server.work_budget = WorkBudget(2, 16 * 1024 * 1024)
    monkeypatch.setattr(web, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("Unadmitted EXIF was decoded"))
    _error(source_server, path, payload, "memory_limit")
    assert source_server.work_budget.usage() == (0, 0)
    assert not source_server.sessions


def test_prepare_keeps_probe_decode_and_serialization_leased_and_frees_owned_stills(source_server, monkeypatch) -> None:
    observed, owned = [], []
    actual_probe, actual_open, actual_send = web.probe_source, web.open_image_bytes, _SourceHandler._send_json

    def probe(*args):
        observed.append(source_server.work_budget.usage())
        return actual_probe(*args)

    def open_image(*args, **kwargs):
        observed.append(source_server.work_budget.usage())
        image = actual_open(*args, **kwargs)
        owned.append(image)
        return image

    def send(handler, payload, *args, **kwargs):
        observed.append(source_server.work_budget.usage())
        for image in owned:
            with pytest.raises(ValueError, match="closed image"):
                image.getpixel((0, 0))
        actual_send(handler, payload, *args, **kwargs)

    monkeypatch.setattr(web, "probe_source", probe)
    monkeypatch.setattr(web, "open_image_bytes", open_image)
    monkeypatch.setattr(_SourceHandler, "_send_json", send)
    _post(source_server, "/api/source/prepare", {"image": _url(_animation()), "source": {"frame": 2}})
    assert all(workers == 1 and memory > 64 * 1024 for workers, memory in observed)
    assert observed[1][1] > observed[0][1]
    assert observed[2] == observed[1]
    assert source_server.work_budget.usage() == (0, 0)


def test_prepare_response_disconnect_and_decode_error_release_all_admission(source_server, monkeypatch) -> None:
    def disconnected(*_args, **_kwargs):
        raise ConnectionAbortedError("client disconnected")

    with monkeypatch.context() as patch:
        patch.setattr(_SourceHandler, "_send_json", disconnected)
        with pytest.raises(RemoteDisconnected):
            _post(source_server, "/api/source/prepare", {"image": _url(_animation())})
    assert source_server.work_budget.usage() == (0, 0)
    raw = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw += _chunk(b"IDAT", b"not-zlib") + _chunk(b"IEND", b"")
    _error(source_server, "/api/source/prepare", {"image": _url(raw)}, "invalid_image")
    assert source_server.work_budget.usage() == (0, 0)
    assert _post(source_server, "/api/source/prepare", {"image": _url(_animation())})["frame_count"] == 3


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_run_restore_palette_and_export_share_selected_frozen_target(source_server) -> None:
    original = _url(_animation())
    options = _options()
    events = _post(source_server, "/api/run/stream", {"image": original, "options": options}, stream=True)
    start, head = events[0], events[-1]
    assert head["event"] == "complete" and head["shape_count"] == 1
    expected = bytes((127, 128, 0, 255)) * 2 + bytes((255, 0, 0, 255)) * 2
    expected = expected * 2 + bytes((255, 0, 0, 255)) * 8
    digest = hashlib.sha256(expected).hexdigest()
    assert start["target_digest"] == head["target_digest"] == digest
    assert start["source"] == head["source"] == options["source"]
    assert head["background"] == [9, 19, 29, 255]
    assert head["batch_summary"]["source"] == options["source"]
    extracted = _post(source_server, "/api/palette", {"image": original, "source": options["source"], "max_colors": 32})
    assert sorted(extracted["colors"]) == [[127, 128, 0], [255, 0, 0]]
    restored = _post(source_server, "/api/restore", {
        "image": original, "options": _options(background=[255, 255, 255]), "result": head,
    })
    assert restored["target_digest"] == digest
    assert restored["source"] == options["source"]
    assert restored["background"] == head["background"]
    assert restored["attempts"] == 0 and restored["restored_shape_count"] == 1
    assert restored["preview"] == head["preview"]
    exported = _post(source_server, "/api/export", {"result": restored, "export_size": 32})
    assert exported["target_digest"] == digest
    assert exported["background"] == head["background"]
    assert source_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_source_mismatch_precedes_fitting_admission_and_cancellation_reset(source_server, monkeypatch) -> None:
    events = _post(source_server, "/api/run/stream", {"image": _url(_animation()), "options": _options()}, stream=True)
    head = events[-1]
    session = source_server.get_session(head["session_id"])
    session.request_cancel()
    counters = (session.attempts, session.revision, session.batch_count)
    source_server.work_budget = WorkBudget(1, 64 * 1024)
    monkeypatch.setattr(_SourceHandler, "_reserve_fit",
                        lambda *_args, **_kwargs: pytest.fail("Changed source reserved fit"))
    _error(source_server, "/api/run/stream", {
        "session_id": head["session_id"], "options": _options(source={"frame": 0, "matte": None}),
    }, "source_mismatch", 409)
    assert session._cancel_requested.is_set()
    assert (session.attempts, session.revision, session.batch_count) == counters
    assert session.target_digest == head["target_digest"]
    assert source_server.work_budget.usage() == (0, 0) and not source_server.active_runs


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_digest_mismatch_never_publishes_or_changes_the_parent(source_server, monkeypatch) -> None:
    events = _post(source_server, "/api/run/stream", {"image": _url(_animation()), "options": _options()}, stream=True)
    head = events[-1]
    parent = source_server.get_session(head["session_id"])
    snapshot = (parent.attempts, parent.revision, parent.result().image.tobytes())
    monkeypatch.setattr(native._native, "restore_rgba",
                        lambda *_args, **_kwargs: pytest.fail("Wrong target was replayed"))
    _error(source_server, "/api/restore", {
        "image": _url(_animation()), "options": _options(source={"frame": 0, "matte": None}), "result": head,
    }, "invalid_result")
    assert list(source_server.sessions) == [head["session_id"]]
    assert (parent.attempts, parent.revision, parent.result().image.tobytes()) == snapshot
    assert source_server.work_budget.usage() == (0, 0)


def test_stale_background_extension_reports_native_unavailable_and_releases_preparation(
    source_server, monkeypatch,
) -> None:
    monkeypatch.setattr(native, "_native", SimpleNamespace(
        is_available=lambda: True,
        RunnerSession=lambda *_args: pytest.fail("Unsupported background reached native"),
    ))
    _error(source_server, "/api/run/stream", {"image": _url(_animation()), "options": _options()},
           "native_unavailable", 503)
    assert not source_server.sessions and source_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_wrapped_original_source_prepares_extracts_and_restores_identical_target_bytes(
    source_server, monkeypatch,
) -> None:
    original = _animation()
    encoded = base64.b64encode(original).decode("ascii")
    wrapped = "data:image/apng;base64," + "\r\n".join(encoded[i:i + 17] for i in range(0, len(encoded), 17))
    decoded = []
    actual_open = web.open_image_bytes

    def open_image(raw, *args, **kwargs):
        decoded.append(raw)
        return actual_open(raw, *args, **kwargs)

    monkeypatch.setattr(web, "open_image_bytes", open_image)
    prepared = _post(source_server, "/api/source/prepare", {"image": wrapped, "source": _options()["source"]})
    assert prepared["mime_type"] == "image/apng" and prepared["frame_count"] == 3
    colors = _post(source_server, "/api/palette", {"image": wrapped, "source": _options()["source"], "max_colors": 32})
    assert sorted(colors["colors"]) == [[127, 128, 0], [255, 0, 0]]
    events = _post(source_server, "/api/run/stream", {"image": wrapped, "options": _options()}, stream=True)
    head = events[-1]
    assert head["event"] == "complete" and head["shape_count"] == 1
    restored = _post(source_server, "/api/restore", {"image": wrapped, "options": _options(), "result": head})
    assert restored["target_digest"] == head["target_digest"]
    assert restored["preview"] == head["preview"] and restored["restored_shape_count"] == 1
    assert restored["attempts"] == 0
    assert len(decoded) == 4 and all(raw == original for raw in decoded)
    assert source_server.work_budget.usage() == (0, 0)
