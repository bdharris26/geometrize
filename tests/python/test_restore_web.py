from __future__ import annotations

import copy
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
from geometrize_py.errors import APIError
from geometrize_py.images import image_data_url_bytes, image_to_data_url, open_image_bytes
from geometrize_py.native import ImageSession, RunOptions, native_available
from geometrize_py.resources import WorkBudget
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer, _estimate_session_bytes


class _RestoreServer(GeometrizeServer):
    post_completions: dict[str, threading.Event]


class _RestoreHandler(GeometrizeRequestHandler):
    def do_POST(self) -> None:
        try:
            super().do_POST()
        finally:
            request_id = self.headers.get("X-Test-Request-ID")
            if request_id:
                self.server.post_completions[request_id].set()


@pytest.fixture
def restore_server():
    server = _RestoreServer(("127.0.0.1", 0), _RestoreHandler)
    server.post_completions = {}
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def _response(server, path: str, payload: dict):
    request_id = uuid.uuid4().hex
    completion = threading.Event()
    server.post_completions[request_id] = completion
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Test-Request-ID": request_id}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            yield response
    finally:
        assert completion.wait(5), "Server did not finish releasing request admission"
        server.post_completions.pop(request_id)


def _post(server, path: str, payload: dict, *, stream: bool = False) -> dict | list[dict]:
    with _response(server, path, payload) as response:
        return [json.loads(line) for line in response] if stream else json.load(response)


def _payload() -> dict:
    image = Image.new("RGBA", (32, 24), (20, 40, 60, 255))
    image.paste((210, 140, 30, 255), (16, 0, 32, 24))
    return {"image": image_to_data_url(image), "options": {"max_size": 32, "max_threads": 1},
            "result": {"width": 32, "height": 24, "background": [0, 0, 0, 255], "shapes": [], "attempts": 41}}


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_is_confirmed_native_prefix_then_continues_as_a_fresh_experiment(restore_server) -> None:
    server = restore_server
    payload = _payload()
    image = open_image_bytes(image_data_url_bytes(payload["image"]))
    options = RunOptions(steps=3, max_threads=1, shape_count=8, mutations=8, max_size=32,
                         shape_types=("rectangle",), seed=123)
    original = ImageSession.from_image(image, options)
    prefix_pixels = [original.result().image.tobytes()]
    for event in original.run_batch(options):
        if event["shapes"]:
            prefix_pixels.append(original.result().image.tobytes())
    assert len(original.shapes) == 3
    server.store_session("original", original)
    original_shapes = copy.deepcopy(original.shapes)
    payload["result"] = {"width": 32, "height": 24, "background": original.background,
                         "shapes": original_shapes, "attempts": original.attempts, "revision": 999}
    payload["shape_count"] = 1
    payload["options"]["max_size"] = 64
    confirmed = _post(server, "/api/restore", payload)
    session_id = confirmed["session_id"]
    assert confirmed["event"] == "restored"
    assert session_id != "original"
    assert confirmed["attempts"] == 0
    assert confirmed["shape_count"] == confirmed["revision"] == confirmed["restored_shape_count"] == 1
    assert confirmed["restore"] == {"shape_count": 1, "source_shape_count": 3,
                                    "source_attempts": original.attempts, "attempt_policy": "reset"}
    assert confirmed["batch_summary"] is None
    assert open_image_bytes(image_data_url_bytes(confirmed["preview"])).tobytes() == prefix_pixels[1]
    assert server.get_session(session_id).batch_count == 0
    assert server.get_session("original") is original
    assert original.shapes == original_shapes
    assert original.result().image.tobytes() == prefix_pixels[-1]
    assert server.active_runs == {}
    events = _post(
        server, "/api/run/stream",
        {"session_id": session_id, "options": {**payload["options"], "steps": 1, "shape_count": 8, "mutations": 8}},
        stream=True,
    )
    assert events[0]["event"] == "start"
    assert events[0]["attempts"] == 0
    assert events[0]["restored_shape_count"] == 1
    steps = [event for event in events if event["event"] == "step"]
    assert steps[0]["attempts"] == 1
    assert all(event["restored_shape_count"] == 1 for event in events if event["event"] != "error")
    assert events[-1]["revision"] == events[-1]["shape_count"] == 2
    assert original.shapes == original_shapes
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("count", [0, None])
def test_restore_accepts_empty_prefix_and_optional_full_prefix(restore_server, count) -> None:
    payload = _payload()
    if count is not None:
        payload["shape_count"] = count
    confirmed = _post(restore_server, "/api/restore", payload)
    assert confirmed["restored_shape_count"] == confirmed["attempts"] == confirmed["shape_count"] == 0
    assert confirmed["revision"] == 0
    assert confirmed["background"] == [0, 0, 0, 255]
    assert open_image_bytes(image_data_url_bytes(confirmed["preview"])).getpixel((0, 0)) == (0, 0, 0, 255)
    assert restore_server.get_session(confirmed["session_id"]) is not None
    assert restore_server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("count", [None, False, True, -1, 1, 0.5, "0", [], {}])
