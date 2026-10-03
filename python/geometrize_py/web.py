from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from .contracts import MAX_REQUEST_BYTES, OPTION_LIMITS, app_contract
from .errors import APIError
from .exporting import ExportArtifact, ExportCache, RenderScene, build_export, inspect_scene, validate_scene
from .images import image_bytes_size, image_data_url_bytes, image_to_data_url, open_image_bytes
from .native import ImageSession, NativeBackendUnavailable, RunOptions, native_available
from .render import export_dimensions
from .resources import (
    DEFAULT_ACTIVE_MEMORY_BYTES,
    DEFAULT_WORKER_BUDGET,
    ResourceLease,
    WorkBudget,
    export_memory_bytes,
    fitting_memory_bytes,
    scene_memory_bytes,
)

DEFAULT_MAX_SESSIONS = 8
DEFAULT_MAX_ACTIVE_RENDERS = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
DEFAULT_SESSION_MEMORY_BYTES = 512 * 1024 * 1024
MAX_SESSION_ACTION_BYTES = 16 * 1024
MAX_PAUSE_BODY_BYTES = 4 * 1024
REQUEST_MEMORY_FLOOR = 64 * 1024
JSON_EXPANSION_FACTOR = 32
ESTIMATED_BYTES_PER_PIXEL = 16
ESTIMATED_BYTES_PER_SHAPE = 512
ESTIMATED_SESSION_OVERHEAD_BYTES = 64 * 1024
LOGGER = logging.getLogger(__name__)
SESSION_ACTION = re.compile(r"^/api/sessions/([a-zA-Z0-9_-]+)/(pause|snapshot)$")
IMAGE_LITERAL = re.compile(rb'"image"\s*:\s*"(data:image/[^"\\]{0,128};base64,[A-Za-z0-9+/=]*)"')


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
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        session_idle_seconds: float = DEFAULT_SESSION_IDLE_SECONDS,
        max_session_bytes: int = DEFAULT_SESSION_MEMORY_BYTES,
        worker_budget: int = DEFAULT_WORKER_BUDGET,
        active_memory_bytes: int = DEFAULT_ACTIVE_MEMORY_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(max_sessions, max_active_renders, max_session_bytes) < 1:
            raise ValueError("Session, render, and cache limits must be positive")
        if min(request_timeout_seconds, session_idle_seconds) <= 0:
            raise ValueError("Request and session timeouts must be positive")
        self.work_budget = WorkBudget(worker_budget, active_memory_bytes)
        super().__init__(server_address, RequestHandlerClass)
        self.sessions: OrderedDict[str, _StoredSession] = OrderedDict()
        self.sessions_lock = threading.RLock()
        self.active_runs: dict[str, str] = {}
        self._render_slots = threading.BoundedSemaphore(max_active_renders)
        self.max_sessions = max_sessions
        self.max_active_renders = max_active_renders
        self.default_workers = min(OPTION_LIMITS["max_threads"][1], max(1, worker_budget // max_active_renders))
        self.request_timeout_seconds = request_timeout_seconds
        self.session_idle_seconds = session_idle_seconds
        self.max_session_bytes = max_session_bytes
        self.export_cache = ExportCache()
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
            self._enforce_session_limits()

    def get_session(self, session_id: str) -> ImageSession | None:
        with self.sessions_lock:
            now = self._clock()
            self._remove_expired_sessions(now)
            record = self.sessions.get(session_id)
            if record is None:
                return None
            record.last_accessed = now
            self.sessions.move_to_end(session_id)
            return record.session

    def activate_session(self, session_id: str, session: ImageSession, run_id: str) -> None:
        with self.sessions_lock:
            self.active_runs[session_id] = run_id
            self.store_session(session_id, session)

    def finish_session(self, session_id: str, session: ImageSession) -> None:
        with self.sessions_lock:
            self.active_runs.pop(session_id, None)
            self.store_session(session_id, session)

    def service_actions(self) -> None:
        super().service_actions()
        with self.sessions_lock:
            self._remove_expired_sessions(self._clock())

    def _remove_expired_sessions(self, now: float) -> None:
        for session_id, record in list(self.sessions.items()):
            if session_id not in self.active_runs and now - record.last_accessed >= self.session_idle_seconds:
                self._remove_session(session_id)

    def _enforce_session_limits(self) -> None:
        for session_id in list(self.sessions):
            if len(self.sessions) <= self.max_sessions and self._stored_session_bytes <= self.max_session_bytes:
                break
            if session_id not in self.active_runs:
                self._remove_session(session_id)

    def _remove_session(self, session_id: str) -> None:
        self._stored_session_bytes -= self.sessions.pop(session_id).estimated_bytes


def run_server(
    host: str = "127.0.0.1",
    port: int = 7860,
    open_browser: bool = False,
    *,
    worker_budget: int = DEFAULT_WORKER_BUDGET,
    active_memory_bytes: int = DEFAULT_ACTIVE_MEMORY_BYTES,
) -> None:
    server = GeometrizeServer(
        (host, port),
        GeometrizeRequestHandler,
        worker_budget=worker_budget,
        active_memory_bytes=active_memory_bytes,
    )
    url = f"http://{host}:{server.server_port}"
    print(f"Geometrize UI running at {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Geometrize UI")
    finally:
        with server.sessions_lock:
            for record in server.sessions.values():
                record.session.request_cancel()
        server.server_close()


class GeometrizeRequestHandler(BaseHTTPRequestHandler):
    server_version = "GeometrizePython/0.2"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.geometrize_server.request_timeout_seconds)

    @property
    def geometrize_server(self) -> GeometrizeServer:
        return cast(GeometrizeServer, self.server)

    def do_GET(self) -> None:
        path = PurePosixPath(unquote(urlsplit(self.path).path))
        if str(path) in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
        elif str(path) == "/health":
            self._send_json({"ok": True, "native": native_available()})
        elif str(path) == "/api/config":
            config = app_contract()
            server = self.geometrize_server
            config["resources"] = {
                "worker_budget": server.work_budget.workers,
                "default_workers": server.default_workers,
                "max_active_renders": server.max_active_renders,
                "active_memory_bytes": server.work_budget.memory_bytes,
            }
            self._send_json(config)
        elif str(path).startswith("/static/"):
            self._send_static(str(path).removeprefix("/static/"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        action = SESSION_ACTION.fullmatch(path)
        if path not in {"/api/run", "/api/run/stream", "/api/export"} and action is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._request_lease: ResourceLease | None = None
        self._request_image_bytes = 0
        self._request_body_memory = 0
        has_slot = False
        try:
            try:
                length = self._content_length()
                if action is not None and length > MAX_SESSION_ACTION_BYTES:
                    raise APIError(
                        "request_too_large", "Session request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    )
                # A pause is a tiny control message that must remain available
                # even when fitting has consumed the full CPU and memory budget.
                if action is not None and action.group(2) == "pause":
                    if length > MAX_PAUSE_BODY_BYTES:
                        raise APIError(
                            "request_too_large", "Pause request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                        )
                else:
                    self._request_lease = self._reserve_work(0, _request_memory(path, length, 0, parsed=False))
                payload = self._read_json()
                if self._request_lease is not None:
                    self._request_body_memory = _request_memory(path, length, self._request_image_bytes, parsed=True)
                    self._resize_request_memory(self._request_body_memory)
            except TimeoutError:
                self.close_connection = True
                self._send_error(APIError("request_timeout", "Request body timed out", HTTPStatus.REQUEST_TIMEOUT))
                return
            except (APIError, ValueError) as exc:
                self._send_error(exc if isinstance(exc, APIError) else APIError("invalid_request", str(exc)))
                return
            except ConnectionError:
                return
            if action is not None:
                self._session_action(action.group(1), action.group(2), payload)
                return
            has_slot = self.geometrize_server.try_acquire_render_slot()
            if not has_slot:
                raise _busy_error()
            if path == "/api/export":
                self._export(payload)
            elif path == "/api/run/stream":
                self._run_stream(payload)
            else:
                self._run_once(payload)
        except APIError as exc:
            self._send_error(exc)
        except NativeBackendUnavailable as exc:
            self._send_error(APIError("native_unavailable", str(exc), HTTPStatus.SERVICE_UNAVAILABLE))
        except (ValueError, TypeError, KeyError) as exc:
            self._send_error(APIError("invalid_request", str(exc)))
        except (ConnectionError, TimeoutError):
            return
        except Exception:
            LOGGER.exception("Geometrize request failed")
            self._send_error(
                APIError(
                    "internal_error", "The renderer encountered an unexpected error", HTTPStatus.INTERNAL_SERVER_ERROR
                )
            )
        finally:
            if has_slot:
                self.geometrize_server.release_render_slot()
            if self._request_lease is not None:
                self.geometrize_server.work_budget.release(self._request_lease)
                self._request_lease = None

    def _run_once(self, payload: dict[str, Any]) -> None:
        options = _options(payload)
        session, options, lease = self._new_session(payload, options)
        try:
            for _event in session.run_batch(options):
                pass
            self._charge_request_scene({"shapes": session.shapes})
            snapshot = self._snapshot_payload(session, include_preview=False)
            del session
        finally:
            self.geometrize_server.work_budget.release(lease)
        # One-shot requests are an explicit run-and-export convenience.
        scene = _scene_from_snapshot(snapshot)
        with self._artifact(scene, options.export_size) as artifact:
            snapshot.update(self._export_payload(scene, artifact))
            snapshot["event"] = "complete"
            self._send_json(snapshot)

    def _run_stream(self, payload: dict[str, Any]) -> None:
        options = _options(payload)
        if not native_available():
            raise NativeBackendUnavailable("Geometrize native backend is unavailable")
        session_id = str(payload.get("session_id") or "").strip()
        run_id = uuid.uuid4().hex
        continued = bool(session_id and payload.get("image") is None)
        lease: ResourceLease | None = None
        acquired = False
        activated = False
        if continued:
            session = self._session_by_id(session_id)
        else:
            session, options, lease = self._new_session(payload, options)
            session_id = uuid.uuid4().hex
        try:
            acquired = session.try_acquire_run()
            if not acquired:
                raise _session_busy_error()
            if lease is None:
                options, lease = self._reserve_fit(options, session.width, session.height)
            session.prepare_run()
            self._charge_request_scene({"shapes": session.shapes})
            self.geometrize_server.activate_session(session_id, session, run_id)
            activated = True
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
                        "run_id": run_id,
                        "width": session.width,
                        "height": session.height,
                        "background": session.background,
                        "attempts": session.attempts,
                        "shape_count": len(session.shapes),
                        "shapes": session.shapes,
                        "batch": session.batch_count + 1,
                        "batch_goal": options.steps,
                        "score": session.score,
                        "initial_score": session.initial_score,
                        "revision": session.revision,
                        "effective_threads": options.max_threads,
                    }
                )
                with closing(session.run_reserved_batch(options)) as events:
                    for event in events:
                        self._write_stream_event(event)
                self._charge_request_scene({"shapes": session.shapes})
                terminal = self._snapshot_payload(session, session_id)
                terminal["event"] = "paused" if session.stop_reason == "paused" else "complete"
                terminal["run_id"] = run_id
                self._write_stream_event(terminal)
            except (ConnectionError, TimeoutError):
                session.request_cancel()
            except Exception:
                LOGGER.exception("Geometrize stream failed")
                try:
                    self._write_stream_event(
                        {
                            "event": "error",
                            "code": "render_failed",
                            "error": "The render could not finish",
                        }
                    )
                except (ConnectionError, TimeoutError):
                    pass
        finally:
            if activated:
                self.geometrize_server.finish_session(session_id, session)
            if acquired:
                session.release_run()
            if lease is not None:
                self.geometrize_server.work_budget.release(lease)

    def _session_action(self, session_id: str, action: str, payload: dict[str, Any]) -> None:
        session = self._session_by_id(session_id)
        if action == "pause":
            with self.geometrize_server.sessions_lock:
                active_run = self.geometrize_server.active_runs.get(session_id)
                if active_run is not None:
                    if payload.get("run_id") != active_run:
                        raise APIError("stale_run", "That run has already ended.", HTTPStatus.CONFLICT)
                    session.request_cancel()
            self._send_json(
                {
                    "event": "pause_requested",
                    "session_id": session_id,
                    "run_id": active_run,
                    "already_stopped": active_run is None,
                }
            )
            return
        if not session.try_acquire_run():
            raise _session_busy_error()
        lease: ResourceLease | None = None
        try:
            lease = self._reserve_work(1, scene_memory_bytes(*inspect_scene({"shapes": session.shapes})))
            snapshot = self._snapshot_payload(session, session_id, include_preview=False)
            snapshot["event"] = "snapshot"
            self._send_json(snapshot)
        finally:
            session.release_run()
            if lease is not None:
                self.geometrize_server.work_budget.release(lease)

    def _new_session(
        self, payload: dict[str, Any], options: RunOptions
    ) -> tuple[ImageSession, RunOptions, ResourceLease]:
        try:
            raw = image_data_url_bytes(_required_text(payload, "image"))
            width, height = image_bytes_size(raw)
        except (ValueError, OSError) as exc:
            raise APIError("invalid_image", str(exc)) from exc
        options, lease = self._reserve_fit(
            options,
            min(width, options.max_size),
            min(height, options.max_size),
            extra_memory=width * height * 12 + len(raw) * 2,
        )
        try:
            session = ImageSession.from_image(open_image_bytes(raw), options)
        except (ValueError, OSError) as exc:
            self.geometrize_server.work_budget.release(lease)
            raise APIError("invalid_image", str(exc)) from exc
        except BaseException:
            self.geometrize_server.work_budget.release(lease)
            raise
        return session, options, lease

    def _reserve_fit(
        self,
        options: RunOptions,
        width: int,
        height: int,
        *,
        extra_memory: int = 0,
    ) -> tuple[RunOptions, ResourceLease]:
        server = self.geometrize_server
        workers = min(options.max_threads or server.default_workers, server.work_budget.workers)
        memory = fitting_memory_bytes(width, height, workers) + extra_memory
        return replace(options, max_threads=workers), self._reserve_work(workers, memory)

    def _reserve_work(self, workers: int, memory: int) -> ResourceLease:
        budget = self.geometrize_server.work_budget
        if memory > budget.memory_bytes:
            raise APIError(
                "memory_limit", "This operation exceeds the active memory budget; reduce image or export size"
            )
        lease = budget.try_reserve(workers, memory)
        if lease is None:
            raise _busy_error()
        return lease

    def _snapshot_payload(
        self,
        session: ImageSession,
        session_id: str | None = None,
        *,
        include_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "snapshot",
            "width": session.width,
            "height": session.height,
            "background": session.background,
            "shapes": session.shapes.copy(),
            "attempts": session.attempts,
            "shape_count": len(session.shapes),
            "revision": session.revision,
            "score": session.score,
            "initial_score": session.initial_score,
            "stop_reason": session.stop_reason,
            "batch_summary": session.batch_summary,
        }
        if include_preview:
            payload["preview"] = image_to_data_url(session.result().image)
        if session_id is not None:
            payload["session_id"] = session_id
        return payload

    def _export(self, payload: dict[str, Any]) -> None:
        longest = payload.get("export_size", 1024)
        lower, upper = OPTION_LIMITS["export_size"]
        if isinstance(longest, bool) or not isinstance(longest, int) or not lower <= longest <= upper:
            raise APIError("invalid_options", f"Export size must be an integer from {lower} to {upper}")
        if payload.get("result") is not None:
            if payload.get("session_id"):
                raise APIError("invalid_result", "Choose either a frozen result or a session to export")
            self._charge_request_scene(payload["result"])
            scene = validate_scene(payload["result"])
        else:
            session = self._session_by_id(_required_text(payload, "session_id"))
            if not session.try_acquire_run():
                raise _session_busy_error()
            try:
                self._charge_request_scene({"shapes": session.shapes})
                scene = _scene_from_snapshot(self._snapshot_payload(session, include_preview=False))
            finally:
                session.release_run()
        with self._artifact(scene, longest) as artifact:
            self._send_json(self._export_payload(scene, artifact))

    @contextmanager
    def _artifact(self, scene: RenderScene, longest: int) -> Iterator[ExportArtifact]:
        server = self.geometrize_server
        metadata_memory = scene_memory_bytes(*inspect_scene(scene))
        lease = self._reserve_work(1, metadata_memory)
        try:
            key = (scene.fingerprint(), longest)
            artifact = server.export_cache.get(key)
            if artifact is None:
                width, height = export_dimensions(scene.width, scene.height, longest)
                lease = self._resize_work(lease, metadata_memory + export_memory_bytes(width, height))
                artifact = build_export(scene, longest)
                server.export_cache.store(key, artifact)
            # Account for base64, UTF-8 JSON and write buffers until the response
            # has been sent, including cache hits that avoid rasterization.
            response_memory = metadata_memory + len(artifact.png) * 6 + len(artifact.svg.encode("utf-8")) * 4
            lease = self._resize_work(lease, response_memory)
            yield artifact
        finally:
            server.work_budget.release(lease)

    def _resize_work(self, lease: ResourceLease, memory: int) -> ResourceLease:
        budget = self.geometrize_server.work_budget
        if memory > budget.memory_bytes:
            raise APIError("memory_limit", "This export exceeds the active memory budget; reduce export size")
        updated = budget.try_resize(lease, memory)
        if updated is None:
            raise _busy_error()
        return updated

    def _export_payload(self, scene: RenderScene, artifact: ExportArtifact) -> dict[str, Any]:
        return {
            "event": "export",
            "width": scene.width,
            "height": scene.height,
            "export_width": artifact.width,
            "export_height": artifact.height,
            "background": scene.background,
            "shapes": scene.shapes,
            "shape_count": len(scene.shapes),
            "attempts": scene.attempts,
            "revision": scene.revision,
            "preview": "data:image/png;base64," + base64.b64encode(artifact.png).decode("ascii"),
            "svg": artifact.svg,
        }

    def _session_by_id(self, session_id: str) -> ImageSession:
        session = self.geometrize_server.get_session(session_id)
        if session is None:
            raise APIError("unknown_session", "Unknown render session. Start a new render.")
        return session

    def _read_json(self) -> dict[str, Any]:
        length = self._content_length()
        body = self.rfile.read(length)
        if len(body) != length:
            raise APIError("invalid_request", "Incomplete request body")
        self._admit_json_body(body)
        try:
            payload = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise APIError("invalid_request", "Invalid JSON request body") from exc
        if not isinstance(payload, dict):
            raise APIError("invalid_request", "Expected a JSON object")
        return payload

    def _content_length(self) -> int:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError("invalid_request", "Invalid Content-Length header") from exc
        if length <= 0:
            raise APIError("invalid_request", "Missing request body")
        if length > MAX_REQUEST_BYTES:
            raise APIError("request_too_large", "Request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        return length

    def _admit_json_body(self, body: bytes) -> None:
        if self._request_lease is None:
            return
        image_span = IMAGE_LITERAL.search(body) if urlsplit(self.path).path in {"/api/run", "/api/run/stream"} else None
        self._request_image_bytes = image_span.end(1) - image_span.start(1) if image_span is not None else 0
        self._resize_request_memory(
            _request_memory(urlsplit(self.path).path, len(body), self._request_image_bytes, parsed=False)
        )

    def _charge_request_scene(self, raw: RenderScene | dict[str, Any]) -> None:
        if self._request_lease is not None:
            self._resize_request_memory(self._request_body_memory + scene_memory_bytes(*inspect_scene(raw)))

    def _resize_request_memory(self, memory: int) -> None:
        lease = self._request_lease
        if lease is None:
            return
        budget = self.geometrize_server.work_budget
        if memory > budget.memory_bytes:
            raise APIError("memory_limit", "This request exceeds the active memory budget; reduce its size")
        updated = budget.try_resize(lease, memory)
        if updated is None:
            raise _busy_error()
        self._request_lease = updated

    def _send_error(self, error: APIError) -> None:
        try:
            self._send_json(error.payload(), error.status)
        except (ConnectionError, TimeoutError):
            pass

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _write_stream_event(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, allow_nan=False).encode("utf-8") + b"\n")
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

    def log_message(self, format: str, *args: Any) -> None:
        return


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise APIError("invalid_request", f"Missing required field: {key}")
    return value


def _options(payload: dict[str, Any]) -> RunOptions:
    value = payload.get("options")
    if value is not None and not isinstance(value, dict):
        raise APIError("invalid_options", "Options must be an object")
    try:
        return RunOptions.from_mapping(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise APIError("invalid_options", str(exc)) from exc


def _scene_from_snapshot(snapshot: dict[str, Any]) -> RenderScene:
    return RenderScene(
        snapshot["width"],
        snapshot["height"],
        tuple(snapshot["background"]),
        snapshot["shapes"],
        snapshot["attempts"],
        snapshot["revision"],
    )


def _request_memory(path: str, body_bytes: int, image_bytes: int, *, parsed: bool) -> int:
    if path in {"/api/run", "/api/run/stream"}:
        # Encoded image data is a large string with predictable expansion;
        # other JSON can contain deeply nested Python containers and needs a
        # higher allowance before json.loads constructs them.
        if image_bytes:
            return max(
                REQUEST_MEMORY_FLOOR, 4 * image_bytes + JSON_EXPANSION_FACTOR * (body_bytes - image_bytes)
            )
        if not parsed:
            return max(REQUEST_MEMORY_FLOOR, 4 * body_bytes)
    return max(REQUEST_MEMORY_FLOOR, JSON_EXPANSION_FACTOR * body_bytes)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _busy_error() -> APIError:
    return APIError(
        "renderer_busy", "The renderer is busy. Wait for an active operation to finish.", HTTPStatus.TOO_MANY_REQUESTS
    )


def _session_busy_error() -> APIError:
    return APIError("session_busy", "This render session is already active.", HTTPStatus.CONFLICT)


def _safe_static_path(name: str) -> PurePosixPath | None:
    path = PurePosixPath(unquote(name))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _estimate_session_bytes(session: ImageSession) -> int:
    pixels = max(0, session.width) * max(0, session.height)
    return (
        ESTIMATED_SESSION_OVERHEAD_BYTES
        + pixels * ESTIMATED_BYTES_PER_PIXEL
        + len(session.shapes) * ESTIMATED_BYTES_PER_SHAPE
    )
