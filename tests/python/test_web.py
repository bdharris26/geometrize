from __future__ import annotations

import json
import socket
import threading
import urllib.request
from collections.abc import Iterator
from http import HTTPStatus
from types import SimpleNamespace
from typing import cast
from urllib.error import HTTPError

import pytest
from PIL import Image

from geometrize_py.images import image_to_data_url
from geometrize_py.native import ImageSession, native_available
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer, _estimate_session_bytes


@pytest.fixture
def server() -> Iterator[GeometrizeServer]:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_health_endpoint_reports_native_state(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload == {"ok": True, "native": native_available()}


def test_index_supports_sample_without_required_file_input(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
        html = response.read().decode("utf-8")
    assert 'id="sample-button"' in html
    assert 'id="image-input" type="file"' in html
    assert "image/svg+xml" not in html
    assert 'id="image-name"' in html
    assert 'id="steps" name="steps" type="range" min="1" max="4096" value="128"' in html
    assert "Shapes to add" in html
    assert "Candidates per shape" in html
    assert "Mutations per candidate" in html
    assert "Working resolution" in html
    assert "Export resolution" in html
    assert 'id="seed" name="seed" type="number" min="0" max="2147483647" value="9001"' in html
    assert 'id="max-size" name="max_size" type="range" min="64" max="2048" step="64" value="1024"' in html
    assert 'id="export-size" name="export_size" type="range" min="64" max="4096" step="64" value="1024"' in html
    assert 'id="load-project-button"' in html
    assert 'id="save-project-button"' in html
    assert 'id="max-size-number" type="number" min="64" max="2048" step="64" value="1024"' in html
    assert 'id="export-size-number" type="number" min="64" max="4096" step="64" value="1024"' in html
    assert 'id="pause-button"' in html
    assert 'class="telemetry-console"' in html
    assert 'id="result-canvas"' in html
    assert 'id="score-graph"' in html
    assert 'id="impact-graph"' in html
    assert 'id="telemetry-acceptance"' in html


def test_static_route_rejects_path_traversal(server: GeometrizeServer) -> None:
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/static/%2e%2e/web.py", timeout=5)
    assert error.value.code == 404


def test_run_endpoint_rejects_non_object_json(server: GeometrizeServer) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run",
        data=b"[]",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert payload == {"error": "Expected a JSON object"}


def test_session_cache_expires_only_after_idle_deadline(server: GeometrizeServer) -> None:
    clock = _FakeClock()
    server._clock = clock
    server.session_idle_seconds = 10
    session = _fake_session(32, 32)
    server.store_session("render", session)

    clock.advance(9)
    assert server.get_session("render") is session
    clock.advance(9)
    assert server.get_session("render") is session
    clock.advance(10)
    server.service_actions()
    assert server.get_session("render") is None
    assert not server.sessions
    assert server._stored_session_bytes == 0


def test_session_cache_evicts_least_recently_used_session(server: GeometrizeServer) -> None:
    server.max_sessions = 2
    first = _fake_session(16, 16)
    second = _fake_session(16, 16)
    third = _fake_session(16, 16)
    server.store_session("first", first)
    server.store_session("second", second)

    assert server.get_session("first") is first
    server.store_session("third", third)

    assert server.get_session("second") is None
    assert server.get_session("first") is first
    assert server.get_session("third") is third


def test_session_cache_enforces_approximate_memory_budget(server: GeometrizeServer) -> None:
    small = _fake_session(16, 16)
    large = _fake_session(64, 64)
    oversized = _fake_session(128, 128)
    server.max_session_bytes = _estimate_session_bytes(large)

    server.store_session("small", small)
    server.store_session("large", large)

    assert server.get_session("small") is None
    assert server.get_session("large") is large
    assert server._stored_session_bytes == _estimate_session_bytes(large)

    server.store_session("oversized", oversized)
    assert server.get_session("large") is None
    assert server.get_session("oversized") is None
    assert server._stored_session_bytes == 0


def test_render_concurrency_slots_are_bounded(server: GeometrizeServer) -> None:
    assert server.try_acquire_render_slot()
    assert server.try_acquire_render_slot()
    assert not server.try_acquire_render_slot()

    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run/stream",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == HTTPStatus.TOO_MANY_REQUESTS
        assert json.loads(error.value.read().decode("utf-8"))["error"].startswith("The renderer is busy")
    finally:
        server.release_render_slot()
        server.release_render_slot()

    assert server.try_acquire_render_slot()
    server.release_render_slot()


def test_partial_request_body_times_out_without_acquiring_render_slot() -> None:
    class ObservedReadHandler(GeometrizeRequestHandler):
        read_started = threading.Event()

        def _read_json(self) -> dict[str, object]:
            self.read_started.set()
            return super()._read_json()

    server = GeometrizeServer(
        ("127.0.0.1", 0),
        ObservedReadHandler,
        max_active_renders=1,
        request_timeout_seconds=0.2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = socket.create_connection(server.server_address, timeout=5)
    slot_acquired = False
    try:
        client.sendall(
            b"POST /api/run HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"{"
        )
        assert ObservedReadHandler.read_started.wait(timeout=5)
        slot_acquired = server.try_acquire_render_slot()
        assert slot_acquired
        server.release_render_slot()
        slot_acquired = False
        response = _read_socket_response(client)
        assert b"408 Request Timeout" in response
        assert b"Request body timed out" in response
    finally:
        if slot_acquired:
            server.release_render_slot()
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_rejects_concurrent_use_of_same_session(server: GeometrizeServer) -> None:
    native_state = SimpleNamespace(attempts=0)
    session = ImageSession(1, 1, (0, 0, 0, 255), native_state)
    assert session.try_acquire_run()
    server.store_session("busy", session)
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run/stream",
        data=json.dumps(
            {
                "session_id": "busy",
                "options": {"steps": 1, "shape_types": ["rectangle"]},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == HTTPStatus.CONFLICT
        assert json.loads(error.value.read().decode("utf-8")) == {
            "error": "This render session is already active."
        }
    finally:
        session.release_run()

    assert server.try_acquire_render_slot()
    server.release_render_slot()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_run_endpoint_returns_rendered_image(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (6, 6), (50, 100, 180, 255))
    body = json.dumps(
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 1,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 6,
                "export_size": 64,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["width"] == 6
    assert payload["height"] == 6
    assert payload["export_width"] == 64
    assert payload["export_height"] == 64
    assert payload["preview"].startswith("data:image/png;base64,")
    assert payload["svg"].startswith('<?xml version="1.0"')
    assert 'width="64" height="64" viewBox="0 0 6 6"' in payload["svg"]
    assert payload["shape_count"] == len(payload["shapes"])
    assert payload["attempts"] >= 1
    if payload["shapes"]:
        assert "score" in payload["shapes"][0]


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_endpoint_returns_progress_events(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (6, 6), (80, 120, 40, 255))
    events = _post_stream(
        server,
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 2,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 6,
                "export_size": 64,
            },
        },
    )
    assert events[0]["event"] == "start"
    assert events[0]["session_id"]
    assert events[0]["continued"] is False
    assert events[-1]["event"] == "complete"
    assert any(event["event"] == "step" for event in events)
    assert events[0]["width"] == 6
    assert events[-1]["attempts"] >= 2
    assert events[-1]["export_width"] == 64
    assert events[-1]["preview"].startswith("data:image/png;base64,")


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_endpoint_can_continue_session_with_new_shape_options(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (12, 12), (30, 40, 50, 255))
    for x in range(6, 12):
        for y in range(12):
            image.putpixel((x, y), (220, 180, 80, 255))

    first_events = _post_stream(
        server,
        {
            "image": image_to_data_url(image),
            "options": {"steps": 1, "shape_types": ["rectangle"], "shape_count": 8, "mutations": 8},
        },
    )
    session_id = first_events[0]["session_id"]
    first_count = first_events[-1]["shape_count"]

    second_events = _post_stream(
        server,
        {
            "session_id": session_id,
            "options": {"steps": 1, "shape_types": ["triangle"], "shape_count": 8, "mutations": 8},
        },
    )

    assert second_events[0]["continued"] is True
    assert second_events[0]["shape_count"] == first_count
    assert second_events[-1]["shape_count"] >= first_count
    assert second_events[-1]["session_id"] == session_id


def _post_stream(server: GeometrizeServer, payload: dict) -> list[dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return [json.loads(line) for line in response.read().decode("utf-8").splitlines()]


def _read_socket_response(client: socket.socket) -> bytes:
    response = bytearray()
    while True:
        chunk = client.recv(4096)
        if not chunk:
            return bytes(response)
        response.extend(chunk)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _fake_session(width: int, height: int) -> ImageSession:
    return cast(ImageSession, SimpleNamespace(width=width, height=height, shapes=[]))