def test_restore_rejects_noninteger_or_out_of_range_prefix_without_publishing_session(restore_server, count) -> None:
    payload = _payload()
    payload["shape_count"] = count
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", payload)
    assert error.value.code == HTTPStatus.BAD_REQUEST
    assert json.load(error.value)["code"] == "invalid_result"
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


def test_restore_request_memory_is_admitted_before_reading_body(restore_server, monkeypatch) -> None:
    restore_server.work_budget = WorkBudget(1, 32 * 1024)
    monkeypatch.setattr(GeometrizeRequestHandler, "_read_json", lambda _self: pytest.fail("Body read before admission"))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", {})
    assert json.load(error.value)["code"] == "memory_limit"
    assert restore_server.work_budget.usage() == (0, 0)


def test_restore_scene_metadata_is_admitted_before_normalization(restore_server, monkeypatch) -> None:
    restore_server.work_budget = WorkBudget(1, 1024 * 1024)
    monkeypatch.setattr(web, "validate_scene", lambda _raw: pytest.fail("Copied geometry before admission"))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", _payload())
    assert json.load(error.value)["code"] == "memory_limit"
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_replay_scratch_is_reserved_before_image_decode_or_native_drawing(restore_server, monkeypatch) -> None:
    restore_server.work_budget = WorkBudget(1, 8 * 1024 * 1024)
    payload = _payload()
    payload["result"]["shapes"] = [{"type": "polyline", "color": [0, 0, 0, 128],
                                    "data": {"points": [[-512, -384], [512, 384]] * 500}}]
    monkeypatch.setattr(web, "open_image_bytes", lambda _raw: pytest.fail("Image decoded before scratch admission"))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", payload)
    assert json.load(error.value)["code"] == "memory_limit"
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("radius", [1000, pytest.param(10**400, id="overflowing_integer"), False])
def test_restore_rejects_hostile_geometry_before_drawing(restore_server, monkeypatch, radius) -> None:
    payload = _payload()
    payload["result"]["shapes"] = [{"type": "circle", "color": [0, 0, 0, 128],
                                    "data": {"x": 16, "y": 12, "r": radius}}]
    monkeypatch.setattr(ImageSession, "_from_validated_scene", lambda *_args: pytest.fail("Unsafe scene was replayed"))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", payload)
    assert json.load(error.value)["code"] == "invalid_result"
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_mismatched_source_does_not_change_existing_session_or_leak_resources(restore_server) -> None:
    original = ImageSession.from_image(Image.new("RGBA", (2, 2), "blue"), RunOptions())
    restore_server.store_session("original", original)
    payload = _payload()
    payload["result"]["height"] = 25
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", payload)
    assert json.load(error.value)["code"] == "invalid_result"
    assert restore_server.get_session("original") is original
    assert list(restore_server.sessions) == ["original"]
    assert original.result().image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert restore_server.work_budget.usage() == (0, 0)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_restore_dynamic_work_exhaustion_preserves_parent_and_releases_admission(restore_server) -> None:
    original = ImageSession.from_image(Image.new("RGBA", (2, 2), "blue"), RunOptions())
    restore_server.store_session("original", original)
    payload = _payload()
    payload["image"] = image_to_data_url(Image.new("RGBA", (32, 32), (0, 0, 0, 255)))
    payload["result"]["height"] = 32
    payload["result"]["shapes"] = [
        {"type": "rectangle", "color": [level, level, level, 255],
         "data": {"x1": 0, "y1": 0, "x2": 0, "y2": 0}} for level in [1, 0] * 32
    ]
    restore_server.restore_work_limit = 8 * 32 * 32 + 4 * 64 + 32 * 32
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", payload)
    failure = json.load(error.value)
    assert failure["code"] == "invalid_result"
    assert "work limit" in failure["error"]
    assert list(restore_server.sessions) == ["original"]
    assert restore_server.get_session("original") is original
    assert original.result().image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert restore_server.active_runs == {}
    assert restore_server.work_budget.usage() == (0, 0)
    # A subsequent request can use the same render slot and remains within the
    # ceiling, confirming the failed unpublished session leaves no reservation.
    payload["result"]["shapes"] = []
    assert _post(restore_server, "/api/restore", payload)["restored_shape_count"] == 0


