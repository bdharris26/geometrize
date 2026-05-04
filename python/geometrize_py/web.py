from __future__ import annotations

import json
import mimetypes
import threading
import uuid
import webbrowser
from collections import OrderedDict
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


class GeometrizeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.sessions: OrderedDict[str, ImageSession] = OrderedDict()
        self.sessions_lock = threading.Lock()
        self.max_sessions = 8

    def store_session(self, session_id: str, session: ImageSession) -> None:
        with self.sessions_lock:
            self.sessions[session_id] = session
            self.sessions.move_to_end(session_id)
            while len(self.sessions) > self.max_sessions:
                self.sessions.popitem(last=False)

    def get_session(self, session_id: str) -> ImageSession | None:
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if session is not None:
                self.sessions.move_to_end(session_id)
            return session


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
        if path == "/api/run/stream":
            self._run_stream()
            return
        if path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            image = image_from_data_url(_required_text(payload, "image"))
            options = RunOptions.from_mapping(payload.get("options"))
            result = run_image(image, options)
            self._send_json(self._result_payload(result, options))
        except NativeBackendUnavailable as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _run_stream(self) -> None:
        try:
            payload = self._read_json()
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
