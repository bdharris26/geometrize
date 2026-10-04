from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from geometrize_py import native, web
from geometrize_py.images import image_to_data_url
from geometrize_py.native import ImageSession, RunOptions, native_available
from geometrize_py.palette import Palette, palette_fitting_memory_bytes, palette_memory_bytes
from geometrize_py.resources import WorkBudget, fitting_memory_bytes
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer


class _PaletteServer(GeometrizeServer):
    completions: dict[str, threading.Event]


class _PaletteHandler(GeometrizeRequestHandler):
    def do_POST(self) -> None:
        try:
            super().do_POST()
        finally:
            request_id = self.headers.get("X-Test-Request-ID")
            if request_id:
                self.server.completions[request_id].set()


@pytest.fixture
def palette_server():
    server = _PaletteServer(("127.0.0.1", 0), _PaletteHandler)
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
        assert completion.wait(5), "Server did not finish releasing palette admission"
        server.completions.pop(request_id)


def _post(server, path, payload, *, stream=False):
    with _response(server, path, payload) as response:
        return [json.loads(line) for line in response] if stream else json.load(response)


def _image():
    source = Image.new("RGBA", (48, 32), (200, 110, 35, 255))
    source.paste((25, 70, 210, 255), (0, 0, 24, 32))
    return image_to_data_url(source)


def test_palette_extraction_returns_visible_rgb_metadata_without_native(palette_server, monkeypatch) -> None:
    monkeypatch.setattr(native, "_native", None)
    payload = _post(palette_server, "/api/palette", {"image": _image(), "max_colors": 32})
    assert sorted(payload["colors"]) == [[25, 70, 210], [200, 110, 35]]
    assert payload["requested_max_colors"] == 32
    assert (payload["sample_width"], payload["sample_height"]) == (48, 32)
    assert _post(palette_server, "/api/palette", {"image": _image()})["requested_max_colors"] == 8
    assert palette_server.work_budget.usage() == (0, 0)
    assert not palette_server.sessions


@pytest.mark.parametrize("count", [True, False, None, 0, 33, 8.0, "8"])
def test_palette_count_rejection_precedes_source_decoding(palette_server, monkeypatch, count) -> None:
    monkeypatch.setattr(web, "image_data_url_bytes", lambda _raw: pytest.fail("Invalid count decoded image"))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {"image": "bad", "max_colors": count})
    assert error.value.code == HTTPStatus.BAD_REQUEST
    assert json.load(error.value)["code"] == "invalid_options"
    assert palette_server.work_budget.usage() == (0, 0)


def test_palette_request_memory_is_admitted_before_body_read(palette_server, monkeypatch) -> None:
    palette_server.work_budget = WorkBudget(1, 32 * 1024)
    monkeypatch.setattr(_PaletteHandler, "_read_json", lambda _self: pytest.fail("Unadmitted body was read"))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {})
    assert json.load(error.value)["code"] == "memory_limit"
    assert palette_server.work_budget.usage() == (0, 0)


def test_palette_source_memory_is_admitted_before_full_decode(palette_server, monkeypatch) -> None:
    palette_server.work_budget = WorkBudget(1, 1024 * 1024)
    monkeypatch.setattr(web, "open_image_bytes", lambda _raw: pytest.fail("Unadmitted source was decoded"))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {"image": _image()})
    assert json.load(error.value)["code"] == "memory_limit"
    assert palette_server.work_budget.usage() == (0, 0)


def test_palette_extraction_respects_busy_admission_and_releases_after_errors(palette_server, monkeypatch) -> None:
    lease = palette_server.work_budget.try_reserve(palette_server.work_budget.workers, 1)
    assert lease is not None
    try:
        with pytest.raises(HTTPError) as error:
            _post(palette_server, "/api/palette", {"image": _image()})
        assert error.value.code == HTTPStatus.TOO_MANY_REQUESTS
        assert json.load(error.value)["code"] == "renderer_busy"
    finally:
        palette_server.work_budget.release(lease)
    monkeypatch.setattr(web, "extract_palette", lambda *_args: (_ for _ in ()).throw(ValueError("bad source")))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {"image": _image()})
    assert json.load(error.value)["code"] == "invalid_image"
    assert palette_server.work_budget.usage() == (0, 0)


def test_palette_extraction_leases_source_and_histogram_until_response_finishes(palette_server, monkeypatch) -> None:
    original = _PaletteHandler._send_json
    observed = []

    def send(handler, payload, *args, **kwargs):
        observed.append(handler.geometrize_server.work_budget.usage())
        original(handler, payload, *args, **kwargs)

    monkeypatch.setattr(_PaletteHandler, "_send_json", send)
    _post(palette_server, "/api/palette", {"image": _image()})
    assert observed[0][0] == 1
    assert observed[0][1] > palette_memory_bytes(48, 32)
    assert palette_server.work_budget.usage() == (0, 0)