@pytest.mark.parametrize("limit", [None, False, True, 0, -1, 1000000001, 0.5, "100"])
def test_server_restore_work_limit_rejects_invalid_configuration(limit) -> None:
    with pytest.raises(ValueError, match="restore_work_limit"):
        GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler, restore_work_limit=limit)


def test_server_publishes_its_lower_restore_work_limit() -> None:
    with GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler, restore_work_limit=100000) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/config", timeout=5) as response:
                assert json.load(response)["restore"]["max_work"] == 100000
        finally:
            server.shutdown()
            thread.join(5)


def test_restore_at_exhausted_render_capacity_is_busy(restore_server) -> None:
    assert restore_server.try_acquire_render_slot()
    assert restore_server.try_acquire_render_slot()
    try:
        with pytest.raises(HTTPError) as error:
            _post(restore_server, "/api/restore", _payload())
        assert error.value.code == HTTPStatus.TOO_MANY_REQUESTS
        assert json.load(error.value)["code"] == "renderer_busy"
        assert not restore_server.sessions
        assert restore_server.work_budget.usage() == (0, 0)
    finally:
        restore_server.release_render_slot()
        restore_server.release_render_slot()


def test_restore_missing_native_capability_is_actionable_503(restore_server, monkeypatch) -> None:
    monkeypatch.setattr(native, "_native", SimpleNamespace(is_available=lambda: True))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", _payload())
    assert error.value.code == HTTPStatus.SERVICE_UNAVAILABLE
    payload = json.load(error.value)
    assert payload["code"] == "native_unavailable"
    assert "rebuild or reinstall" in payload["error"]
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


def test_restore_rejects_unretainable_prefix_before_native_access(restore_server, monkeypatch) -> None:
    restore_server.max_session_bytes = 512 * 1024
    monkeypatch.setattr(web, "require_restore_native", lambda: pytest.fail("Unretainable session reached native"))
    with pytest.raises(HTTPError) as error:
        _post(restore_server, "/api/restore", _payload())
    assert json.load(error.value)["code"] == "memory_limit"
    assert not restore_server.sessions
    assert restore_server.work_budget.usage() == (0, 0)


