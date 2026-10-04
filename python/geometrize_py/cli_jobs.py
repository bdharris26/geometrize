"""Serial headless jobs, admitted work, path preflight, and atomic artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cli_options import fork_overrides, options_from_args
from .contracts import OPTION_LIMITS, PROJECT_MAX_BYTES, PROJECT_MAX_HISTORY_POINTS, PROJECT_MAX_HISTORY_SHAPES
from .exporting import RenderScene, build_export, inspect_scene
from .image_probe import probe_source
from .images import image_data_url_bytes, open_image_bytes
from .json_memory import JSON_MEMORY_FLOOR, json_memory_bytes, json_read_memory
from .native import ImageSession, RunOptions, require_restore_native
from .palette import extract_palette, palette_fitting_memory_bytes, palette_memory_bytes
from .render import export_dimensions
from .resources import WorkBudget, export_memory_bytes, fitting_memory_bytes, scene_memory_bytes, source_memory_bytes
from .source import SourceOptions

PALETTE_FILE_BYTES = 16 * 1024
MAX_BATCH_JOBS = 4096


class Admission:
    """One lease accounts for retained inputs plus each operation's scratch."""

    def __init__(self, workers: int, memory_mb: int, *, retained: int = 0) -> None:
        self.budget = WorkBudget(workers, memory_mb * 1024 * 1024)
        self.retained = retained
        self.lease = self.budget.try_reserve(0, JSON_MEMORY_FLOOR)
        if self.lease is None:
            raise ValueError("Active memory budget is too small")

    def __enter__(self) -> Admission:
        try:
            self.use(0)
        except BaseException:
            self.budget.release(self.lease)
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        self.budget.release(self.lease)

    def use(self, scratch: int, *, workers: int = 0) -> None:
        memory = self.retained + scratch + JSON_MEMORY_FLOOR
        if memory > self.budget.memory_bytes:
            raise ValueError("Operation exceeds active memory budget; increase --active-memory-mb or reduce the work")
        lease = self.budget.try_resize(self.lease, memory, workers=workers)
        if lease is None:
            raise ValueError("Operation exceeds worker or active memory budget")
        self.lease = lease

    def read_bytes(self, path: Path, limit: int = PROJECT_MAX_BYTES) -> bytes:
        size = _file_size(path, limit)
        self.use(size * 4)
        raw = _read_bounded(path, size)
        self.retained += len(raw) * 2
        self.use(0)
        return raw

    def read_json(
        self,
        path: Path,
        loader: Callable[[bytes], Any] = json.loads,
        limit: int = PROJECT_MAX_BYTES,
        *,
        image_fields: tuple[bytes, ...] = (),
    ) -> Any:
        size = _file_size(path, limit)
        self.use(json_read_memory(size, image_strings=True))
        raw = _read_bounded(path, size)
        memory = json_memory_bytes(raw, image_fields=image_fields)
        self.use(memory)
        # Admit before decode/parse; keep the allowance while the normalized
        # graph is alive, including its original source and geometry copies.
        value = loader(raw)
        self.retained += memory
        self.use(0)
        return value


def _file_size(path: Path, limit: int) -> int:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Input is not a regular file: {path}")
    if info.st_size > limit:
        raise ValueError(f"File exceeds {limit} bytes: {path}")
    return info.st_size


def _read_bounded(path: Path, size: int) -> bytes:
    with path.open("rb") as source:
        raw = source.read(size + 1)
    if len(raw) != size:
        raise ValueError(f"File changed during bounded read: {path}")
    return raw


