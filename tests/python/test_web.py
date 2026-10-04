from __future__ import annotations

import base64
import io
import json
import socket
import threading
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.client import HTTPResponse
from types import SimpleNamespace
from typing import cast
from urllib.error import HTTPError

import pytest
from PIL import Image, ImageDraw

from geometrize_py.contracts import MAX_REQUEST_BYTES
from geometrize_py.images import image_to_data_url
from geometrize_py.json_memory import JSON_EXPANSION_FACTOR
from geometrize_py.native import Focus, ImageSession, RunOptions, native_available
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer, _estimate_session_bytes, _safe_static_path


class _ObservedServer(GeometrizeServer):
    post_completions: dict[str, threading.Event]


class _ObservedHandler(GeometrizeRequestHandler):
    def do_POST(self) -> None:
        try:
            super().do_POST()
        finally:
            request_id = self.headers.get("X-Test-Request-ID")
            if request_id:
                completion = cast(_ObservedServer, self.server).post_completions.get(request_id)
                if completion is not None:
                    completion.set()


@pytest.fixture
def server() -> Iterator[GeometrizeServer]:
    server = _ObservedServer(("127.0.0.1", 0), _ObservedHandler)
    server.post_completions = {}
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
    assert 'id="max-size" name="max_size" type="range" min="32" max="8192" step="1" value="1024"' in html
    assert 'id="export-size" name="export_size" type="range" min="32" max="8192" step="1" value="1024"' in html
    assert 'id="load-project-button"' in html
    assert 'id="save-project-button"' in html
    assert 'id="max-size-number" type="number" min="32" max="8192" step="1" value="1024"' in html
    assert 'id="export-size-number" type="number" min="32" max="8192" step="1" value="1024"' in html
    assert 'id="pause-button"' in html
    assert 'class="telemetry-console"' in html
    assert 'id="result-canvas"' in html
    assert 'id="score-graph"' in html
    assert 'id="impact-graph"' in html
    assert 'id="telemetry-acceptance"' in html


@pytest.mark.parametrize("path", ["%2e%2e/web.py", "..%5cweb.py", "..%255cweb.py", "C:app.js"])
def test_static_route_rejects_path_traversal(server: GeometrizeServer, path: str) -> None:
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/static/{path}", timeout=5)
    assert error.value.code == 404


@pytest.mark.parametrize("name", ["nested\\app.js", "C:app.js", "C:/app.js", "C:\\app.js"])
def test_static_paths_reject_windows_syntax_on_every_platform(name: str) -> None:
    assert _safe_static_path(name) is None