def test_cached_session_budget_counts_retained_polyline_vertices(restore_server) -> None:
    short = SimpleNamespace(width=32, height=24, shapes=[{"type": "polyline", "data": {"points": [[0, 0]]}}])
    long = SimpleNamespace(width=32, height=24, shapes=[{"type": "polyline", "data": {"points": [[0, 0]] * 10000}}])
    assert _estimate_session_bytes(long) - _estimate_session_bytes(short) >= (10000 - 1) * 192
    restore_server.max_session_bytes = _estimate_session_bytes(short)
    restore_server.store_session("long", long)
    assert restore_server.get_session("long") is None
    assert restore_server._stored_session_bytes == 0


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("capacity", ["count", "bytes"])
def test_restore_rejects_full_active_cache_without_mutating_existing_sessions(restore_server, capacity) -> None:
    server = restore_server
    active = ImageSession.from_image(Image.new("RGBA", (32, 24), "blue"), RunOptions())
    server.activate_session("active", active, "running", options=RunOptions(steps=1, shape_types=("rectangle",)))
    if capacity == "count":
        server.max_sessions = 1
    else:
        idle = ImageSession.from_image(Image.new("RGBA", (2, 2), "red"), RunOptions())
        server.store_session("idle", idle)
        # The incoming empty scene fits by itself, and the current active+idle
        # cache fits. Active+incoming cannot fit even if idle is evicted.
        server.max_session_bytes = 2 * _estimate_session_bytes(active) - 1
    original_ids = list(server.sessions)
    original_bytes = server._stored_session_bytes
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/restore", _payload())
    assert error.value.code == HTTPStatus.TOO_MANY_REQUESTS
    assert json.load(error.value)["code"] == "session_capacity"
    assert list(server.sessions) == original_ids
    assert server._stored_session_bytes == original_bytes
    assert server.get_session("active") is active
    assert active.result().image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert server.active_runs == {"active": "running"}
    assert server.work_budget.usage() == (0, 0)
    # Capacity recovery must also recover the request/render reservations.
    server.max_sessions = 3
    server.max_session_bytes *= 2
    restored = _post(server, "/api/restore", _payload())
    assert server.get_session(restored["session_id"]) is not None


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("capacity", ["count", "bytes"])
def test_restore_retains_new_session_by_evicting_idle_victim_and_can_continue(restore_server, capacity) -> None:
    server = restore_server
    active = ImageSession.from_image(Image.new("RGBA", (32, 24), "blue"), RunOptions())
    idle = ImageSession.from_image(Image.new("RGBA", (32, 24), "red"), RunOptions())
    server.activate_session("active", active, "running", options=RunOptions(steps=1, shape_types=("rectangle",)))
    server.store_session("idle", idle)
    if capacity == "count":
        server.max_sessions = 2
    else:
        server.max_session_bytes = 2 * _estimate_session_bytes(active) + 8192
    restored = _post(server, "/api/restore", _payload())
    session_id = restored["session_id"]
    assert list(server.sessions) == ["active", session_id]
    assert server.get_session("active") is active
    assert server.get_session("idle") is None
    assert server.get_session(session_id) is not None
    assert server._stored_session_bytes <= server.max_session_bytes
    events = _post(
        server, "/api/run/stream",
        {"session_id": session_id, "options": {"steps": 1, "max_threads": 1, "shape_types": ["rectangle"],
                                              "shape_count": 8, "mutations": 8}},
        stream=True,
    )
    assert events[0]["event"] == "start"
    assert events[0]["session_id"] == session_id
    assert events[-1]["event"] == "complete"
    assert events[-1]["attempts"] >= 1
    assert not any(event["event"] == "error" for event in events)
    assert server.get_session(session_id) is not None
    assert server.active_runs == {"active": "running"}
    assert server.work_budget.usage() == (0, 0)


def test_retained_cache_insertion_rejects_oversized_replacement_atomically(restore_server) -> None:
    server = restore_server
    previous = SimpleNamespace(width=32, height=24, shapes=[])
    larger = SimpleNamespace(width=64, height=48, shapes=[])
    server.store_session("previous", previous)
    server.max_session_bytes = _estimate_session_bytes(previous)
    original_bytes = server._stored_session_bytes
    with pytest.raises(APIError, match="saved session cache"):
        server.store_session("previous", larger, require_retention=True)
    assert server.get_session("previous") is previous
    assert server._stored_session_bytes == original_bytes