def test_palette_transparent_source_and_unknown_fields_are_clear_errors(palette_server) -> None:
    transparent = image_to_data_url(Image.new("RGBA", (512, 512)))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {"image": transparent})
    payload = json.load(error.value)
    assert payload["code"] == "invalid_image"
    assert "fully transparent" in payload["error"]
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/palette", {"image": _image(), "count": 8})
    assert json.load(error.value)["code"] == "invalid_request"
    assert palette_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_palette_session_batches_snapshot_constraints_without_recoloring_history(palette_server) -> None:
    first_palette = {"colors": [[25, 70, 210], [200, 110, 35]], "strength": 1}
    options = {"steps": 1, "shape_count": 4, "mutations": 8, "max_threads": 1, "max_size": 48,
               "palette": first_palette}
    events = _post(palette_server, "/api/run/stream", {"image": _image(), "options": options}, stream=True)
    final = events[-1]
    assert final["event"] == "complete"
    assert len(final["shapes"]) == 1
    assert all(event["palette"] == first_palette for event in events)
    assert final["batch_summary"]["palette"] == first_palette
    next_palette = {"colors": [[0, 0, 0], [255, 255, 255]], "strength": 1}
    again = _post(palette_server, "/api/run/stream", {
        "session_id": final["session_id"], "options": {**options, "palette": next_palette},
    }, stream=True)[-1]
    assert again["shapes"][:1] == final["shapes"]
    assert again["palette"] == again["batch_summary"]["palette"] == next_palette
    assert again["attempts"] >= final["attempts"]
    assert palette_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stale_palette_native_rejects_continue_before_stream_or_counters_change(palette_server, monkeypatch) -> None:
    options = {"steps": 1, "shape_count": 4, "mutations": 8, "max_threads": 1, "max_size": 48}
    final = _post(palette_server, "/api/run/stream", {"image": _image(), "options": options}, stream=True)[-1]
    monkeypatch.setattr(native, "_native", SimpleNamespace(is_available=lambda: True))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/run/stream", {"session_id": final["session_id"], "options": {
            **options, "palette": {"colors": [[0, 0, 0]]},
        }})
    assert error.value.code == HTTPStatus.SERVICE_UNAVAILABLE
    assert "rebuild or reinstall" in json.load(error.value)["error"]
    retained = palette_server.get_session(final["session_id"])
    assert retained.attempts == final["attempts"]
    assert retained.revision == final["revision"]
    assert retained.palette is None
    assert not palette_server.active_runs
    assert palette_server.work_budget.usage() == (0, 0)


def test_positive_palette_fit_accounts_for_masks_and_replacement_before_native(palette_server, monkeypatch) -> None:
    ordinary_memory = fitting_memory_bytes(48, 32, 1)
    extra_memory = palette_fitting_memory_bytes(48, 32, 1)
    handler = object.__new__(_PaletteHandler)
    handler.server = palette_server
    palette_server.work_budget = WorkBudget(1, ordinary_memory + extra_memory - 1)
    options = RunOptions(max_threads=1, palette=Palette([(0, 0, 0)]))
    with pytest.raises(web.APIError, match="active memory budget"):
        handler._reserve_fit(options, 48, 32)
    assert palette_server.work_budget.usage() == (0, 0)
    _options, lease = handler._reserve_fit(RunOptions(max_threads=1), 48, 32)
    palette_server.work_budget.release(lease)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_palette_restore_admits_replay_without_reserving_future_fitting(palette_server, monkeypatch) -> None:
    source = Image.new("RGBA", (1, 4096), (200, 110, 35, 255))
    source.paste((25, 70, 210, 255), (0, 0, 1, 2048))
    options = RunOptions(steps=1, max_size=4096, max_threads=8, shape_count=1, mutations=8,
                         palette=Palette([(0, 0, 0), (255, 255, 255)]))
    scene = {"width": 1, "height": 4096, "background": [10, 20, 30, 255], "attempts": 17,
             "shapes": [{"type": "rectangle", "color": [17, 33, 65, 128],
                         "data": {"x1": 0, "y1": 2, "x2": 0, "y2": 25}}]}
    expected = ImageSession.from_scene(source, options, scene)
    palette_server.work_budget = WorkBudget(8, 2600 * 1024)
    observed = []
    send_json = _PaletteHandler._send_json

    def observe_restore(handler, payload, *args, **kwargs):
        if payload.get("event") == "restored":
            observed.append(handler.geometrize_server.work_budget.usage())
        return send_json(handler, payload, *args, **kwargs)

    monkeypatch.setattr(_PaletteHandler, "_send_json", observe_restore)
    confirmed = _post(palette_server, "/api/restore", {
        "image": image_to_data_url(source), "options": options.to_native_dict(), "result": scene,
    })
    restored = palette_server.get_session(confirmed["session_id"])
    assert restored is not None
    assert confirmed["palette"] == restored.palette.to_dict() == options.palette.to_dict()
    assert confirmed["attempts"] == restored.batch_count == 0
    assert confirmed["batch_summary"] is None
    assert confirmed["shape_count"] == confirmed["restored_shape_count"] == confirmed["revision"] == 1
    assert confirmed["background"] == scene["background"]
    assert confirmed["shapes"][0]["color"] == {"r": 17, "g": 33, "b": 65, "a": 128}
    assert restored.result().image.tobytes() == expected.result().image.tobytes()
    assert observed[0][0] == 1
    assert observed[0][1] <= palette_server.work_budget.memory_bytes
    assert observed[0][1] + palette_fitting_memory_bytes(1, 4096, 1) > palette_server.work_budget.memory_bytes
    assert fitting_memory_bytes(1, 4096, 8) <= palette_server.work_budget.memory_bytes
    assert (fitting_memory_bytes(1, 4096, 8) + palette_fitting_memory_bytes(1, 4096, 8)
            > palette_server.work_budget.memory_bytes)
    assert palette_server.work_budget.usage() == (0, 0)

    monkeypatch.setattr(ImageSession, "prepare_run", lambda *_args: pytest.fail("Unadmitted palette fit started"))
    with pytest.raises(HTTPError) as error:
        _post(palette_server, "/api/run/stream", {
            "session_id": confirmed["session_id"], "options": options.to_native_dict(),
        }, stream=True)
    assert json.load(error.value)["code"] == "memory_limit"
    assert restored.attempts == restored.batch_count == 0
    assert restored.revision == 1
    assert restored.palette == options.palette
    assert restored.result().image.tobytes() == expected.result().image.tobytes()
    assert not palette_server.active_runs
    assert palette_server.work_budget.usage() == (0, 0)
