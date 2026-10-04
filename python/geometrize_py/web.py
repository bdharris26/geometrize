from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import socket
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

from .contracts import (
    MAX_REQUEST_BYTES,
    OPTION_LIMITS,
    PALETTE_DEFAULTS,
    PROJECT_MAX_SHAPES,
    PROJECT_MAX_TOTAL_POINTS,
    RESTORE_MAX_WORK,
    SOURCE_PREVIEW_SIZE,
    app_contract,
)
from .errors import APIError
from .exporting import ExportArtifact, ExportCache, RenderScene, build_export, inspect_scene, validate_scene
from .image_probe import SourceProbe, probe_source
from .images import fit_image, image_data_url_bytes, image_to_data_url, open_image_bytes
from .native import (
    ImageSession,
    NativeBackendUnavailable,
    RunOptions,
    native_available,
    normalize_focus,
    normalize_restore_count,
    require_restore_native,
)
from .palette import extract_palette, normalize_max_colors, palette_fitting_memory_bytes, palette_memory_bytes
from .render import export_dimensions
from .resources import (
    DEFAULT_ACTIVE_MEMORY_BYTES,
    DEFAULT_WORKER_BUDGET,
    ResourceLease,
    WorkBudget,
    export_memory_bytes,
    fitting_memory_bytes,
    scene_memory_bytes,
    source_memory_bytes,
)
from .source import SourceOptions, normalize_source