class _ControlledRunner:
    initial_score = 0.5
    score = 0.5
    attempts = 0

    def __init__(self, *, fail: bool = False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = fail
        self.shape = {"type": "polyline", "color": [20, 40, 60, 128],
                      "data": {"points": [[0, 0], [1, 1], [2, 2], [3, 3]]}}

    def step(self, _options):
        self.entered.set()
        assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("Controlled optimizer failure")
        self.attempts += 1
        self.score *= 0.9
        return {"attempt": self.attempts, "shapes": [copy.deepcopy(self.shape)]}

    def current_rgba(self):
        return bytes((0, 0, 0, 255)) * 32 * 24


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("capacity", ["projected_growth_fits", "full_growth_does_not_fit"])
def test_restore_admission_accounts_for_future_active_growth_before_finish(restore_server, capacity) -> None:
    server = restore_server
    runner = _ControlledRunner()
    active = ImageSession(32, 24, (0, 0, 0, 255), runner)
    server.store_session("active", active)
    future = SimpleNamespace(width=32, height=24, shapes=[runner.shape] * 4)
    partial = SimpleNamespace(width=32, height=24, shapes=[runner.shape])
    incoming = SimpleNamespace(width=32, height=24, shapes=[])
    budget = _estimate_session_bytes(future if capacity == "projected_growth_fits" else partial)
    server.max_session_bytes = budget + _estimate_session_bytes(incoming)
    body = {"session_id": "active", "options": {"steps": 4, "shape_types": ["polyline"], "max_threads": 1}}
    try:
        with _response(server, "/api/run/stream", body) as response:
            start = json.loads(response.readline())
            assert runner.entered.wait(5)
            assert active.shapes == []
            assert server.sessions["active"].estimated_bytes == _estimate_session_bytes(future)
            usage = server.work_budget.usage()
            if capacity == "projected_growth_fits":
                restored = _post(server, "/api/restore", _payload())
            else:
                with pytest.raises(HTTPError) as error:
                    _post(server, "/api/restore", _payload())
                assert error.value.code == HTTPStatus.TOO_MANY_REQUESTS
                assert json.load(error.value)["code"] == "session_capacity"
                assert list(server.sessions) == ["active"]
                assert server.work_budget.usage() == usage
                _post(server, "/api/sessions/active/pause", {"run_id": start["run_id"]})
            runner.release.set()
            events = [json.loads(line) for line in response]
        assert events[-1]["event"] == ("complete" if capacity == "projected_growth_fits" else "paused")
        assert len(active.shapes) == (4 if capacity == "projected_growth_fits" else 1)
        assert not server.active_runs
        assert not server._active_session_bytes
        assert server.sessions["active"].estimated_bytes == _estimate_session_bytes(active)
        assert server._stored_session_bytes == sum(record.estimated_bytes for record in server.sessions.values())
        assert server._stored_session_bytes <= server.max_session_bytes
        if capacity == "full_growth_does_not_fit":
            # Pause released three unused shape reservations. The restore now
            # fits alongside the one accepted stroke, with the same byte limit.
            restored = _post(server, "/api/restore", _payload())
        assert server.get_session(restored["session_id"]) is not None
        continued = _post(server, "/api/run/stream", {"session_id": restored["session_id"], "options": {
            "steps": 1, "shape_types": ["rectangle"], "shape_count": 8, "mutations": 8, "max_threads": 1,
        }}, stream=True)
        assert continued[0]["continued"] is True
        assert continued[-1]["event"] == "complete"
        assert not any(event["event"] == "error" for event in continued)
        assert server.work_budget.usage() == (0, 0)
    finally:
        runner.release.set()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
@pytest.mark.parametrize("ending", ["error", "disconnect"])
def test_stream_releases_unused_cache_growth_after_error_or_disconnect(restore_server, monkeypatch, ending) -> None:
    server = restore_server
    runner = _ControlledRunner(fail=ending == "error")
    session = ImageSession(32, 24, (0, 0, 0, 255), runner)
    server.store_session("active", session)
    if ending == "disconnect":
        write_event = GeometrizeRequestHandler._write_stream_event

        def disconnect_on_step(handler, event):
            if event["event"] == "step":
                raise BrokenPipeError("Controlled closed stream")
            write_event(handler, event)

        monkeypatch.setattr(GeometrizeRequestHandler, "_write_stream_event", disconnect_on_step)
    try:
        with _response(server, "/api/run/stream", {"session_id": "active", "options": {
            "steps": 4, "shape_types": ["polyline"], "max_threads": 1,
        }}) as response:
            assert json.loads(response.readline())["event"] == "start"
            assert runner.entered.wait(5)
            assert server._stored_session_bytes > _estimate_session_bytes(session)
            runner.release.set()
            events = [json.loads(line) for line in response]
        if ending == "error":
            assert events[-1]["event"] == "error"
        else:
            assert events == []
        assert len(session.shapes) == (0 if ending == "error" else 1)
        assert server.get_session("active") is session
        assert not server.active_runs
        assert not server._active_session_bytes
        assert server._stored_session_bytes == _estimate_session_bytes(session)
        assert server.work_budget.usage() == (0, 0)
        assert session.try_acquire_run()
        session.release_run()
    finally:
        runner.release.set()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_failed_activation_rolls_back_cache_projection_and_releases_run(restore_server, monkeypatch) -> None:
    server = restore_server
    session = ImageSession(32, 24, (0, 0, 0, 255), _ControlledRunner())
    server.store_session("active", session)
    original_bytes = server._stored_session_bytes

    def reject_store(*_args, **_kwargs):
        assert "active" in server._active_session_bytes
        raise APIError("session_capacity", "Controlled activation failure")

    monkeypatch.setattr(server, "store_session", reject_store)
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run/stream", {"session_id": "active", "options": {
            "steps": 4, "shape_types": ["polyline"], "max_threads": 1,
        }})
    assert json.load(error.value)["code"] == "session_capacity"
    assert not server.active_runs
    assert not server._active_session_bytes
    assert server.get_session("active") is session
    assert server._stored_session_bytes == original_bytes
    assert server.work_budget.usage() == (0, 0)
    assert session.try_acquire_run()
    session.release_run()