def preflight_paths(reads: Iterable[tuple[str, Path]], writes: Iterable[tuple[str, Path]]) -> None:
    """Read aliases may coexist; every writer must have a distinct target."""
    entries = [(label, path, False) for label, path in reads]
    entries.extend((label, path, True) for label, path in writes)
    names: dict[str, tuple[str, Path, bool]] = {}
    identities: dict[tuple[int, int], tuple[str, Path, bool]] = {}
    output_names: dict[str, str] = {}
    output_parents: dict[str, str] = {}
    checked_parents: set[Path] = set()
    for label, path, writing in entries:
        if writing:
            _validate_write_name(path)
            for parent in path.parents:
                if parent in checked_parents:
                    continue
                checked_parents.add(parent)
                try:
                    if not stat.S_ISDIR(parent.stat().st_mode):
                        raise ValueError(f"Output parent is not a directory: {parent}")
                except FileNotFoundError:
                    pass
        real = path.resolve()
        key = os.path.normcase(str(real))
        identity = None
        try:
            info = path.stat()
            identity = (info.st_dev, info.st_ino)
            if writing and not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{label} must be a regular output file: {path}")
        except FileNotFoundError:
            pass
        previous = names.get(key)
        same_inode = identities.get(identity) if identity is not None else None
        if previous is None and same_inode is not None and path.samefile(same_inode[1]):
            previous = same_inode
        if previous is not None and (writing or previous[2]):
            raise ValueError(f"{label} path must differ from the {previous[0]} path")
        names[key] = (label, path, writing)
        if identity is not None:
            identities[identity] = (label, path, writing)
        if writing:
            parents = [os.path.normcase(str(parent)) for parent in real.parents]
            conflicting = output_parents.get(key) or next(
                (output_names[parent] for parent in parents if parent in output_names), None
            )
            if conflicting:
                raise ValueError(f"{label} path must differ from the {conflicting} file and its parent directories")
            output_names[key] = label
            for parent in parents:
                output_parents[parent] = label


def _validate_write_name(path: Path) -> None:
    if os.name != "nt":
        return
    devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in path.parts[1:] if path.anchor else path.parts:
        if part in {".", ".."}:
            continue
        if (
            part.endswith((".", " "))
            or part.split(".", 1)[0].upper() in devices
            or any(char in '<>:"|?*' or ord(char) < 32 for char in part)
        ):
            raise ValueError(f"Output path has an ambiguous or reserved Windows component: {part}")


