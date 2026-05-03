from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import PurePosixPath
from typing import Any

from .images import image_from_data_url, image_to_data_url
from .native import NativeBackendUnavailable, RunOptions, iter_image, native_available, run_image
from .svg import shapes_to_svg


class GeometrizeServer(ThreadingHTTPServer):
    daemon_threads = True


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
        path = PurePosixPath(self.path.split("?", 1)[0])
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
            image = image_from_data_url(str(payload["image"]))
            options = RunOptions.from_mapping(payload.get("options"))
            result = run_image(image, options)
            svg = shapes_to_svg(result.shapes, result.width, result.height, result.background)
            self._send_json(
                {
                    "width": result.width,
                    "height": result.height,
                    "attempts": result.attempts,
                    "shape_count": len(result.shapes),
                    "preview": image_to_data_url(result.image),
                    "svg": svg,
                    "shapes": result.shapes,
                }
            )
        except NativeBackendUnavailable as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _run_stream(self) -> None:
        try:
            payload = self._read_json()
            image = image_from_data_url(str(payload["image"]))
            options = RunOptions.from_mapping(payload.get("options"))
            if not native_available():
                raise NativeBackendUnavailable("Geometrize native backend is unavailable")
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
            for event in iter_image(image, options):
                event_name = str(event["event"])
                if event_name == "complete":
                    result = event["result"]
                    svg = shapes_to_svg(result.shapes, result.width, result.height, result.background)
                    self._write_stream_event(
                        {
                            "event": "complete",
                            "width": result.width,
                            "height": result.height,
                            "attempts": result.attempts,
                            "shape_count": len(result.shapes),
                            "preview": image_to_data_url(result.image),
                            "svg": svg,
                            "shapes": result.shapes,
                        }
                    )
                    continue
                self._write_stream_event(event)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._write_stream_event({"event": "error", "error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Missing request body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_stream_event(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload).encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _send_static(self, name: str, content_type: str | None = None) -> None:
        clean_name = str(PurePosixPath(name))
        if clean_name.startswith("../") or clean_name == "..":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ref = resources.files("geometrize_py.static").joinpath(clean_name)
        if not ref.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = ref.read_bytes()
        guessed = content_type or mimetypes.guess_type(clean_name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
