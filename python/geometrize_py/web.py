from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from .images import image_from_data_url, image_to_data_url
from .native import ImageSession, NativeBackendUnavailable, RunOptions, RunResult, native_available, run_image
from .render import export_dimensions, render_shapes_to_image
from .svg import shapes_to_svg

MAX_REQUEST_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SESSIONS = 8
DEFAULT_MAX_ACTIVE_RENDERS = 2
DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
DEFAULT_SESSION_MEMORY_BYTES = 512 * 1024 * 1024
ESTIMATED_BYTES_PER_PIXEL = 16
ESTIMATED_BYTES_PER_SHAPE = 512
ESTIMATED_SESSION_OVERHEAD_BYTES = 64 * 1024


@dataclass
class _StoredSession:
    session: ImageSession
    last_accessed: float
    estimated_bytes: int


class GeometrizeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_active_renders: int = DEFAULT_MAX_ACTIVE_RENDERS,
        session_idle_seconds: float = DEFAULT_SESSION_IDLE_SECONDS,
        max_session_bytes: int = DEFAULT_SESSION_MEMORY_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        if max_active_renders < 1:
            raise ValueError("max_active_renders must be positive")
        if session_idle_seconds <= 0:
            raise ValueError("session_idle_seconds must be positive")
        if max_session_bytes < 1:
            raise ValueError("max_session_bytes must be positive")
        super().__init__(server_address, RequestHandlerClass)
        self.sessions: OrderedDict[str, _StoredSession] = OrderedDict()
        self.sessions_lock = threading.Lock()
        self._render_slots = threading.BoundedSemaphore(max_active_renders)
        self.max_sessions = max_sessions
        self.session_idle_seconds = session_idle_seconds
        self.max_session_bytes = max_session_bytes
        self._clock = clock
        self._stored_session_bytes = 0

    def try_acquire_render_slot(self) -> bool:
        return self._render_slots.acquire(blocking=False)

    def release_render_slot(self) -> None:
        self._render_slots.release()

    def store_session(self, session_id: str, session: ImageSession) -> None:
        with self.sessions_lock:
            now = self._clock()
            self._remove_expired_sessions(now)
            previous = self.sessions.pop(session_id, None)
            if previous is not None:
                self._stored_session_bytes -= previous.estimated_bytes
            record = _StoredSession(session, now, _estimate_session_bytes(session))
            self.sessions[session_id] = record
            self._stored_session_bytes += record.estimated_bytes
            self.sessions.move_to_end(session_id)
            self._enforce_session_limits()

    def get_session(self, session_id: str) -> ImageSession | None:
        with self.sessions_lock:
            now = self._clock()
            self._remove_expired_sessions(now)
            record = self.sessions.get(session_id)
            if record is not None:
                record.last_accessed = now
                self.sessions.move_to_end(session_id)
                return record.session
            return None

    def service_actions(self) -> None:
        super().service_actions()
        with self.sessions_lock:
            self._remove_expired_sessions(self._clock())

    def _remove_expired_sessions(self, now: float) -> None:
        while self.sessions:
            session_id, record = next(iter(self.sessions.items()))
            if now - record.last_accessed < self.session_idle_seconds:
                break
            self._remove_session(session_id)

    def _enforce_session_limits(self) -> None:
        while len(self.sessions) > self.max_sessions or self._stored_session_bytes > self.max_session_bytes:
            session_id = next(iter(self.sessions))
            self._remove_session(session_id)

    def _remove_session(self, session_id: str) -> None:
        record = self.sessions.pop(session_id)
        self._stored_session_bytes -= record.estimated_bytes


def run_server(host: str = "127.0.0.1", port: int = 7860, open_browser: bool = False) -> None:
    server = GeometrizeServer((host, port), GeometrizeRequestHandler)
    url = f"http://{host}:{server.server_port}"
    print(f"Geometrize UI running at {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Geometrize UI")
    finally:
        server.server_close()