DEFAULT_MAX_SESSIONS = 8
DEFAULT_MAX_ACTIVE_RENDERS = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
DEFAULT_SESSION_MEMORY_BYTES = 512 * 1024 * 1024
MAX_SESSION_ACTION_BYTES = 16 * 1024
MAX_PAUSE_BODY_BYTES = 4 * 1024
MAX_FOCUS_BODY_BYTES = 4 * 1024
REJECTED_BODY_DRAIN_BYTES = 64 * 1024
REJECTED_BODY_DRAIN_CHUNK_BYTES = 4 * 1024
REJECTED_BODY_DRAIN_SECONDS = 0.25
REQUEST_MEMORY_FLOOR = 64 * 1024
JSON_EXPANSION_FACTOR = 32
ESTIMATED_BYTES_PER_PIXEL = 16
ESTIMATED_SESSION_OVERHEAD_BYTES = 64 * 1024
LOGGER = logging.getLogger(__name__)
SESSION_ACTION = re.compile(r"^/api/sessions/([a-zA-Z0-9_-]+)/(pause|focus|snapshot)$")
# Estimate scalar-string memory, including JSON escapes for wrapped base64.
# JSON and image decoding still validate the value; raw quotes end the span.
IMAGE_LITERAL = re.compile(
    rb'"image"\s*:\s*"((?i:data:(?:image/[^"\\]{0,128}|application/octet-stream);base64,)[A-Za-z0-9+/=\\]*)"'
)


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
        max_session_bytes: int | None = None,
        worker_budget: int = DEFAULT_WORKER_BUDGET,
        active_memory_bytes: int = DEFAULT_ACTIVE_MEMORY_BYTES,
        restore_work_limit: int = RESTORE_MAX_WORK,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_session_bytes is None:
            # Explicitly raising the active budget must also allow large fits
            # to remain resumable after their first batch finishes.
            max_session_bytes = max(DEFAULT_SESSION_MEMORY_BYTES, active_memory_bytes // 2)
        if min(max_sessions, max_active_renders, max_session_bytes) < 1:
            raise ValueError("Session, render, and cache limits must be positive")
        if min(request_timeout_seconds, session_idle_seconds) <= 0:
            raise ValueError("Request and session timeouts must be positive")
        if (isinstance(restore_work_limit, bool) or not isinstance(restore_work_limit, int)
                or not 1 <= restore_work_limit <= RESTORE_MAX_WORK):
            raise ValueError(f"restore_work_limit must be an integer between 1 and {RESTORE_MAX_WORK}")
        self.work_budget = WorkBudget(worker_budget, active_memory_bytes)
        super().__init__(server_address, RequestHandlerClass)
        self.sessions: OrderedDict[str, _StoredSession] = OrderedDict()
        self.sessions_lock = threading.RLock()
        self.active_runs: dict[str, str] = {}
        self._active_session_bytes: dict[str, int] = {}
        self._render_slots = threading.BoundedSemaphore(max_active_renders)
        self.max_sessions = max_sessions
        self.max_active_renders = max_active_renders
        self.default_workers = min(OPTION_LIMITS["max_threads"][1], max(1, worker_budget // max_active_renders))
        self.request_timeout_seconds = request_timeout_seconds
        self.session_idle_seconds = session_idle_seconds
        self.max_session_bytes = max_session_bytes
        self.restore_work_limit = restore_work_limit
        self.export_cache = ExportCache()
        self._clock = clock
        self._stored_session_bytes = 0

    def try_acquire_render_slot(self) -> bool:
        return self._render_slots.acquire(blocking=False)

    def release_render_slot(self) -> None:
        self._render_slots.release()

    def store_session(self, session_id: str, session: ImageSession, *, require_retention: bool = False) -> None:
        with self.sessions_lock:
            now = self._clock()
            estimated = max(_estimate_session_bytes(session), self._active_session_bytes.get(session_id, 0))
            record = _StoredSession(session, now, estimated)
            if require_retention:
                protected = [
                    stored for key, stored in self.sessions.items()
                    if key != session_id and key in self.active_runs
                ]
                if (len(protected) + 1 > self.max_sessions
                        or sum(stored.estimated_bytes for stored in protected) + record.estimated_bytes
                        > self.max_session_bytes):
                    raise APIError(
                        "session_capacity", "Active sessions fill the saved session cache; wait for a run to finish",
                        HTTPStatus.TOO_MANY_REQUESTS,
                    )
            self._remove_expired_sessions(now)
            previous = self.sessions.pop(session_id, None)
            if previous is not None:
                self._stored_session_bytes -= previous.estimated_bytes
            self.sessions[session_id] = record
            self._stored_session_bytes += record.estimated_bytes
            self._enforce_session_limits(session_id if require_retention else None)

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

    def activate_session(self, session_id: str, session: ImageSession, run_id: str, *, options: RunOptions) -> None:
        with self.sessions_lock:
            projected = _projected_session_bytes(session, options)
            previous_run = self.active_runs.get(session_id)
            previous_projection = self._active_session_bytes.get(session_id)
            self.active_runs[session_id] = run_id
            self._active_session_bytes[session_id] = projected
            try:
                self.store_session(session_id, session)
            except BaseException:
                if previous_run is None:
                    self.active_runs.pop(session_id, None)
                else:
                    self.active_runs[session_id] = previous_run
                if previous_projection is None:
                    self._active_session_bytes.pop(session_id, None)
                else:
                    self._active_session_bytes[session_id] = previous_projection
                raise

    def finish_session(self, session_id: str, session: ImageSession) -> None:
        with self.sessions_lock:
            self.active_runs.pop(session_id, None)
            self._active_session_bytes.pop(session_id, None)
            self.store_session(session_id, session)

    def service_actions(self) -> None:
        super().service_actions()
        with self.sessions_lock:
            self._remove_expired_sessions(self._clock())

    def _remove_expired_sessions(self, now: float) -> None:
        for session_id, record in list(self.sessions.items()):
            if session_id not in self.active_runs and now - record.last_accessed >= self.session_idle_seconds:
                self._remove_session(session_id)

    def _enforce_session_limits(self, protected_session_id: str | None = None) -> None:
        for session_id in list(self.sessions):
            if len(self.sessions) <= self.max_sessions and self._stored_session_bytes <= self.max_session_bytes:
                break
            if session_id not in self.active_runs and session_id != protected_session_id:
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
    restore_work_limit: int = RESTORE_MAX_WORK,
) -> None:
    server = GeometrizeServer(
        (host, port),
        GeometrizeRequestHandler,
        worker_budget=worker_budget,
        active_memory_bytes=active_memory_bytes,
        restore_work_limit=restore_work_limit,
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
            config["restore"]["max_work"] = server.restore_work_limit
            self._send_json(config)
        elif str(path).startswith("/static/"):
            self._send_static(str(path).removeprefix("/static/"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        action = SESSION_ACTION.fullmatch(path)
        routes = {"/api/run", "/api/run/stream", "/api/export", "/api/restore", "/api/palette", "/api/source/prepare"}
        if path not in routes and action is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._request_lease: ResourceLease | None = None
        self._request_image_bytes = 0
        self._request_body_memory = 0
        has_slot = False
        length: int | None = None
        body_read_started = False
        try:
            try:
                length = self._content_length()
                if action is not None and length > MAX_SESSION_ACTION_BYTES:
                    raise APIError(
                        "request_too_large", "Session request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    )
                # Pause and focus are tiny control messages that remain available
                # even when fitting has consumed the full CPU and memory budget.
                if action is not None and action.group(2) in {"pause", "focus"}:
                    control_limit = MAX_PAUSE_BODY_BYTES if action.group(2) == "pause" else MAX_FOCUS_BODY_BYTES
                    if length > control_limit:
                        raise APIError(
                            "request_too_large", "Control request body is too large",
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        )
                else:
                    self._request_lease = self._reserve_work(0, _request_memory(path, length, 0, inspected=False))
                body_read_started = True
                payload = self._read_json()
                if self._request_lease is not None:
                    self._request_body_memory = _request_memory(path, length, self._request_image_bytes, inspected=True)
                    self._resize_request_memory(self._request_body_memory)
            except TimeoutError:
                self.close_connection = True
                self._send_error(APIError("request_timeout", "Request body timed out", HTTPStatus.REQUEST_TIMEOUT))
                return
            except (APIError, ValueError) as exc:
                error = exc if isinstance(exc, APIError) else APIError("invalid_request", str(exc))
                if body_read_started:
                    self._send_error(error)
                else:
                    self._reject_before_body(error, length)
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
            elif path == "/api/restore":
                self._restore(payload)
            elif path == "/api/palette":
                self._extract_palette(payload)
            elif path == "/api/source/prepare":
                self._prepare_source(payload)
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
            session.validate_source(options.source)
        else:
            session, options, lease = self._new_session(payload, options)
            session_id = uuid.uuid4().hex
        try:
            acquired = session.try_acquire_run()
            if not acquired:
                raise _session_busy_error()
            if lease is None:
                options, lease = self._reserve_fit(options, session.width, session.height)
            session.prepare_run(options)
            self._charge_request_scene({"shapes": session.shapes})
            self.geometrize_server.activate_session(session_id, session, run_id, options=options)
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
                        "restored_shape_count": session.restored_shape_count,
                        "shapes": session.shapes,
                        "batch": session.batch_count + 1,
                        "batch_goal": options.steps,
                        "score": session.score,
                        "initial_score": session.initial_score,
                        "revision": session.revision,
                        "effective_threads": options.max_threads,
                        "focus": options.focus.to_dict() if options.focus is not None else None,
                        "palette": options.palette.to_dict() if options.palette is not None else None,
                        "source": session.source.to_dict(),
                        "target_digest": session.target_digest,
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
        if action == "focus":
            if set(payload) != {"run_id", "focus"}:
                raise APIError("invalid_request", "Focus control requires only run_id and focus")
            run_id = _required_text(payload, "run_id")
            try:
                focus = normalize_focus(payload["focus"])
            except (ValueError, TypeError, OverflowError) as exc:
                raise APIError("invalid_options", str(exc)) from exc
            with self.geometrize_server.sessions_lock:
                if self.geometrize_server.active_runs.get(session_id) != run_id:
                    raise APIError("stale_run", "That run has already ended.", HTTPStatus.CONFLICT)
                session.request_focus(focus)
            self._send_json({"focus": focus.to_dict() if focus is not None else None, "applies": "next_attempt"})
            return
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
        raw, probe, lease = self._inspect_source(payload, options.source)
        try:
            options, lease = self._reserve_fit(
                options, min(probe.width, options.max_size), min(probe.height, options.max_size),
                extra_memory=source_memory_bytes(probe, len(raw)), lease=lease,
            )
            with closing(open_image_bytes(raw, options.source, _probe=probe)) as image:
                session = ImageSession.from_image(image, options)
        except (ValueError, OSError, SyntaxError) as exc:
            self.geometrize_server.work_budget.release(lease)
            if isinstance(exc, APIError):
                raise
            raise APIError("invalid_image", str(exc)) from exc
        except BaseException:
            self.geometrize_server.work_budget.release(lease)
            raise
        return session, options, lease

    def _inspect_source(
        self, payload: dict[str, Any], source: SourceOptions,
    ) -> tuple[bytes, SourceProbe, ResourceLease]:
        # Reserve a worker before base64 decoding or walking potentially large
        # containers. Atomically grow this same lease before Image.open/seek.
        encoded = _required_text(payload, "image")
        lease = self._reserve_work(1, 64 * 1024 + len(encoded) * 2)
        try:
            raw = image_data_url_bytes(encoded)
            probe = probe_source(raw, source)
        except (ValueError, OSError) as exc:
            self.geometrize_server.work_budget.release(lease)
            raise APIError("invalid_image", str(exc)) from exc
        except BaseException:
            self.geometrize_server.work_budget.release(lease)
            raise
        return raw, probe, lease

    def _prepare_source(self, payload: dict[str, Any]) -> None:
        if set(payload) - {"image", "source"}:
            raise APIError("invalid_request", "Source preparation only accepts image and source")
        source = _source_options(payload)
        raw, probe, lease = self._inspect_source(payload, source)
        try:
            preview_pixels = min(probe.width, SOURCE_PREVIEW_SIZE) * min(probe.height, SOURCE_PREVIEW_SIZE)
            lease = self._resize_work(lease, source_memory_bytes(probe, len(raw), preview_pixels=preview_pixels))
            try:
                with closing(open_image_bytes(raw, source, _probe=probe)) as image:
                    with closing(fit_image(image, SOURCE_PREVIEW_SIZE)) as preview:
                        result = {
                            "source": source.to_dict(), "width": image.width, "height": image.height,
                            **image.info["geometrize_source"],
                            "preview_data_url": image_to_data_url(preview),
                            "preview_width": preview.width, "preview_height": preview.height,
                        }
            except (ValueError, OSError, SyntaxError) as exc:
                raise APIError("invalid_image", str(exc)) from exc
            self._send_json(result)
        finally:
            self.geometrize_server.work_budget.release(lease)

    def _restore(self, payload: dict[str, Any]) -> None:
        options = _options(payload)
        raw_scene = payload.get("result")
        # Count and admit metadata before normalization copies its geometry.
        self._charge_request_scene(raw_scene)
        scene = validate_scene(raw_scene)
        if "shape_count" in payload and payload["shape_count"] is None:
            raise APIError("invalid_result", "shape_count must be an integer")
        count = normalize_restore_count(payload.get("shape_count"), len(scene.shapes))
        prefix = scene.shapes[:count]
        shape_count, point_count = inspect_scene({"shapes": prefix})
        cache_memory = _retained_session_bytes(scene.width, scene.height, shape_count, point_count)
        if cache_memory > self.geometrize_server.max_session_bytes:
            raise APIError("memory_limit", "Restored prefix exceeds the retained session budget; restore fewer shapes")
        try:
            scratch_memory = require_restore_native().replay_memory(
                scene.width, scene.height, scene.background, prefix,
                max_work=self.geometrize_server.restore_work_limit,
            )
        except (ValueError, TypeError, OverflowError) as exc:
            raise APIError("invalid_result", str(exc)) from exc
        raw, probe, lease = self._inspect_source(payload, options.source)
        try:
            memory = (fitting_memory_bytes(scene.width, scene.height, 1)
                      + source_memory_bytes(probe, len(raw)) + scratch_memory)
            lease = self._resize_work(lease, memory)
            try:
                image = open_image_bytes(raw, options.source, _probe=probe)
            except (ValueError, OSError, SyntaxError) as exc:
                raise APIError("invalid_image", str(exc)) from exc
            try:
                with closing(image):
                    session = ImageSession._from_validated_scene(
                        image, options, scene, count, native_checked=True,
                        replay_work_limit=self.geometrize_server.restore_work_limit,
                    )
            except (ValueError, TypeError, OverflowError) as exc:
                if isinstance(exc, APIError):
                    raise
                raise APIError("invalid_result", str(exc)) from exc
            session_id = uuid.uuid4().hex
            snapshot = self._snapshot_payload(session, session_id)
            snapshot["event"] = "restored"
            snapshot["restore"] = {
                "shape_count": count,
                "source_shape_count": len(scene.shapes),
                "source_attempts": scene.attempts,
                "attempt_policy": "reset",
            }
            self.geometrize_server.store_session(session_id, session, require_retention=True)
            self._send_json(snapshot)
        finally:
            self.geometrize_server.work_budget.release(lease)

    def _extract_palette(self, payload: dict[str, Any]) -> None:
        if set(payload) - {"image", "max_colors", "source"}:
            raise APIError("invalid_request", "Palette extraction only accepts image, max_colors and source")
        try:
            max_colors = normalize_max_colors(payload.get("max_colors", PALETTE_DEFAULTS["max_colors"]))
        except ValueError as exc:
            raise APIError("invalid_options", str(exc)) from exc
        source = _source_options(payload)
        raw, probe, lease = self._inspect_source(payload, source)
        try:
            lease = self._resize_work(lease, source_memory_bytes(probe, len(raw))
                                      + palette_memory_bytes(probe.width, probe.height))
            try:
                with closing(open_image_bytes(raw, source, _probe=probe)) as image:
                    result = extract_palette(image, max_colors)
            except (ValueError, OSError, SyntaxError) as exc:
                raise APIError("invalid_image", str(exc)) from exc
            self._send_json(result)
        finally:
            self.geometrize_server.work_budget.release(lease)

    def _reserve_fit(
        self,
        options: RunOptions,
        width: int,
        height: int,
        *,
        extra_memory: int = 0,
        lease: ResourceLease | None = None,
    ) -> tuple[RunOptions, ResourceLease]:
        server = self.geometrize_server
        workers = min(options.max_threads or server.default_workers, server.work_budget.workers)
        memory = fitting_memory_bytes(width, height, workers) + extra_memory
        if options.palette is not None and options.palette.strength > 0:
            memory += palette_fitting_memory_bytes(width, height, workers)
        reserved = (self._reserve_work(workers, memory) if lease is None
                    else self._resize_work(lease, memory, workers=workers))
        return replace(options, max_threads=workers), reserved

    def _resize_work(self, lease: ResourceLease, memory: int, *, workers: int | None = None) -> ResourceLease:
        budget = self.geometrize_server.work_budget
        if memory > budget.memory_bytes:
            raise APIError(
                "memory_limit", "This operation exceeds the active memory budget; reduce source or frame size",
            )
        updated = budget.try_resize(lease, memory, workers=workers)
        if updated is None:
            raise _busy_error()
        return updated

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
        focus = session.focus
        payload: dict[str, Any] = {
            "event": "snapshot",
            "width": session.width,
            "height": session.height,
            "background": session.background,
            "shapes": session.shapes.copy(),
            "attempts": session.attempts,
            "shape_count": len(session.shapes),
            "restored_shape_count": session.restored_shape_count,
            "revision": session.revision,
            "score": session.score,
            "initial_score": session.initial_score,
            "stop_reason": session.stop_reason,
            "batch_summary": session.batch_summary,
            "focus": focus.to_dict() if focus is not None else None,
            "palette": session.palette.to_dict() if session.palette is not None else None,
            "source": session.source.to_dict(),
        }
        if include_preview:
            with closing(session.result().image) as image:
                payload["preview"] = image_to_data_url(image)
        if session.target_digest is not None:
            payload["target_digest"] = session.target_digest
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

    def _export_payload(self, scene: RenderScene, artifact: ExportArtifact) -> dict[str, Any]:
        payload = {
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
        if scene.target_digest is not None:
            payload["target_digest"] = scene.target_digest
        return payload

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
        image_routes = {"/api/run", "/api/run/stream", "/api/restore", "/api/palette", "/api/source/prepare"}
        path = urlsplit(self.path).path
        image_span = IMAGE_LITERAL.search(body) if path in image_routes and body.isascii() else None
        self._request_image_bytes = 0
        if image_span is not None:
            start, end = image_span.span(1)
            # Unicode can widen the entire decoded JSON text or image scalar.
            # An escaped quote can also truncate this match before such a suffix.
            if not body.endswith(b"\\", start, end) and body.find(b"\\u", start, end) == -1:
                self._request_image_bytes = end - start
        # The scan is complete: unmatched bytes need the full JSON allowance
        # before json.loads allocates strings or containers.
        self._resize_request_memory(
            _request_memory(path, len(body), self._request_image_bytes, inspected=True)
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

    def _reject_before_body(self, error: APIError, unread_length: int | None) -> None:
        """Reply before discarding bounded raw input for a graceful TCP close."""
        self.close_connection = True
        try:
            self._send_json(error.payload(), error.status, close=True)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)
            # Immediate close with unread/arriving bytes can reset TCP and erase
            # the response on Windows. This is teardown, never JSON admission:
            # no body is retained, decoded, or passed to an application action.
            # Invalid/oversized Content-Length has no trusted body length, so
            # the same byte cap and deadline bound its fallback cleanup.
            remaining = REJECTED_BODY_DRAIN_BYTES
            if unread_length is not None:
                remaining = min(unread_length, remaining)
            deadline = time.monotonic() + min(
                REJECTED_BODY_DRAIN_SECONDS, self.geometrize_server.request_timeout_seconds
            )
            read = getattr(self.rfile, "read1", self.rfile.read)
            while remaining > 0:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                self.connection.settimeout(timeout)
                chunk = read(min(remaining, REJECTED_BODY_DRAIN_CHUNK_BYTES))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            # Peer disconnect or the drain deadline ends cleanup; the handler
            # still closes the connection and releases any request lease.
            pass

    def _send_json(
        self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, *, close: bool = False
    ) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
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


def _source_options(payload: dict[str, Any]) -> SourceOptions:
    try:
        return normalize_source(payload.get("source"))
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
        snapshot.get("target_digest"),
    )


def _request_memory(path: str, body_bytes: int, image_bytes: int, *, inspected: bool) -> int:
    if path in {"/api/run", "/api/run/stream", "/api/restore", "/api/palette", "/api/source/prepare"}:
        # Encoded image data is a large string with predictable expansion;
        # other JSON can contain deeply nested Python containers and needs a
        # higher allowance before json.loads constructs them.
        if image_bytes:
            return max(
                REQUEST_MEMORY_FLOOR, 4 * image_bytes + JSON_EXPANSION_FACTOR * (body_bytes - image_bytes)
            )
        if not inspected:
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
    name = unquote(name)
    # Resource joins use the host filesystem, where Windows interprets these
    # characters as separators or drive prefixes rather than filename text.
    if "\\" in name or ":" in name:
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _estimate_session_bytes(session: ImageSession) -> int:
    count, points = inspect_scene({"shapes": session.shapes})
    return _retained_session_bytes(session.width, session.height, count, points)


def _projected_session_bytes(session: ImageSession, options: RunOptions) -> int:
    count, points = inspect_scene({"shapes": session.shapes})
    added = min(options.steps, max(0, PROJECT_MAX_SHAPES - count))
    added_points = min(4 * added, max(0, PROJECT_MAX_TOTAL_POINTS - points)) if "polyline" in options.shape_types else 0
    # Active batches accept at most `steps` shapes. Charge their possible new
    # geometry until finish, so cache admission cannot depend on live progress.
    return _retained_session_bytes(session.width, session.height, count + added, points + added_points)


def _retained_session_bytes(width: int, height: int, shape_count: int, point_count: int) -> int:
    pixels = max(0, width) * max(0, height)
    return (
        ESTIMATED_SESSION_OVERHEAD_BYTES
        + pixels * ESTIMATED_BYTES_PER_PIXEL
        + scene_memory_bytes(shape_count, point_count)
    )