def test_static_route_serves_browser_module(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/static/preview.js", timeout=5) as response:
        assert response.headers.get_content_type() in {"text/javascript", "application/javascript"}
        assert "export " in response.read().decode("utf-8")


def test_run_endpoint_rejects_non_object_json(server: GeometrizeServer) -> None:
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run", body=b"[]")
    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert payload == {"error": "Expected a JSON object", "code": "invalid_request"}
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("path", ["/api/run", "/api/run/stream", "/api/restore"])
@pytest.mark.parametrize("options", [{"steps": 0}, {"max_size": 6}, {"alpha": True}, {"seed": "42"},
                                     {"shape_count": 1.5}, {"unexpected": 1}, []])
def test_invalid_options_are_rejected_before_image_or_native_allocation(
    server: GeometrizeServer, monkeypatch, path: str, options,
) -> None:
    from geometrize_py import web

    monkeypatch.setattr(web, "image_data_url_bytes", lambda *_args: pytest.fail("invalid options decoded an image"))
    monkeypatch.setattr(web, "require_restore_native", lambda: pytest.fail("invalid options allocated native replay"))
    monkeypatch.setattr(ImageSession, "from_image", lambda *_args: pytest.fail("invalid options allocated fitting"))
    with pytest.raises(HTTPError) as error:
        _post(server, path, {"image": "data:image/png;base64,YWJj", "options": options, "result": {}})
    assert error.value.code == HTTPStatus.BAD_REQUEST
    assert json.load(error.value)["code"] == "invalid_options" and server.work_budget.usage() == (0, 0)


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


@pytest.mark.parametrize(
    ("active_mb", "explicit_cache_mb", "retained"),
    [(512, None, False), (8192, None, True), (8192, 512, False)],
)
def test_large_session_cache_follows_explicit_active_budget(
    active_mb: int, explicit_cache_mb: int | None, retained: bool
) -> None:
    cache = None if explicit_cache_mb is None else explicit_cache_mb * 1024 * 1024
    with GeometrizeServer(
        ("127.0.0.1", 0), GeometrizeRequestHandler,
        active_memory_bytes=active_mb * 1024 * 1024, max_session_bytes=cache,
    ) as server:
        session = _fake_session(8192, 8192)
        server.store_session("large", session)
        assert (server.get_session("large") is session) is retained


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
        assert server.work_budget.usage() == (0, 0)
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
            "error": "This render session is already active.",
            "code": "session_busy",
        }
    finally:
        session.release_run()

    assert server.try_acquire_render_slot()
    server.release_render_slot()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_run_endpoint_returns_rendered_image(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (6, 6), (50, 100, 180, 255))
    ImageDraw.Draw(image).rectangle((3, 0, 5, 5), fill=(230, 180, 50, 255))
    body = json.dumps(
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 1,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 32,
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
    ImageDraw.Draw(image).rectangle((3, 0, 5, 5), fill=(230, 180, 50, 255))
    events = _post_stream(
        server,
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 2,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 32,
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
    assert events[-1]["stop_reason"] == "target_reached"
    assert events[-1]["batch_summary"]["added"] == 2
    assert "svg" not in events[-1]  # Stream completion does not generate exports.
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
    with _tracked_post(server) as request_id:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/run/stream",
            data=body,
            headers={"Content-Type": "application/json", "X-Test-Request-ID": request_id},
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


def test_config_exposes_shared_contract_and_resource_budget(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/config") as response:
        contract = json.load(response)
    assert len(contract["shapes"]) == 9
    assert contract["limits"]["max_size"] == {"min": 32, "max": 8192}
    assert contract["limits"]["export_size"] == {"min": 32, "max": 8192}
    assert "image/tiff" not in contract["images"]["mime_types"]
    assert contract["resources"]["worker_budget"] == server.work_budget.workers


@pytest.mark.parametrize("body", [b'{"options":{"steps":Infinity}}', b'{"options":{"steps":NaN}}'])
def test_nonfinite_json_is_a_structured_client_error(server: GeometrizeServer, body: bytes) -> None:
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run/stream", body=body)
    assert error.value.code == 400
    assert json.load(error.value)["code"] == "invalid_request"
    assert server.work_budget.usage() == (0, 0)


def test_request_admission_happens_before_body_read(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 32 * 1024)
    read_started = threading.Event()
    original = GeometrizeRequestHandler._read_json

    def observed_read(self):
        read_started.set()
        return original(self)

    monkeypatch.setattr(GeometrizeRequestHandler, "_read_json", observed_read)
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run", body=b"{}")
    assert json.load(error.value)["code"] == "memory_limit"
    assert not read_started.is_set()
    assert server.work_budget.usage() == (0, 0)


@pytest.fixture
def rejection_probe() -> Iterator[SimpleNamespace]:
    from geometrize_py.resources import WorkBudget

    probe = SimpleNamespace(
        response_sent=threading.Event(),
        finish_response=threading.Event(),
        connection_closed=threading.Event(),
        parsed_body=threading.Event(),
        read_sizes=[],
        bytes_read=0,
        read_before_response=False,
        eof=False,
        timed_out=False,
    )

    class ObservedInput:
        def __init__(self, source):
            self.source = source

        def read1(self, size):
            probe.read_before_response |= not probe.response_sent.is_set()
            probe.read_sizes.append(size)
            try:
                chunk = self.source.read1(size)
            except TimeoutError:
                probe.timed_out = True
                raise
            probe.bytes_read += len(chunk)
            probe.eof |= not chunk
            return chunk

        def __getattr__(self, name):
            return getattr(self.source, name)

    class DelayedCloseServer(GeometrizeServer):
        def shutdown_request(self, request):
            try:
                super().shutdown_request(request)
            finally:
                probe.connection_closed.set()

    class DelayedCloseHandler(GeometrizeRequestHandler):
        def setup(self):
            super().setup()
            self.rfile = ObservedInput(self.rfile)

        def _send_json(self, payload, status=HTTPStatus.OK, **kwargs):
            super()._send_json(payload, status, **kwargs)
            probe.response_sent.set()
            assert probe.finish_response.wait(5)

        def _read_json(self):
            probe.parsed_body.set()
            return super()._read_json()

    server = DelayedCloseServer(("127.0.0.1", 0), DelayedCloseHandler)
    server.work_budget = WorkBudget(1, 32 * 1024)
    probe.server = server
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield probe
        assert not probe.parsed_body.is_set()
        assert not probe.read_before_response
        assert all(0 < size <= 4 * 1024 for size in probe.read_sizes)
        assert server.work_budget.usage() == (0, 0)
    finally:
        probe.finish_response.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def _send_rejected_headers(client: socket.socket, length: int | str, body: bytes = b"") -> None:
    headers = (
        "POST /api/run HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        f"Content-Length: {length}\r\nConnection: close\r\n\r\n"
    )
    client.sendall(headers.encode("ascii") + body)


def _assert_rejection_response(
    client: socket.socket, status: HTTPStatus = HTTPStatus.BAD_REQUEST, code: str = "memory_limit"
) -> None:
    with HTTPResponse(client) as response:
        response.begin()
        assert response.status == status
        assert response.getheader("Connection") == "close"
        assert json.load(response)["code"] == code


@pytest.mark.parametrize("delivery", ["already_sent", "late", "eof"])
def test_early_rejection_delivers_response_after_close(rejection_probe, delivery: str) -> None:
    probe = rejection_probe
    with socket.create_connection(probe.server.server_address, timeout=5) as client:
        _send_rejected_headers(client, 2, b"{}" if delivery == "already_sent" else b"")
        assert probe.response_sent.wait(5)
        if delivery == "late":
            client.sendall(b"{}")
        elif delivery == "eof":
            client.shutdown(socket.SHUT_WR)
        probe.finish_response.set()
        assert probe.connection_closed.wait(5)
        _assert_rejection_response(client)
        assert probe.bytes_read == (0 if delivery == "eof" else 2)
        assert probe.eof == (delivery == "eof")


@pytest.mark.parametrize("delivery", ["silent", "slow"])
def test_early_rejection_cleanup_has_an_absolute_deadline(rejection_probe, delivery: str) -> None:
    probe = rejection_probe
    if delivery == "silent":
        probe.server.request_timeout_seconds = 0.1
    stopped = threading.Event()
    with socket.create_connection(probe.server.server_address, timeout=5) as client:
        _send_rejected_headers(client, 64 * 1024)
        assert probe.response_sent.wait(5)
        # The complete error arrives even when no request body has arrived yet.
        _assert_rejection_response(client)

        def trickle_body():
            while not stopped.is_set():
                try:
                    client.sendall(b"x")
                except OSError:
                    return  # A closed server connection ends this test sender.
                stopped.wait(0.02)

        sender = threading.Thread(target=trickle_body, daemon=True)
        try:
            if delivery == "slow":
                sender.start()
            probe.finish_response.set()
            assert probe.connection_closed.wait(1), "Body trickling must not extend the close deadline"
            if delivery == "slow":
                assert probe.bytes_read > 1
            else:
                assert probe.bytes_read == 0
                assert probe.timed_out
        finally:
            stopped.set()
            if sender.ident is not None:
                sender.join(5)


def test_early_rejection_cleanup_stops_at_byte_limit(rejection_probe, monkeypatch) -> None:
    from geometrize_py import web

    probe = rejection_probe
    monkeypatch.setattr(web, "REJECTED_BODY_DRAIN_SECONDS", 3.0)
    with socket.create_connection(probe.server.server_address, timeout=5) as client:
        _send_rejected_headers(client, 64 * 1024 + 1, b"x" * (64 * 1024))
        assert probe.response_sent.wait(5)
        probe.finish_response.set()
        assert probe.connection_closed.wait(1), "Cleanup must stop without awaiting the remaining byte"
        _assert_rejection_response(client)
        assert probe.bytes_read == 64 * 1024


@pytest.mark.parametrize("body", [b"", b"ignored"])
@pytest.mark.parametrize(
    ("length", "status", "code"),
    [
        ("invalid", HTTPStatus.BAD_REQUEST, "invalid_request"),
        ("-1", HTTPStatus.BAD_REQUEST, "invalid_request"),
        (str(MAX_REQUEST_BYTES + 1), HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large"),
    ],
)
def test_early_rejection_with_untrusted_length_has_bounded_cleanup(
    rejection_probe, length: str, status: HTTPStatus, code: str, body: bytes
) -> None:
    probe = rejection_probe
    probe.server.request_timeout_seconds = 0.1
    with socket.create_connection(probe.server.server_address, timeout=5) as client:
        _send_rejected_headers(client, length, body)
        assert probe.response_sent.wait(5)
        probe.finish_response.set()
        assert probe.connection_closed.wait(1)
        _assert_rejection_response(client, status, code)
        assert probe.bytes_read == len(body)
        assert probe.timed_out


def test_json_expansion_is_admitted_before_decode(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 1024 * 1024)
    body = b'{"unused":"' + b"a" * 70_000 + b'"}'
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run", body=body)
    assert json.load(error.value)["code"] == "memory_limit"
    assert server.work_budget.usage() == (0, 0)


def test_base64_image_uses_bounded_string_allowance(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 1024 * 1024)
    body = b'{"image":"data:image/png;base64,' + b"a" * 70_000 + b'","options":{}}'
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run", body=body)
    assert json.load(error.value)["code"] == "invalid_image"
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("header", ["data:image/bmp;base64,", "DATA:IMAGE/BMP;BASE64,"])
@pytest.mark.parametrize("separator", ["", "\r\n"])
def test_wrapped_image_prepares_under_string_budget_with_original_bytes(
    server: GeometrizeServer, monkeypatch, header: str, separator: str,
) -> None:
    from geometrize_py import web
    from geometrize_py.resources import WorkBudget

    output = io.BytesIO()
    Image.new("RGB", (128, 128), (20, 40, 60)).save(output, format="BMP")
    original = output.getvalue()
    encoded = base64.b64encode(original).decode("ascii")
    encoded = separator.join(encoded[i:i + 17] for i in range(0, len(encoded), 17))
    body = json.dumps({"image": header + encoded}).encode()
    server.work_budget = WorkBudget(1, 1536 * 1024)
    assert len(body) * JSON_EXPANSION_FACTOR > server.work_budget.memory_bytes
    decoded = []
    actual_open = web.open_image_bytes

    def open_image(raw, *args, **kwargs):
        decoded.append(raw)
        return actual_open(raw, *args, **kwargs)

    monkeypatch.setattr(web, "open_image_bytes", open_image)
    result = _post(server, "/api/source/prepare", body=body)
    assert (result["width"], result["height"], result["mime_type"]) == (128, 128, "image/bmp")
    with Image.open(io.BytesIO(base64.b64decode(result["preview_data_url"].split(",", 1)[1]))) as preview:
        assert preview.getpixel((0, 0)) == (20, 40, 60, 255)
    assert decoded == [original]
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("kind", ["unused", "other-field", "escaped-quote", "containers", "nested", "export"])
def test_image_allowance_keeps_other_json_admitted_before_parsing(
    server: GeometrizeServer, monkeypatch, kind: str,
) -> None:
    from geometrize_py import web
    from geometrize_py.resources import WorkBudget

    path = "/api/source/prepare"
    if kind == "unused":
        payload = {"unused": "data:image/png;base64," + "a" * 70_000}
    elif kind == "other-field":
        payload = {"image": "data:image/png;base64,YWJj\r\n", "unused": "a" * 70_000}
    elif kind == "escaped-quote":
        payload = {"image": "data:image/png;base64," + "a" * 20_000 + '"' + "a" * 60_000}
    elif kind in {"containers", "nested"}:
        payload = {"unused": [[0]] * 30_000}
    else:
        path = "/api/export"
        payload = {"image": "data:image/png;base64," + "a" * 70_000}
    body_text = ('{"unused":' + "[" * 2000 + "0" + "]" * 2000 + "}"
                 if kind == "nested" else json.dumps(payload))
    body = body_text.encode()
    decoded = []
    actual_loads = json.loads

    def loads(value, *args, **kwargs):
        if value == body_text:
            decoded.append(value)
        return actual_loads(value, *args, **kwargs)

    monkeypatch.setattr(web.json, "loads", loads)
    server.work_budget = WorkBudget(1, 96 * 1024 if kind == "nested" else 1024 * 1024)
    with pytest.raises(HTTPError) as error:
        _post(server, path, body=body)
    assert json.load(error.value)["code"] == "memory_limit"
    assert not decoded and server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("kind", [
    "astral-escape", "bmp-escape", "ascii-escape", "raw-outside", "raw-header",
    "raw-payload", "escaped-quote", "double-escape", "mixed",
])
def test_image_strings_that_can_widen_require_generic_admission_before_parsing(
    server: GeometrizeServer, monkeypatch, kind: str,
) -> None:
    from geometrize_py import web
    from geometrize_py.resources import WorkBudget

    image = "data:image/png;base64," + "a" * 70_000
    payload = {"image": image}
    if kind == "bmp-escape":
        payload["image"] += "\u0100"
    elif kind == "ascii-escape":
        payload["image"] += "A"
    elif kind == "raw-outside":
        payload["unused"] = "\U0001f600"
    elif kind == "raw-header":
        payload["image"] = image.replace("image/png", "image/png;name=\U0001f600")
    elif kind == "escaped-quote":
        payload["image"] += '"\U0001f600'
    elif kind == "double-escape":
        payload["image"] += r"\u0061"
    elif kind == "mixed":
        payload["image"] += "\r\n\U0001f600"
    else:
        payload["image"] += "\U0001f600"
    body_text = json.dumps(payload, ensure_ascii=not kind.startswith("raw-"))
    if kind == "ascii-escape":
        body_text = body_text.replace('A"}', r'\u0041"}')
    body = body_text.encode()
    decoded = []
    actual_loads = json.loads

    def loads(value, *args, **kwargs):
        if value == body_text:
            decoded.append(value)
        return actual_loads(value, *args, **kwargs)

    monkeypatch.setattr(web.json, "loads", loads)
    server.work_budget = WorkBudget(1, 5 * len(body))
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/source/prepare", body=body)
    assert json.load(error.value)["code"] == "memory_limit"
    assert not decoded and server.work_budget.usage() == (0, 0)


def test_unicode_source_header_can_prepare_when_generic_memory_is_available(server: GeometrizeServer) -> None:
    image = image_to_data_url(Image.new("RGB", (2, 2), (20, 40, 60)))
    image = image.replace("image/png", "image/png;name=\U0001f600")
    result = _post(server, "/api/source/prepare", body=json.dumps({"image": image}, ensure_ascii=False).encode())
    assert (result["width"], result["height"], result["mime_type"]) == (2, 2, "image/png")
    assert server.work_budget.usage() == (0, 0)


def test_estimated_image_string_still_requires_valid_json_escapes(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py import web
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 1024 * 1024)
    body = b'{"image":"data:image/png;base64,' + b"a" * 70_000 + b'\\q"}'
    monkeypatch.setattr(web, "image_data_url_bytes", lambda *_args: pytest.fail("Invalid JSON decoded image bytes"))
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/source/prepare", body=body)
    assert json.load(error.value)["code"] == "invalid_request"
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize("invalid", ["\t", "\f", "\\", "!"])
def test_estimated_wrapped_image_still_requires_strict_base64(
    server: GeometrizeServer, monkeypatch, invalid: str,
) -> None:
    from geometrize_py import web

    monkeypatch.setattr(web, "probe_source", lambda *_args: pytest.fail("Invalid base64 reached the container probe"))
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/source/prepare", {"image": "data:image/png;base64,YW\r\n" + invalid + "Jj"})
    assert json.load(error.value)["code"] == "invalid_image"
    assert server.work_budget.usage() == (0, 0)


def test_wrapped_image_does_not_bypass_the_request_byte_cap(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py import web

    body = json.dumps({"image": "data:image/png;base64," + "YWJj\r\n" * 100}).encode()
    monkeypatch.setattr(web, "MAX_REQUEST_BYTES", len(body) - 1)
    monkeypatch.setattr(_ObservedHandler, "_read_json", lambda _self: pytest.fail("Oversized image body was read"))
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/source/prepare", body=body)
    assert error.value.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert json.load(error.value)["code"] == "request_too_large"
    assert server.work_budget.usage() == (0, 0)


def test_malformed_json_releases_request_memory(server: GeometrizeServer) -> None:
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", body=b'{"result":[1,2,}')
    assert json.load(error.value)["code"] == "invalid_request"
    assert server.work_budget.usage() == (0, 0)


def test_invalid_image_is_a_client_error_and_releases_resources(server: GeometrizeServer) -> None:
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/run/stream", {"image": "data:image/png;base64,bm90IGFuIGltYWdl"})
    assert error.value.code == 400
    assert json.load(error.value)["code"] == "invalid_image"
    assert server.work_budget.usage() == (0, 0)


def test_frozen_result_exports_at_new_size_without_fitting(server: GeometrizeServer, monkeypatch) -> None:
    def unexpected_fit(*args, **kwargs):
        raise AssertionError("Export must not create a native fitting session")

    monkeypatch.setattr(ImageSession, "from_image", unexpected_fit)
    scene = {"width": 12, "height": 8, "background": [30, 50, 70, 255], "shapes": []}
    first = _post(server, "/api/export", {"result": scene, "export_size": 64})
    second = _post(server, "/api/export", {"result": scene, "export_size": 128})
    assert (first["export_width"], first["export_height"]) == (64, 43)
    assert (second["export_width"], second["export_height"]) == (128, 85)
    assert _png_size(second["preview"]) == (128, 85)
    assert not server.sessions
    assert server.work_budget.usage() == (0, 0)


def test_export_at_8192_preserves_memory_admission(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    scene = {"width": 32, "height": 32, "background": [30, 50, 70, 255], "shapes": []}
    payload = {"result": scene, "export_size": 8192}
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", payload)
    assert json.load(error.value)["code"] == "memory_limit"
    assert server.work_budget.usage() == (0, 0)
    server.work_budget = WorkBudget(1, 2 * 1024 * 1024 * 1024)
    exported = _post(server, "/api/export", payload)
    assert _png_size(exported["preview"]) == (8192, 8192)
    assert (exported["export_width"], exported["export_height"]) == (8192, 8192)
    assert server.work_budget.usage() == (0, 0)
    payload["export_size"] = 8193
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", payload)
    assert json.load(error.value)["code"] == "invalid_options"
    assert server.work_budget.usage() == (0, 0)


def test_export_cache_avoids_repeated_rasterization(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py import web

    original = web.build_export
    calls = []

    def counted(scene, longest):
        calls.append(longest)
        return original(scene, longest)

    monkeypatch.setattr(web, "build_export", counted)
    payload = {"result": {"width": 4, "height": 4, "background": [20, 40, 60, 255], "shapes": []}, "export_size": 64}
    first = _post(server, "/api/export", payload)
    again = _post(server, "/api/export", payload)
    payload["result"]["background"] = [60, 40, 20, 255]
    changed = _post(server, "/api/export", payload)
    assert first["preview"] == again["preview"] != changed["preview"]
    assert calls == [64, 64]


def test_export_memory_budget_applies_and_recovers(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(2, 5 * 1024 * 1024)
    payload = {"result": {"width": 4, "height": 4, "background": [20, 40, 60, 255], "shapes": []}, "export_size": 4096}
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", payload)
    assert json.load(error.value)["code"] == "memory_limit"
    assert server.work_budget.usage() == (0, 0)
    payload["export_size"] = 32
    assert _post(server, "/api/export", payload)["export_width"] == 32
    assert server.work_budget.usage() == (0, 0)


def test_frozen_scene_is_budgeted_before_normalization(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py import web
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(2, 1024 * 1024)

    def unexpected_validation(_raw):
        raise AssertionError("Scene normalization should not run before its memory is admitted")

    monkeypatch.setattr(web, "validate_scene", unexpected_validation)
    scene = {"width": 4, "height": 4, "background": [0, 0, 0, 255], "shapes": []}
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", {"result": scene, "export_size": 32})
    assert json.load(error.value)["code"] == "memory_limit"
    assert server.work_budget.usage() == (0, 0)


def test_total_polyline_point_cap_precedes_normalization(server: GeometrizeServer, monkeypatch) -> None:
    from geometrize_py import exporting, web

    monkeypatch.setattr(exporting, "PROJECT_MAX_TOTAL_POINTS", 3)

    def unexpected_validation(_raw):
        raise AssertionError("Point cap should be checked before normalization")

    monkeypatch.setattr(web, "validate_scene", unexpected_validation)
    scene = {
        "width": 4,
        "height": 4,
        "background": [0, 0, 0, 255],
        "shapes": [{"type": "polyline", "data": {"points": [[0, 0], [1, 1], [2, 2], [3, 3]]}}],
    }
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/export", {"result": scene, "export_size": 32})
    assert json.load(error.value)["code"] == "invalid_result"
    assert server.work_budget.usage() == (0, 0)


def test_pause_can_cancel_when_workers_and_memory_are_exhausted(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 1024 * 1024)
    fitting_lease = server.work_budget.try_reserve(1, server.work_budget.memory_bytes)
    assert fitting_lease is not None
    session = ImageSession(1, 1, (0, 0, 0, 255), SimpleNamespace(attempts=0))
    server.activate_session("controlled", session, "active-run", options=RunOptions(steps=1))
    try:
        ack = _post(server, "/api/sessions/controlled/pause", {"run_id": "active-run"})
        assert ack["event"] == "pause_requested"
        assert session._cancel_requested.is_set()
        assert server.work_budget.usage() == (1, 1024 * 1024)

        with pytest.raises(HTTPError) as error:
            _post(server, "/api/sessions/controlled/pause", {"padding": "x" * 4096})
        assert error.value.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    finally:
        server.finish_session("controlled", session)
        server.work_budget.release(fitting_lease)
    assert server.work_budget.usage() == (0, 0)


def test_focus_control_remains_available_at_exhausted_capacity(server: GeometrizeServer) -> None:
    from geometrize_py.resources import WorkBudget

    server.work_budget = WorkBudget(1, 1024 * 1024)
    fitting_lease = server.work_budget.try_reserve(1, server.work_budget.memory_bytes)
    assert fitting_lease is not None
    assert server.try_acquire_render_slot()
    assert server.try_acquire_render_slot()
    session = ImageSession(1, 1, (0, 0, 0, 255), SimpleNamespace(attempts=0))
    assert session.try_acquire_run()
    server.activate_session("controlled", session, "active-run", options=RunOptions(steps=1))
    try:
        ack = _post(server, "/api/sessions/controlled/focus", {"run_id": "active-run", "focus": {"x": 0, "y": 1}})
        expected = Focus(0, 1).to_dict()
        assert ack == {"focus": expected, "applies": "next_attempt"}
        assert session.focus.to_dict() == expected
        assert session.attempts == session.revision == len(session.shapes) == 0
        assert not session._cancel_requested.is_set()
        assert server.work_budget.usage() == (1, 1024 * 1024)

        ack = _post(server, "/api/sessions/controlled/focus", {"run_id": "active-run", "focus": None})
        assert ack == {"focus": None, "applies": "next_attempt"}
        assert session.focus is None
        with pytest.raises(HTTPError) as error:
            _post(server, "/api/sessions/controlled/focus", {"run_id": "active-run", "focus": None,
                                                           "padding": "x" * 4096})
        assert error.value.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    finally:
        session.release_run()
        server.finish_session("controlled", session)
        server.work_budget.release(fitting_lease)
        server.release_render_slot()
        server.release_render_slot()
    assert server.work_budget.usage() == (0, 0)


@pytest.mark.parametrize(
    "payload",
    [{"run_id": "active-run"}, {"run_id": "active-run", "focus": None, "shapes": []},
     {"run_id": "active-run", "focus": False}, {"run_id": "active-run", "focus": []},
     {"run_id": "active-run", "focus": {"x": True, "y": 0}},
     {"run_id": "active-run", "focus": {"x": 0, "y": 0, "radius": 0}},
     {"run_id": "active-run", "focus": {"x": 0, "y": 0, "strength": "0.75"}}],
)
def test_focus_control_rejects_invalid_small_messages(server: GeometrizeServer, payload: dict) -> None:
    session = ImageSession(1, 1, (0, 0, 0, 255), SimpleNamespace(attempts=0))
    server.activate_session("controlled", session, "active-run", options=RunOptions(steps=1))
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/sessions/controlled/focus", payload)
    assert error.value.code == HTTPStatus.BAD_REQUEST
    assert json.load(error.value)["code"] in {"invalid_request", "invalid_options"}
    assert session.focus is None
    assert server.work_budget.usage() == (0, 0)


def test_focus_control_rejects_old_and_finished_run_tokens(server: GeometrizeServer) -> None:
    session = ImageSession(1, 1, (0, 0, 0, 255), SimpleNamespace(attempts=0))
    server.activate_session("controlled", session, "active-run", options=RunOptions(steps=1))
    for token in ("old-run", "future-run"):
        with pytest.raises(HTTPError) as error:
            _post(server, "/api/sessions/controlled/focus", {"run_id": token, "focus": {"x": 1, "y": 1}})
        assert error.value.code == HTTPStatus.CONFLICT
        assert json.load(error.value)["code"] == "stale_run"
    server.finish_session("controlled", session)
    with pytest.raises(HTTPError) as error:
        _post(server, "/api/sessions/controlled/focus", {"run_id": "active-run", "focus": {"x": 1, "y": 1}})
    assert json.load(error.value)["code"] == "stale_run"
    assert session.focus is None
    assert session.attempts == session.revision == 0


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_live_focus_updates_take_effect_between_stream_attempts(server: GeometrizeServer) -> None:
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    seen_focus = []

    class ControlledNative:
        initial_score = 0.5
        score = 0.5
        attempts = 0

        def step(self, options):
            seen_focus.append(options["focus"])
            if self.attempts < 2:
                entered[self.attempts].set()
                assert release[self.attempts].wait(5)
            self.attempts += 1
            self.score *= 0.9
            return {
                "attempt": self.attempts,
                "shapes": [{"type": "rectangle", "score": self.score,
                            "color": {"r": 20, "g": 40, "b": 60, "a": 255},
                            "data": {"x1": 0, "y1": 0, "x2": 4, "y2": 4}}],
            }

        def current_rgba(self):
            return bytes((20, 40, 60, 255)) * 64

    session = ImageSession(8, 8, (20, 40, 60, 255), ControlledNative())
    server.store_session("controlled", session)
    initial = Focus(0.25, 0.5).to_dict()
    moved = Focus(0.8, 0.2, strength=1).to_dict()
    body = json.dumps({"session_id": "controlled", "options": {"steps": 3, "focus": initial}}).encode()
    try:
        with _tracked_post(server) as request_id:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/run/stream", data=body,
                headers={"Content-Type": "application/json", "X-Test-Request-ID": request_id}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                start = json.loads(response.readline())
                assert start["focus"] == initial
                assert entered[0].wait(5)
                ack = _post(server, "/api/sessions/controlled/focus", {"run_id": start["run_id"], "focus": moved})
                assert ack == {"focus": moved, "applies": "next_attempt"}
                assert session.attempts == session.revision == 0
                release[0].set()
                assert entered[1].wait(5)
                assert session.attempts == session.revision == 1
                ack = _post(server, "/api/sessions/controlled/focus", {"run_id": start["run_id"], "focus": None})
                assert ack == {"focus": None, "applies": "next_attempt"}
                release[1].set()
                events = [json.loads(line) for line in response]
        terminal = events[-1]
        assert terminal["event"] == "complete"
        assert terminal["focus"] is None
        assert terminal["attempts"] == terminal["shape_count"] == terminal["revision"] == 3
        assert terminal["batch_summary"]["initial_focus"] == initial
        assert terminal["batch_summary"]["focus"] is None
        assert seen_focus == [initial, moved, None]
        assert [event["focus"] for event in events if event["event"] == "step"] == seen_focus
        assert server.work_budget.usage() == (0, 0)
        snapshot = _post(server, "/api/sessions/controlled/snapshot", {})
        assert snapshot["focus"] is None
        assert snapshot["shape_count"] == snapshot["attempts"] == 3

        # A queued paint stroke is an ordinary one-accepted-shape continuation
        # with its own captured focus, rather than retargeting the prior stroke.
        stroke = _post_stream(server, {"session_id": "controlled", "options": {"steps": 1, "focus": moved}})
        assert stroke[0]["focus"] == stroke[-1]["focus"] == moved
        assert stroke[-1]["batch_summary"]["added"] == 1
        assert stroke[-1]["shape_count"] == stroke[-1]["attempts"] == 4
    finally:
        for event in release:
            event.set()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_first_paint_stroke_can_create_focused_session(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (48, 32), (20, 40, 60, 255))
    ImageDraw.Draw(image).rectangle((24, 0, 47, 31), fill=(230, 180, 80, 255))
    focus = Focus(0.8, 0.5, radius=0.1, strength=1).to_dict()
    events = _post_stream(server, {"image": image_to_data_url(image), "options": {
        "steps": 1, "shape_types": ["circle"], "shape_count": 8, "mutations": 16, "focus": focus,
    }})
    assert events[0]["continued"] is False
    assert events[0]["focus"] == events[-1]["focus"] == focus
    assert events[-1]["batch_summary"]["initial_focus"] == focus
    assert events[-1]["batch_summary"]["added"] == events[-1]["shape_count"] == 1
    assert events[-1]["stop_reason"] == "target_reached"


def test_active_session_is_pinned_until_finish(server: GeometrizeServer) -> None:
    clock = _FakeClock()
    server._clock = clock
    server.session_idle_seconds = 1
    server.max_sessions = 1
    active = _fake_session(16, 16)
    server.activate_session("active", active, "current-run", options=RunOptions(steps=1))
    clock.advance(2)
    server.service_actions()
    server.store_session("idle", _fake_session(16, 16))
    assert server.get_session("active") is active
    assert server.get_session("idle") is None
    server.finish_session("active", active)
    clock.advance(2)
    server.service_actions()
    assert server.get_session("active") is None


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_pause_waits_for_current_shape_and_reconciles_terminal_snapshot(server: GeometrizeServer) -> None:
    entered = threading.Event()
    finish_step = threading.Event()

    class ControlledNative:
        initial_score = 0.5
        score = 0.5
        attempts = 0

        def step(self, options):
            entered.set()
            assert finish_step.wait(5)
            self.attempts += 1
            self.score = 0.25
            return {
                "attempt": self.attempts,
                "shapes": [
                    {
                        "type": "rectangle",
                        "score": self.score,
                        "color": {"r": 20, "g": 40, "b": 60, "a": 255},
                        "data": {"x1": 0, "y1": 0, "x2": 4, "y2": 4},
                    }
                ],
            }

        def current_rgba(self):
            return bytes((20, 40, 60, 255)) * 64

    session = ImageSession(8, 8, (20, 40, 60, 255), ControlledNative())
    server.store_session("controlled", session)
    body = json.dumps({"session_id": "controlled", "run_id": "ignored-client-id", "options": {"steps": 3}}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            start = json.loads(response.readline())
            assert start["run_id"] != "ignored-client-id"
            assert entered.wait(5)
            with pytest.raises(HTTPError) as error:
                _post(server, "/api/sessions/controlled/pause", {"run_id": "old-run"})
            assert json.load(error.value)["code"] == "stale_run"
            ack = _post(server, "/api/sessions/controlled/pause", {"run_id": start["run_id"]})
            assert ack["event"] == "pause_requested"
            finish_step.set()
            events = [json.loads(line) for line in response]
        terminal = events[-1]
        assert terminal["event"] == "paused"
        assert terminal["stop_reason"] == "paused"
        assert terminal["attempts"] == terminal["shape_count"] == 1
        assert terminal["batch_summary"]["added"] == 1
        assert len(terminal["shapes"]) == 1
        assert server.work_budget.usage() == (0, 0)
        snapshot = _post(server, "/api/sessions/controlled/snapshot", {})
        assert snapshot["shape_count"] == 1
        assert "preview" not in snapshot
    finally:
        finish_step.set()


def _post(server: GeometrizeServer, path: str, payload: dict | None = None, *, body: bytes | None = None) -> dict:
    with _tracked_post(server) as request_id:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            data=body if body is not None else json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json", "X-Test-Request-ID": request_id},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)


@contextmanager
def _tracked_post(server: GeometrizeServer) -> Iterator[str]:
    observed = cast(_ObservedServer, server)
    request_id = uuid.uuid4().hex
    completion = threading.Event()
    observed.post_completions[request_id] = completion
    try:
        yield request_id
    finally:
        try:
            assert completion.wait(timeout=5), "POST handler did not finish"
        finally:
            del observed.post_completions[request_id]


def _png_size(data_url: str) -> tuple[int, int]:
    with Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as image:
        return image.size