class GeometrizeRequestHandler(BaseHTTPRequestHandler):
    server_version = "GeometrizePython/0.2"

    def do_GET(self) -> None:
        path = PurePosixPath(unquote(urlsplit(self.path).path))
        if str(path) in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if str(path) == "/health":
            self._send_json({"ok": True, "native": native_available()})
            return
        if str(path).startswith("/static/"):
            self._send_static(str(path).removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in {"/api/run", "/api/run/stream"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if not self.geometrize_server.try_acquire_render_slot():
            self._send_json(
                {"error": "The renderer is busy. Wait for an active render to finish."},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        try:
            if path == "/api/run/stream":
                self._run_stream(payload)
            else:
                self._run_once(payload)
        finally:
            self.geometrize_server.release_render_slot()

    def _run_once(self, payload: dict[str, Any]) -> None:
        try:
            image = image_from_data_url(_required_text(payload, "image"))
            options = RunOptions.from_mapping(payload.get("options"))
            result = run_image(image, options)
            self._send_json(self._result_payload(result, options))
        except NativeBackendUnavailable as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _run_stream(self, payload: dict[str, Any]) -> None:
        try:
            options = RunOptions.from_mapping(payload.get("options"))
            if not native_available():
                raise NativeBackendUnavailable("Geometrize native backend is unavailable")
            session_id = str(payload.get("session_id") or "").strip()
            image_value = payload.get("image")
            continued = bool(session_id and image_value is None)
            if continued:
                session = self._session_by_id(session_id)
            else:
                if image_value is None:
                    raise ValueError("Choose an image to start a new run")
                image = image_from_data_url(str(image_value))
                session = ImageSession.from_image(image, options)
                session_id = uuid.uuid4().hex
                self.geometrize_server.store_session(session_id, session)
        except NativeBackendUnavailable as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        try:
            self._write_stream_event(
                {
                    "event": "start",
                    "session_id": session_id,
                    "continued": continued,
                    "width": session.width,
                    "height": session.height,
                    "background": session.background,
                    "attempts": session.attempts,
                    "shape_count": len(session.shapes),
                    "shapes": session.shapes,
                    "batch": session.batch_count + 1,
                    "batch_goal": options.steps,
                }
            )
            for event in session.run_batch(options):
                self._write_stream_event(event)
            self._write_stream_event(self._result_payload(session.result(), options, session_id))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._write_stream_event({"event": "error", "error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return
        finally:
            # Refresh the LRU position, idle deadline, and size estimate after a
            # batch. The local reference remains valid if the cache evicts it.
            self.geometrize_server.store_session(session_id, session)

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def geometrize_server(self) -> GeometrizeServer:
        return cast(GeometrizeServer, self.server)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc
        if length <= 0:
            raise ValueError("Missing request body")
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON request body") from exc
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload

    def _session_by_id(self, session_id: str) -> ImageSession:
        session = self.geometrize_server.get_session(session_id)
        if session is None:
            raise ValueError("Unknown render session. Choose an image to start a new run.")
        return session

    def _result_payload(self, result: RunResult, options: RunOptions, session_id: str | None = None) -> dict[str, Any]:
        export_width, export_height = export_dimensions(result.width, result.height, options.export_size)
        preview = render_shapes_to_image(
            result.shapes,
            result.width,
            result.height,
            result.background,
            export_width,
            export_height,
        )
        svg = shapes_to_svg(
            result.shapes,
            result.width,
            result.height,
            result.background,
            export_width,
            export_height,
        )
        payload: dict[str, Any] = {
            "event": "complete",
            "width": result.width,
            "height": result.height,
            "export_width": export_width,
            "export_height": export_height,
            "attempts": result.attempts,
            "shape_count": len(result.shapes),
            "preview": image_to_data_url(preview),
            "svg": svg,
            "shapes": result.shapes,
        }
        if session_id:
            payload["session_id"] = session_id
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _write_stream_event(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload).encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _send_static(self, name: str, content_type: str | None = None) -> None:
        path = _safe_static_path(name)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ref = resources.files("geometrize_py.static").joinpath(*path.parts)
        if not ref.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = ref.read_bytes()
        guessed = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def _safe_static_path(name: str) -> PurePosixPath | None:
    path = PurePosixPath(unquote(name))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _estimate_session_bytes(session: ImageSession) -> int:
    """Approximate the native bitmaps plus Python-side shape metadata."""
    pixels = max(0, session.width) * max(0, session.height)
    return (
        ESTIMATED_SESSION_OVERHEAD_BYTES
        + pixels * ESTIMATED_BYTES_PER_PIXEL
        + len(session.shapes) * ESTIMATED_BYTES_PER_SHAPE
    )