class PublicationError(OSError):
    def __init__(self, message: str, committed: list[str], temporary_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.committed = committed
        self.temporary_paths = temporary_paths or []


def publish(artifacts: dict[Path, bytes], *, recheck: Callable[[], None] | None = None) -> list[str]:
    """Stage every artifact first, then replace closed files one at a time."""
    staged: list[tuple[Path, Path]] = []
    committed: list[str] = []
    try:
        for destination, content in artifacts.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=".geometrize-", suffix=".tmp", dir=destination.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                staged.append((destination, temporary_path))
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
        if recheck is not None:
            recheck()
        for destination, temporary_path in staged:
            os.replace(temporary_path, destination)
            committed.append(str(destination.resolve()))
        return committed
    except BaseException as exc:
        temporary_paths = []
        for _destination, temporary_path in staged:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                temporary_paths.append(str(temporary_path))
        details = ""
        if temporary_paths:
            details = "; cleanup pending for temporary files: " + ", ".join(path[:2048] for path in temporary_paths[:3])
        if isinstance(exc, (OSError, ValueError)):
            raise PublicationError(str(exc) + details, committed, temporary_paths) from exc
        if isinstance(exc, KeyboardInterrupt):
            exc.committed = committed
            exc.temporary_paths = temporary_paths
        if details:
            print(details.lstrip("; "), file=sys.stderr)
        raise


def serialization_memory(value: Any) -> int:
    """Conservative encoding/copy space, counted without building JSON first."""
    if isinstance(value, str):
        return len(value) * 8 + 64
    if isinstance(value, dict):
        return 128 + sum(serialization_memory(key) + serialization_memory(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return 64 + sum(32 + serialization_memory(item) for item in value)
    return 64


def _batch_jobs(args: Any) -> Iterable[tuple[int, Path, int, dict[str, Path]]]:
    for index, (input_path, seed) in enumerate(((path, seed) for path in args.inputs for seed in args.seeds), 1):
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", input_path.stem).strip(".-_")[:64] or "source"
        prefix = args.output_dir / f"{index:04d}-{stem}-seed{seed}"
        outputs = {"output": Path(str(prefix) + ".png")}
        for key, suffix in (("svg", ".svg"), ("json", ".shapes.json"), ("project", ".geometrize-project.json")):
            if getattr(args, key):
                outputs[key] = Path(str(prefix) + suffix)
        yield index, input_path, seed, outputs


def command_paths(args: Any) -> tuple[list[tuple[str, Path]], list[tuple[str, Path]]]:
    if args.command == "batch":
        if len(args.inputs) * len(args.seeds) > MAX_BATCH_JOBS:
            raise ValueError(f"Batch can contain at most {MAX_BATCH_JOBS} jobs")
        reads = [(f"input {index}", path) for index, path in enumerate(args.inputs, 1)]
        writes = [
            (f"job {index} {kind}", path)
            for index, _input, _seed, outputs in _batch_jobs(args)
            for kind, path in outputs.items()
        ]
        writes.append(("manifest", args.manifest or args.output_dir / "manifest.json"))
    else:
        reads = [("input", args.input)]
        if args.command == "run":
            keys = ("output", "svg", "json", "project")
        elif args.command == "project" and args.project_command == "export":
            keys = ("output", "svg", "json")
        else:
            keys = ("output",)
        writes = [(f"{key.upper()} output", getattr(args, key)) for key in keys if getattr(args, key, None) is not None]
    palette_file = getattr(args, "palette_file", None)
    if palette_file is not None:
        reads.append(("palette file", palette_file))
    return reads, writes


def execute(args: Any, reads: list[tuple[str, Path]], writes: list[tuple[str, Path]]) -> int:
    if args.command == "run":
        run_job(args, reads, writes)
        return 0
    if args.command == "batch":
        return _batch(args, reads, writes)
    with Admission(args.workers, args.active_memory_mb) as admission:
        if args.command == "project":
            return _project_command(args, admission, reads, writes)
        return _source_command(args, admission, reads, writes)


def _resolve_options(args: Any, admission: Admission, inherited: dict[str, Any] | None = None) -> RunOptions:
    value = None
    if args.palette_file is not None:
        value = admission.read_json(args.palette_file, _json_loads, PALETTE_FILE_BYTES)
        if not isinstance(value, (list, dict)) or isinstance(value, dict) and "colors" not in value:
            raise ValueError("Palette file must contain an RGB array or an object with colors")
    return options_from_args(args, inherited, palette_value=value)


def _json_loads(content: bytes) -> Any:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"JSON cannot contain {value}")

    return json.loads(content.decode("utf-8-sig"), parse_constant=invalid_constant)


def _fit_memory(width: int, height: int, options: RunOptions, workers: int) -> int:
    memory = fitting_memory_bytes(width, height, workers)
    if options.palette is not None and options.palette.strength > 0:
        memory += palette_fitting_memory_bytes(width, height, workers)
    return memory


def _workers(options: RunOptions, admission: Admission) -> int:
    effective = min(
        options.max_threads or admission.budget.workers, admission.budget.workers, OPTION_LIMITS["max_threads"][1]
    )
    print(
        f"fitting workers: {effective} (budget {admission.budget.workers}, requested {options.max_threads})",
        file=sys.stderr,
    )
    return effective


def _snapshot(session: ImageSession, duration: int) -> dict[str, Any]:
    return {
        "width": session.width,
        "height": session.height,
        "background": list(session.background),
        "shapes": session.shapes.copy(),
        "attempts": session.attempts,
        "revision": session.revision,
        "restored_shape_count": session.restored_shape_count,
        "target_digest": session.target_digest,
        "initial_score": session.initial_score,
        "score": session.score,
        "duration_ms": duration,
        "stop_reason": session.stop_reason,
        "source": session.source.to_dict(),
        "batch_summary": session.batch_summary,
    }


def _scene(snapshot: dict[str, Any]) -> RenderScene:
    return RenderScene(
        snapshot["width"],
        snapshot["height"],
        tuple(snapshot["background"]),
        snapshot["shapes"],
        snapshot["attempts"],
        snapshot["revision"],
        snapshot["target_digest"],
    )


def _artifacts(
    args: Any,
    scene: RenderScene,
    admission: Admission,
    export_size: int,
    *,
    project: dict[str, Any] | None = None,
    held_memory: int = 0,
) -> dict[Path, bytes]:
    artifacts = {}
    width, height = export_dimensions(scene.width, scene.height, export_size)
    admission.use(
        held_memory
        + export_memory_bytes(width, height)
        + serialization_memory(scene.shapes)
        + (serialization_memory(project) * 2 if project is not None else 0)
    )
    if project is not None:
        from .project import dumps_project

        artifacts[args.project] = dumps_project(project).encode("utf-8")
    output = build_export(scene, export_size)
    artifacts[args.output] = output.png
    if getattr(args, "svg", None):
        artifacts[args.svg] = output.svg.encode("utf-8")
    if getattr(args, "json", None):
        artifacts[args.json] = json.dumps(scene.shapes, indent=2, allow_nan=False).encode("utf-8")
    return artifacts


def run_job(
    args: Any, reads: list[tuple[str, Path]], writes: list[tuple[str, Path]], *, retained: int = 0
) -> dict[str, Any]:
    started = time.monotonic()
    with Admission(args.workers, args.active_memory_mb, retained=retained) as admission:
        options = _resolve_options(args, admission)
        raw = admission.read_bytes(args.input)
        if args.project is not None and ((len(raw) + 2) // 3 * 4 + 4096) > PROJECT_MAX_BYTES:
            raise ValueError("Original source cannot fit in a 64 MiB project")
        admission.use(len(raw) * 4)
        probe = probe_source(raw, options.source)
        source_memory = source_memory_bytes(probe, len(raw))
        admission.use(source_memory)
        with closing(open_image_bytes(raw, options.source, _probe=probe)) as image:
            full_width, full_height = image.size
            if args.extract_palette is not None:
                admission.use(source_memory + palette_memory_bytes(image.width, image.height))
                extracted = options_from_args(args, palette_value=extract_palette(image, args.extract_palette))
                options = replace(options, palette=extracted.palette)
            effective = _workers(options, admission)
            working = replace(options, max_threads=effective)
            scale = min(1, options.max_size / max(image.size))
            width, height = (max(1, math.ceil(edge * scale)) for edge in image.size)
            points = options.steps * 4 if "polyline" in options.shape_types else 0
            geometry_memory = scene_memory_bytes(options.steps, points)
            fit_memory = _fit_memory(width, height, options, effective)
            admission.use(source_memory + fit_memory + geometry_memory, workers=effective)
            session = ImageSession.from_image(image, working)
            for _event in session.run_batch(working):
                pass
            snapshot = _snapshot(session, round((time.monotonic() - started) * 1000))
            del session
        project = None
        if args.project is not None:
            from .project import create_project

            admission.use(geometry_memory * 3 + len(raw) * 12)
            source = {
                "name": args.input.name,
                "width": full_width,
                "height": full_height,
                "data_url": f"data:{probe.mime_type};base64," + base64.b64encode(raw).decode("ascii"),
            }
            project = create_project(source, options, snapshot)
        summary = {
            "shape_count": len(snapshot["shapes"]),
            "restored_shape_count": 0,
            "attempts": snapshot["attempts"],
            "stop_reason": snapshot["stop_reason"],
            "duration_ms": snapshot["duration_ms"],
            "effective_workers": effective,
            "options": options.to_dict(),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "committed_outputs": [],
        }
        artifacts = _artifacts(
            args, _scene(snapshot), admission, options.export_size, project=project, held_memory=geometry_memory
        )
        committed = publish(artifacts, recheck=lambda: preflight_paths(reads, writes))
        summary["committed_outputs"] = committed
        try:
            print(f"wrote {args.output} with {summary['shape_count']} shapes")
        except BaseException as exc:
            exc.committed = committed
            raise
        return summary


def _batch(args: Any, reads: list[tuple[str, Path]], writes: list[tuple[str, Path]]) -> int:
    manifest_path = args.manifest or args.output_dir / "manifest.json"
    retained = sum(
        8192 + len(str(path)) * 16 + sum(len(str(output)) * 16 for output in outputs.values())
        for _index, path, _seed, outputs in _batch_jobs(args)
    )
    with Admission(args.workers, args.active_memory_mb, retained=retained) as admission:
        jobs = list(_batch_jobs(args))
        manifest = {
            "format": "geometrize-batch",
            "version": 1,
            "worker_budget": args.workers,
            "active_memory_mb": args.active_memory_mb,
            "jobs": [
                {
                    "index": index,
                    "input": str(path.resolve()),
                    "seed": seed,
                    "state": "planned",
                    "planned_outputs": {key: str(value.resolve()) for key, value in outputs.items()},
                    "committed_outputs": [],
                }
                for index, path, seed, outputs in jobs
            ],
        }

        def save_manifest() -> None:
            admission.use(serialization_memory(manifest))
            publish(
                {manifest_path: json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")},
                recheck=lambda: preflight_paths(reads, writes),
            )

        failed = False
        entry = None
        try:
            save_manifest()
            for entry, (_index, input_path, seed, outputs) in zip(manifest["jobs"], jobs, strict=True):
                job = copy(args)
                job.input, job.seed = input_path, seed
                for key in ("output", "svg", "json", "project"):
                    setattr(job, key, outputs.get(key))
                entry["state"] = "running"
                save_manifest()
                try:
                    summary = run_job(job, reads, writes, retained=retained)
                    entry.update(summary, state="complete")
                except (OSError, ValueError, RuntimeError, SyntaxError, RecursionError) as exc:
                    failed = True
                    entry.update(state="error", error=str(exc), committed_outputs=getattr(exc, "committed", []))
                    if getattr(exc, "temporary_paths", None):
                        entry["remaining_temporary_files"] = exc.temporary_paths
                    print(f"job {entry['index']}: {exc}", file=sys.stderr)
                save_manifest()
            manifest["state"] = "error" if failed else "complete"
            save_manifest()
            return 1 if failed else 0
        except KeyboardInterrupt as exc:
            if entry is not None and entry["state"] == "running":
                entry.update(state="interrupted", error="Interrupted", committed_outputs=getattr(exc, "committed", []))
                if getattr(exc, "temporary_paths", None):
                    entry["remaining_temporary_files"] = exc.temporary_paths
            manifest["state"] = "interrupted"
            try:
                save_manifest()
            except (OSError, ValueError, KeyboardInterrupt) as failure:
                print(f"could not save interrupted manifest: {failure}", file=sys.stderr)
            return 130


def _source_command(
    args: Any, admission: Admission, reads: list[tuple[str, Path]], writes: list[tuple[str, Path]]
) -> int:
    source = SourceOptions(args.frame, args.matte)
    raw = admission.read_bytes(args.input)
    admission.use(len(raw) * 4)
    probe = probe_source(raw, source)
    memory = source_memory_bytes(probe, len(raw))
    if args.command == "palette":
        memory += palette_memory_bytes(probe.width, probe.height)
    admission.use(memory)
    with closing(open_image_bytes(raw, source, _probe=probe)) as image:
        if args.command == "palette":
            result = extract_palette(image, args.max_colors)
        else:
            result = {
                **image.info["geometrize_source"],
                "width": image.width,
                "height": image.height,
                "source": source.to_dict(),
            }
    content = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if getattr(args, "output", None):
        publish({args.output: content.encode("utf-8")}, recheck=lambda: preflight_paths(reads, writes))
    else:
        print(content, end="")
    return 0


def _graph_memory(project: dict[str, Any]) -> int:
    return scene_memory_bytes(*_graph_counts(project))


def _project_command(
    args: Any, admission: Admission, reads: list[tuple[str, Path]], writes: list[tuple[str, Path]]
) -> int:
    from .project import (
        dumps_project,
        fork_project,
        loads_project,
        project_scene,
        replace_branch_snapshot,
    )

    project = admission.read_json(args.input, loads_project, image_fields=(b"data_url", b"preview_data_url"))
    graph_memory = _graph_memory(project)
    if args.project_command == "inspect":
        admission.use(graph_memory)
        report = {
            "version": project["version"],
            "source": {key: value for key, value in project["source"].items() if key != "data_url"},
            "active_branch": project["history"]["active_branch"],
            "saved_view_shape_count": project["history"]["view_shape_count"],
            "branches": [],
        }
        for record in project["history"]["branches"]:
            active = record["id"] == project["history"]["active_branch"]
            scene = project["result"] if active else record["result"]
            telemetry = project["telemetry"] if active else record["telemetry"]
            report["branches"].append(
                {key: record[key] for key in ("id", "name", "parent_id", "fork_shape_count", "options")}
                | {
                    "shape_count": len(scene["shapes"]),
                    "restored_shape_count": scene["restored_shape_count"],
                    "width": scene["render_width"] or scene["width"],
                    "height": scene["render_height"] or scene["height"],
                    "attempts": telemetry["attempts"],
                    "target_digest": scene.get("target_digest"),
                    "geometry_available": scene["background"] is not None
                    and bool((scene["render_width"] or scene["width"]) and (scene["render_height"] or scene["height"])),
                }
            )
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        else:
            print(f"Project v{report['version']} · {report['source']['name']} · {len(report['branches'])} experiments")
            print(f"Active: {report['active_branch']} · saved cursor: {report['saved_view_shape_count']}")
            for branch in report["branches"]:
                print(
                    f"{branch['id']}: {branch['name']} · {branch['shape_count']} shapes · {branch['attempts']} attempts"
                )
        return 0
    admission.use(graph_memory * 3)
    branch_id = args.branch or project["history"]["active_branch"]
    scene = project_scene(project, branch_id, args.at_shape)
    branch = next(record for record in project["history"]["branches"] if record["id"] == branch_id)
    if args.project_command == "export":
        export_size = args.export_size if args.export_size is not None else branch["options"]["export_size"]
        artifacts = _artifacts(args, scene, admission, export_size, held_memory=graph_memory)
        publish(artifacts, recheck=lambda: preflight_paths(reads, writes))
        print(f"wrote {args.output} with {len(scene.shapes)} shapes")
        return 0
    inherited = branch["options"]
    options = _resolve_options(args, admission, inherited)
    name = args.name if args.name is not None else _fork_name(project, branch["name"])
    admission.use(graph_memory * 4)
    child = fork_project(
        project,
        branch_id=branch["id"],
        shape_count=len(scene.shapes),
        name=name,
        overrides=fork_overrides(args, options),
    )
    if args.steps:
        counts, points = _graph_counts(child)
        if (
            counts + args.steps > PROJECT_MAX_HISTORY_SHAPES
            or points + (args.steps * 4 if "polyline" in options.shape_types else 0) > PROJECT_MAX_HISTORY_POINTS
        ):
            raise ValueError("Requested fitting would exceed the saved experiment shape or point budget")
    # Replay bounds/work validation precede source decoding. One ordinary
    # worker suffices for replay; palettes only allocate fitting scratch if
    # this command actually requests new fitting.
    scratch = require_restore_native().replay_memory(scene.width, scene.height, scene.background, scene.shapes)
    raw_url = project["source"]["data_url"]
    admission.use(graph_memory * 3 + len(raw_url) * 2)
    raw = image_data_url_bytes(raw_url)
    admission.retained += len(raw) * 2
    admission.use(graph_memory * 3 + len(raw) * 4)
    probe = probe_source(raw, options.source)
    source_memory = source_memory_bytes(probe, len(raw))
    replay_memory = fitting_memory_bytes(scene.width, scene.height, 1)
    admission.use(graph_memory * 4 + source_memory + scratch + replay_memory, workers=1)
    started = time.monotonic()
    with closing(open_image_bytes(raw, options.source, _probe=probe)) as image:
        if args.extract_palette is not None:
            admission.use(graph_memory * 3 + source_memory + palette_memory_bytes(image.width, image.height))
            options = options_from_args(args, inherited, palette_value=extract_palette(image, args.extract_palette))
            child["options"] = options.to_dict()
        effective = _workers(options, admission) if args.steps else 1
        if not args.steps:
            print("replay workers: 1", file=sys.stderr)
        working = replace(options, max_threads=effective)
        admission.use(graph_memory * 4 + source_memory + scratch + replay_memory, workers=1)
        session = ImageSession._from_validated_scene(image, working, scene, len(scene.shapes), native_checked=True)
        if args.steps:
            fit_memory = _fit_memory(scene.width, scene.height, options, effective)
            admission.use(
                graph_memory * 4
                + source_memory
                + scratch
                + fit_memory
                + scene_memory_bytes(args.steps, args.steps * 4),
                workers=effective,
            )
            for _event in session.run_batch(working):
                pass
        snapshot = _snapshot(session, round((time.monotonic() - started) * 1000))
        del session
    admission.use(graph_memory * 4 + serialization_memory(child) * 2)
    child = replace_branch_snapshot(child, child["history"]["active_branch"], snapshot)
    content = dumps_project(child).encode("utf-8")
    publish({args.output: content}, recheck=lambda: preflight_paths(reads, writes))
    print(
        f"wrote {args.output} with {len(scene.shapes)} retained "
        f"and {len(snapshot['shapes']) - len(scene.shapes)} new shapes"
    )
    return 0


def _graph_counts(project: dict[str, Any]) -> tuple[int, int]:
    shapes = points = 0
    for branch in project["history"]["branches"]:
        result = project["result"] if branch["id"] == project["history"]["active_branch"] else branch["result"]
        count, vertices = inspect_scene(result)
        shapes += count
        points += vertices
    return shapes, points


def _fork_name(project: dict[str, Any], parent: str) -> str:
    names = {branch["name"].lower() for branch in project["history"]["branches"]}
    prefix = ""
    units = 0
    for character in parent:
        size = len(character.encode("utf-16-le")) // 2
        if units + size > 65:
            break
        prefix += character
        units += size
    base = prefix + " fork"
    name, index = base, 2
    while name.lower() in names:
        name = f"{base} {index}"
        index += 1
    return name