def test_active_cache_projection_survives_refresh_and_is_released_at_finish(restore_server) -> None:
    server = restore_server
    shape = _ControlledRunner().shape
    session = SimpleNamespace(width=32, height=24, shapes=[])
    future = SimpleNamespace(width=32, height=24, shapes=[shape] * 4)
    server.activate_session("active", session, "run", options=RunOptions(steps=4, shape_types=("polyline",)))
    projected = _estimate_session_bytes(future)
    assert server._stored_session_bytes == projected
    session.shapes.append(shape)
    server.store_session("active", session)
    assert server._stored_session_bytes == projected
    server.finish_session("active", session)
    assert not server._active_session_bytes
    assert server._stored_session_bytes == _estimate_session_bytes(session) < projected


def test_projected_cache_growth_respects_global_caps_without_reducing_mixed_shape_count(monkeypatch) -> None:
    monkeypatch.setattr(web, "PROJECT_MAX_SHAPES", 3)
    monkeypatch.setattr(web, "PROJECT_MAX_TOTAL_POINTS", 8)
    current = {"type": "polyline", "data": {"points": [[0, 0]] * 7}}
    rectangle = {"type": "rectangle", "data": {}}
    final_point = {"type": "polyline", "data": {"points": [[0, 0]]}}
    session = SimpleNamespace(width=32, height=24, shapes=[current])
    future = SimpleNamespace(width=32, height=24, shapes=[current, rectangle, final_point])
    assert web._projected_session_bytes(session, RunOptions(steps=10, shape_types=("polyline", "rectangle"))) == (
        _estimate_session_bytes(future)
    )
    session.shapes = [current, rectangle, final_point]
    assert web._projected_session_bytes(session, RunOptions(steps=10, shape_types=("polyline",))) == (
        _estimate_session_bytes(session)
    )
