"""Native-free project validation, serialization and experiment selection.

The normalized model uses the saved wire format: the active experiment aliases
the top-level options, result and telemetry. Functions that change a project
return a new dictionary; original encoded source data is never re-encoded.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .colors import normalize_optional_rgb
from .contracts import (
    FOCUS_DEFAULTS,
    FOCUS_LIMITS,
    MAX_SOURCE_DIMENSION,
    OPTION_DEFAULTS,
    OPTION_LIMITS,
    PALETTE_MAX_COLORS,
    PROJECT_FORMAT,
    PROJECT_MAX_BATCHES,
    PROJECT_MAX_BRANCHES,
    PROJECT_MAX_BYTES,
    PROJECT_MAX_COORDINATE,
    PROJECT_MAX_HISTORY_POINTS,
    PROJECT_MAX_HISTORY_SHAPES,
    PROJECT_MAX_SHAPES,
    PROJECT_VERSION,
    RASTER_MIME_TYPES,
    SHAPE_TYPES,
    SOURCE_MAX_FRAMES,
)
from .exporting import RenderScene, inspect_scene, validate_scene, validate_shapes
from .palette import normalize_palette
from .source import normalize_source

if TYPE_CHECKING:
    from .native import RunOptions

_SAFE_INTEGER = 2**53 - 1
_BRANCH_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_DIGEST = re.compile(r"[a-fA-F0-9]{64}")
_RASTER_URL = re.compile(r"data:(image/[a-z0-9.+-]+)(?:;[^,]*)?;base64,[a-z0-9+/=\r\n]+", re.IGNORECASE)
_BRANCH_FIELDS = {"id", "name", "parent_id", "fork_shape_count", "options", "result", "telemetry"}


class ProjectError(ValueError):
    """A project cannot be inspected, serialized or used as a geometry scene."""

    def __init__(self, message: str, *, code: str = "invalid_project") -> None:
        super().__init__(message)
        self.code = code


def _utf16_length(value: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in value)


def _short_text(value: str, limit: int) -> str:
    units = 0
    for index, character in enumerate(value):
        units += 2 if ord(character) > 0xFFFF else 1
        if units > limit:
            return value[:index]
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectError(f"Project {label} must be an object")
    return value


def _integer(value: Any, lower: int, upper: int, label: str) -> int:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not lower <= value <= upper
            or isinstance(value, float) and not value.is_integer()):
        raise ProjectError(f"Project {label} must be an integer from {lower} to {upper}")
    return int(value)


def _dimension(value: Any, label: str) -> int | None:
    return None if value is None else _integer(value, 1, MAX_SOURCE_DIMENSION, label)


def _number(value: Any, label: str, limit: float = PROJECT_MAX_COORDINATE) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > limit:
        raise ProjectError(f"Project {label} must be a finite number")
    if not math.isfinite(value):
        raise ProjectError(f"Project {label} must be a finite number")
    return float(value)


def _color(value: Any, label: str) -> dict[str, int]:
    if isinstance(value, dict):
        value = [value.get(key) for key in ("r", "g", "b", "a")]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ProjectError(f"Project {label} requires four RGBA channels")
    return {key: _integer(channel, 0, 255, label) for key, channel in zip(("r", "g", "b", "a"), value, strict=True)}


def _raster_url(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > PROJECT_MAX_BYTES:
        raise ProjectError(f"Project {label} must be an embedded supported raster image")
    match = _RASTER_URL.fullmatch(value)
    if match is None or match[1].lower() not in RASTER_MIME_TYPES:
        raise ProjectError(f"Project {label} must be an embedded supported raster image")
    return value


def _focus(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    value = _object(value, "focus")
    if set(value) - FOCUS_LIMITS.keys() or "x" not in value or "y" not in value:
        raise ProjectError("Project focus requires x and y and only accepts x, y, radius and strength")
    result = {}
    for key, (lower, upper) in FOCUS_LIMITS.items():
        number = _number(value.get(key, FOCUS_DEFAULTS.get(key)), f"focus {key}")
        if not lower <= number <= upper:
            raise ProjectError(f"Project focus {key} must be from {lower:g} to {upper:g}")
        result[key] = number
    return result


def _rgb(value: Any, label: str) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise ProjectError(f"Project {label} must be an RGB triplet or null")
    return list(normalize_optional_rgb([_integer(channel, 0, 255, label) for channel in value], label))


def _source(value: Any) -> dict[str, Any]:
    if value is None:
        return normalize_source(None).to_dict()
    value = _object(value, "source policy")
    return normalize_source({**value,
                             "frame": _integer(value.get("frame", 0), 0, SOURCE_MAX_FRAMES - 1, "source frame"),
                             "matte": _rgb(value.get("matte"), "source matte")}).to_dict()


def _palette(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _object(value, "palette")
    colors = value.get("colors")
    if (not isinstance(colors, list) or not 1 <= len(colors) <= PALETTE_MAX_COLORS
            or any(color is None for color in colors)):
        raise ProjectError(f"Project palette requires 1 to {PALETTE_MAX_COLORS} RGB colors")
    palette = normalize_palette({**value, "colors": [_rgb(color, "palette color") for color in colors]})
    return palette.to_dict()


def _options(value: Any) -> dict[str, Any]:
    raw = _object(value, "options")
    types = raw.get("shape_types")
    if not isinstance(types, list) or any(not isinstance(name, str) or name not in SHAPE_TYPES for name in types):
        raise ProjectError("Project options shape types must be an array of supported shapes")
    # Empty arrays are valid legacy inspection settings; RunOptions requires
    # a primitive before native fitting or replay is requested.
    result: dict[str, Any] = {"shape_types": list(dict.fromkeys(types))}
    for key, (lower, upper) in OPTION_LIMITS.items():
        value = raw.get(key)
        if value is None:
            value = raw.get("max_size") if key == "export_size" else OPTION_DEFAULTS[key]
        result[key] = _integer(value, lower, upper, key)
    result["focus"] = _focus(raw.get("focus"))
    result["palette"] = _palette(raw.get("palette"))
    result["source"] = _source(raw.get("source"))
    result["background"] = _rgb(raw.get("background"), "background")
    return result


def _batches(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > PROJECT_MAX_BATCHES:
        raise ProjectError(f"Project batches must be an array of at most {PROJECT_MAX_BATCHES} items")
    result = []
    for position, item in enumerate(value, 1):
        raw = _object(item, f"batch {position}")
        types = raw.get("shapeTypes")
        if (not isinstance(types, list) or not types
                or any(not isinstance(name, str) or name not in SHAPE_TYPES for name in types)):
            raise ProjectError(f"Project batch {position} has invalid shape types")
        batch = {"shapeTypes": list(types), "state": raw.get("state", "Complete")}
        batch["state"] = _short_text(batch["state"], 40) if isinstance(batch["state"], str) else "Complete"
        for key, lower, upper in (
            ("index", 1, _SAFE_INTEGER), ("target", 1, OPTION_LIMITS["steps"][1]),
            ("candidates", 1, OPTION_LIMITS["shape_count"][1]), ("mutations", 1, OPTION_LIMITS["mutations"][1]),
            ("alpha", 1, 255), ("added", 0, PROJECT_MAX_SHAPES), ("attempts", 0, _SAFE_INTEGER),
        ):
            batch[key] = _integer(raw.get(key), lower, upper, f"batch {position} {key}")
        for key in ("seed", "max_threads", "effective_threads", "start_shape_count", "start_attempts"):
            try:
                batch[key] = _integer(raw.get(key), 0, _SAFE_INTEGER, f"batch {key}")
            except ProjectError:
                pass
        if isinstance(raw.get("reason"), str):
            batch["reason"] = _short_text(raw["reason"], 80)
        for key in ("focus", "initial_focus"):
            if key in raw:
                batch[key] = _focus(raw[key])
        if "palette" in raw:
            batch["palette"] = _palette(raw["palette"])
        if "source" in raw:
            batch["source"] = _source(raw["source"])
        if "background" in raw:
            batch["background"] = _rgb(raw["background"], "batch background")
        result.append(batch)
    return result


def _telemetry(value: Any) -> dict[str, Any]:
    raw = _object(value, "telemetry")
    return {
        "attempts": _integer(raw.get("attempts"), 0, _SAFE_INTEGER, "telemetry attempts"),
        "duration_ms": _integer(raw.get("duration_ms"), 0, _SAFE_INTEGER, "telemetry duration"),
        "initial_score": None if raw.get("initial_score") is None else _number(raw["initial_score"], "initial score"),
        "batches": _batches(raw.get("batches")),
    }


def _result_metadata(value: Any) -> dict[str, Any]:
    raw = _object(value, "result")
    inspect_scene(raw)
    result = {key: _dimension(raw.get(key), key) for key in ("width", "height", "render_width", "render_height")}
    preview = raw.get("preview_data_url")
    result["preview_data_url"] = None if preview is None else _raster_url(preview, "result preview")
    result["background"] = None if raw.get("background") is None else _color(raw["background"], "background")
    result["shapes"] = raw["shapes"]
    retained = raw.get("restored_shape_count")
    result["restored_shape_count"] = _integer(
        0 if retained is None else retained, 0, len(result["shapes"]), "retained count",
    )
    if "target_digest" in raw:
        digest = raw["target_digest"]
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ProjectError("Project target digest must be 64 hexadecimal characters")
        result["target_digest"] = digest.lower()
    return result


def _result(value: Any) -> dict[str, Any]:
    result = _result_metadata(value)
    shapes = []
    for shape in result["shapes"]:
        shape = {**shape, "color": _color(shape.get("color"), "shape color")}
        if "type_id" in shape:
            shape["type_id"] = _integer(shape["type_id"], 0, 2**31 - 1, "shape type id")
        shapes.append(shape)
    result["shapes"] = validate_shapes(shapes, *_grid(result))
    return result


def _grid(result: dict[str, Any]) -> tuple[int, int]:
    return result["render_width"] or result["width"] or 0, result["render_height"] or result["height"] or 0


def _name(value: Any) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if not value or _utf16_length(value) > 80:
        raise ProjectError("Experiment name must contain 1 to 80 characters")
    return value


def _records(raw: dict[str, Any]) -> tuple[str, int, list[dict[str, Any]]]:
    history = raw.get("history")
    if history is None:
        return "experiment-1", len(raw["result"]["shapes"]), [{
            "id": "experiment-1", "name": "Original", "parent_id": None, "fork_shape_count": 0,
            "options": raw["options"], "result": raw["result"], "telemetry": raw["telemetry"],
        }]
    history = _object(history, "history")
    if set(history) - {"active_branch", "view_shape_count", "branches"}:
        raise ProjectError("Project history contains an unknown field")
    branches = history.get("branches")
    if not isinstance(branches, list) or not 1 <= len(branches) <= PROJECT_MAX_BRANCHES:
        raise ProjectError(f"Project history must contain 1 to {PROJECT_MAX_BRANCHES} experiments")
    result = []
    for item in branches:
        branch = _object(item, "experiment")
        if set(branch) - _BRANCH_FIELDS:
            raise ProjectError("Project experiment contains an unknown field")
        active = branch.get("id") == history.get("active_branch")
        if active and ("result" in branch or "telemetry" in branch):
            raise ProjectError("Project active experiment uses top-level result and telemetry")
        result.append({**branch, **({"result": raw["result"], "telemetry": raw["telemetry"]} if active else {})})
    return history.get("active_branch"), history.get("view_shape_count"), result


def _preflight(records: list[dict[str, Any]]) -> tuple[int, int]:
    shapes = points = 0
    for record in records:
        count, vertices = inspect_scene(_object(record.get("result"), "experiment result"))
        shapes += count
        points += vertices
        telemetry = _object(record.get("telemetry"), "telemetry")
        batches = telemetry.get("batches")
        if not isinstance(batches, list) or len(batches) > PROJECT_MAX_BATCHES:
            raise ProjectError(f"Project batches must contain at most {PROJECT_MAX_BATCHES} items")
    if shapes > PROJECT_MAX_HISTORY_SHAPES or points > PROJECT_MAX_HISTORY_POINTS:
        raise ProjectError("Project experiments exceed the total shape or polyline point budget")
    return shapes, points


def _graph(active: str, cursor: int, branches: list[dict[str, Any]]) -> None:
    if not isinstance(active, str) or _BRANCH_ID.fullmatch(active) is None:
        raise ProjectError("Project active experiment must be an experiment ID")
    ids = {}
    names = set()
    for branch in branches:
        identifier = branch.get("id")
        if not isinstance(identifier, str) or _BRANCH_ID.fullmatch(identifier) is None or identifier in ids:
            raise ProjectError(
                "Project experiment IDs must be unique ASCII letters, digits, _ or - (1 to 64 characters)",
            )
        branch["name"] = _name(branch.get("name"))
        name = branch["name"].lower()
        if name in names:
            raise ProjectError("Project experiment names must be unique")
        names.add(name)
        ids[identifier] = branch
        parent = branch.get("parent_id")
        if parent is not None and (not isinstance(parent, str) or _BRANCH_ID.fullmatch(parent) is None):
            raise ProjectError("Project experiment parent must be an experiment ID or null")
        if "parent_id" not in branch:
            raise ProjectError("Project experiment parent must be an experiment ID or null")
        branch["fork_shape_count"] = _integer(
            branch.get("fork_shape_count"), 0, len(branch["result"]["shapes"]), "fork shape count",
        )
    for branch in branches:
        parent_id = branch["parent_id"]
        parent = ids.get(parent_id)
        if parent_id is not None and parent is None:
            raise ProjectError("Project experiment parent does not exist")
        if branch["fork_shape_count"] > (len(parent["result"]["shapes"]) if parent else 0):
            raise ProjectError("Project fork shape count exceeds its parent")
        if parent:
            if (_grid(branch["result"]) != _grid(parent["result"])
                    or branch["result"]["background"] != parent["result"]["background"]):
                raise ProjectError("Project fork grid and background must match its parent")
            if branch["options"]["source"] != parent["options"]["source"]:
                raise ProjectError("Project fork source policy must match its parent")
            digest, parent_digest = branch["result"].get("target_digest"), parent["result"].get("target_digest")
            if digest and parent_digest and digest != parent_digest:
                raise ProjectError("Project fork target digest must match its parent")
        seen = {branch["id"]}
        while parent is not None:
            if parent["id"] in seen:
                raise ProjectError("Project experiment parents contain a cycle")
            seen.add(parent["id"])
            parent = ids.get(parent["parent_id"])
    if active not in ids:
        raise ProjectError("Project active experiment does not exist")
    _integer(cursor, 0, len(ids[active]["result"]["shapes"]), "inspected shape count")


def validate_project(raw: Any) -> dict[str, Any]:
    """Validate all branch geometry and migrate v1/v2/v3 to a v3 wire model."""
    try:
        return _validate_project(raw)
    except ProjectError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise ProjectError(str(exc)) from exc


def _validate_project(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        raise ProjectError("This is a Shapes JSON export, not a Geometrize project")
    raw = _object(raw, "project")
    if raw.get("format") != PROJECT_FORMAT:
        raise ProjectError("This JSON file is not a Geometrize project")
    version = _integer(raw.get("version"), 1, PROJECT_VERSION, "version")
    if version == 1 and raw.get("history") is not None:
        raise ProjectError("Version 1 projects cannot contain experiments")
    _object(raw.get("options"), "options")
    _object(raw.get("telemetry"), "telemetry")
    _object(raw.get("result"), "result")
    inspect_scene(raw["result"])
    active, cursor, records = _records(raw)
    _preflight(records)
    options = _options(raw.get("options"))
    branches = []
    for record in records:
        branch_options = _options(record.get("options"))
        branches.append({
            **{key: record.get(key) for key in ("id", "name", "parent_id", "fork_shape_count")},
            "options": deepcopy(options) if record.get("id") == active else branch_options,
            "result": _result_metadata(record.get("result")), "telemetry": record.get("telemetry"),
        })
        if "parent_id" not in record:
            raise ProjectError("Project experiment parent must be an experiment ID or null")
    _graph(active, cursor, branches)
    for branch in branches:
        branch["result"] = _result(branch["result"])
        branch["telemetry"] = _telemetry(branch["telemetry"])
    source = _object(raw.get("source"), "source")
    name = source.get("name")
    source = {"name": (name if isinstance(name, str) and 0 < len(name) <= 512 and _utf16_length(name) <= 512
                       else "Source image"),
              "data_url": _raster_url(source.get("data_url"), "source image"),
              "width": _dimension(source.get("width"), "source width"),
              "height": _dimension(source.get("height"), "source height")}
    head = next(branch for branch in branches if branch["id"] == active)
    return {
        "format": PROJECT_FORMAT, "version": PROJECT_VERSION, "source": source,
        "options": options, "result": head["result"], "telemetry": head["telemetry"],
        "history": {"active_branch": active, "view_shape_count": int(cursor), "branches": [
            {key: value for key, value in branch.items()
             if branch["id"] != active or key not in {"result", "telemetry"}}
            for branch in branches
        ]},
    }


def loads_project(content: str | bytes) -> dict[str, Any]:
    """Parse already-admitted bounded JSON once; no image decode or native work."""
    try:
        if isinstance(content, bytes):
            if len(content) > PROJECT_MAX_BYTES:
                raise ProjectError("Project files must be 64 MiB or smaller")
            text = content.decode("utf-8-sig")
        elif isinstance(content, str):
            size = len(content) if content.isascii() else len(content.encode("utf-8"))
            if size > PROJECT_MAX_BYTES:
                raise ProjectError("Project files must be 64 MiB or smaller")
            text = content[1:] if content.startswith("\ufeff") else content
        else:
            raise ProjectError("Project files must be 64 MiB or smaller")
        raw = json.loads(text, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("Project file is not valid JSON") from exc
    return validate_project(raw)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


def load_project(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as handle:
        return loads_project(handle.read(PROJECT_MAX_BYTES + 1))


def _branch_view(project: dict[str, Any], branch_id: str | None) -> dict[str, Any]:
    history = project["history"]
    branch_id = history["active_branch"] if branch_id is None else branch_id
    branch = next((item for item in history["branches"] if item["id"] == branch_id), None)
    if branch is None:
        raise ProjectError(f"Project experiment does not exist: {branch_id}", code="unknown_branch")
    if branch_id == history["active_branch"]:
        branch = {**branch, **{key: project[key] for key in ("options", "result", "telemetry")}}
    return branch


def project_branch(project: dict[str, Any], branch_id: str | None = None) -> dict[str, Any]:
    """Return a detached full branch from a validated project, defaulting to active."""
    return deepcopy(_branch_view(project, branch_id))


def project_scene(project: dict[str, Any], branch_id: str | None = None, shape_count: int | None = None) -> RenderScene:
    """Select a complete geometry scene, defaulting to the branch's full head."""
    branch = _branch_view(project, branch_id)
    result = branch["result"]
    width, height = _grid(result)
    if not width or not height or result["background"] is None:
        raise ProjectError("This experiment has no reconstruction geometry; only its saved preview can be inspected",
                           code="missing_geometry")
    count = (len(result["shapes"]) if shape_count is None
             else _integer(shape_count, 0, len(result["shapes"]), "shape count"))
    raw = {"width": width, "height": height, "background": result["background"], "shapes": result["shapes"][:count],
           "attempts": branch["telemetry"]["attempts"], "revision": count}
    if "target_digest" in result:
        raw["target_digest"] = result["target_digest"]
    return validate_scene(raw)


def _snapshot(snapshot: dict[str, Any], telemetry: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = _object(snapshot, "snapshot")
    raw = {**snapshot, "preview_data_url": snapshot.get("preview_data_url", snapshot.get("preview"))}
    result = _result(raw)
    if telemetry is None:
        summary = snapshot.get("batch_summary")
        telemetry = {"attempts": snapshot.get("attempts", 0), "duration_ms": snapshot.get("duration_ms", 0),
                     "initial_score": snapshot.get("initial_score"), "batches": [summary] if summary else []}
    return result, _telemetry(telemetry)


def create_project(source: dict[str, Any], options: RunOptions | dict[str, Any], snapshot: dict[str, Any],
                   telemetry: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(options, dict):
        options = options.to_dict()
    result, telemetry = _snapshot(snapshot, telemetry)
    return validate_project({"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "source": source,
                             "options": options, "result": result, "telemetry": telemetry})


def fork_project(project: dict[str, Any], *, name: str, branch_id: str | None = None, shape_count: int | None = None,
                 overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a logical child with retained geometry and fresh experiment telemetry."""
    project = validate_project(project)
    parent = _branch_view(project, branch_id)
    count = (len(parent["result"]["shapes"]) if shape_count is None
             else _integer(shape_count, 0, len(parent["result"]["shapes"]), "shape count"))
    history = project["history"]
    name = _name(name)
    if len(history["branches"]) >= PROJECT_MAX_BRANCHES:
        raise ProjectError("Project experiment limit reached")
    if any(branch["name"].lower() == name.lower() for branch in history["branches"]):
        raise ProjectError("Project experiment names must be unique")
    overrides = {} if overrides is None else _object(overrides, "fit overrides")
    if set(overrides) - OPTION_DEFAULTS.keys():
        raise ProjectError("Project fit overrides contain an unknown field")
    options = _options({**parent["options"], **overrides})
    for key in ("source", "background", "max_size"):
        if options[key] != parent["options"][key]:
            raise ProjectError(f"Fork {key} is frozen; start a new reconstruction to change it", code="frozen_target")
    shape_total, point_total = _preflight(_records(project)[2])
    _, points = inspect_scene({"shapes": parent["result"]["shapes"][:count]})
    if shape_total + count > PROJECT_MAX_HISTORY_SHAPES or point_total + points > PROJECT_MAX_HISTORY_POINTS:
        raise ProjectError("Project experiments exceed the total shape or polyline point budget")
    scene = project_scene(project, parent["id"], count)
    old_active = next(branch for branch in history["branches"] if branch["id"] == history["active_branch"])
    old_active.update(result=project["result"], telemetry=project["telemetry"])
    index = 1
    while any(branch["id"] == f"experiment-{index}" for branch in history["branches"]):
        index += 1
    child_id = f"experiment-{index}"
    result = {**parent["result"], "shapes": scene.shapes, "preview_data_url": None, "restored_shape_count": count}
    history["branches"].append({"id": child_id, "name": name, "parent_id": parent["id"],
                                "fork_shape_count": count, "options": deepcopy(options)})
    history.update(active_branch=child_id, view_shape_count=count)
    project.update(options=options, result=result,
                   telemetry={"attempts": 0, "duration_ms": 0, "initial_score": None, "batches": []})
    _preflight(_records(project)[2])
    _graph(history["active_branch"], history["view_shape_count"], _records(project)[2])
    return project


def replace_branch_snapshot(project: dict[str, Any], branch_id: str, snapshot: dict[str, Any],
                            telemetry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Install a confirmed native head without changing its frozen target policy."""
    project = validate_project(project)
    branch = _branch_view(project, branch_id)
    result = _result_metadata({
        **snapshot, "preview_data_url": snapshot.get("preview_data_url", snapshot.get("preview")),
    })
    if _grid(result) != _grid(branch["result"]) or result["background"] != branch["result"]["background"]:
        raise ProjectError("Confirmed snapshot must keep the experiment grid and actual background",
                           code="frozen_target")
    old_digest, digest = branch["result"].get("target_digest"), result.get("target_digest")
    if old_digest and digest and old_digest != digest:
        raise ProjectError("Confirmed snapshot must keep the experiment target digest", code="frozen_target")
    if old_digest:
        result["target_digest"] = old_digest
    if "source" in snapshot and _source(snapshot["source"]) != branch["options"]["source"]:
        raise ProjectError("Confirmed snapshot must keep the experiment source policy", code="frozen_target")
    total_shapes, total_points = _preflight(_records(project)[2])
    old_shapes, old_points = inspect_scene(branch["result"])
    new_shapes, new_points = inspect_scene(result)
    if (total_shapes - old_shapes + new_shapes > PROJECT_MAX_HISTORY_SHAPES
            or total_points - old_points + new_points > PROJECT_MAX_HISTORY_POINTS):
        raise ProjectError("Project experiments exceed the total shape or polyline point budget")
    result, telemetry = _snapshot(result, telemetry if telemetry is not None else {
        "attempts": snapshot.get("attempts", 0), "duration_ms": snapshot.get("duration_ms", 0),
        "initial_score": snapshot.get("initial_score"),
        "batches": [snapshot["batch_summary"]] if snapshot.get("batch_summary") else [],
    })
    if branch_id == project["history"]["active_branch"]:
        project.update(result=result, telemetry=telemetry)
        project["history"]["view_shape_count"] = len(result["shapes"])
    else:
        record = next(item for item in project["history"]["branches"] if item["id"] == branch_id)
        record.update(result=result, telemetry=telemetry)
    history = project["history"]
    records = _records(project)[2]
    _preflight(records)
    _graph(history["active_branch"], history["view_shape_count"], records)
    return project


def dumps_project(project: dict[str, Any]) -> str:
    """Serialize canonical v3 JSON, pruning only regenerable previews if needed."""
    project = validate_project(project)

    def encode() -> str:
        return json.dumps(project, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"

    content = encode()
    if len(content) <= PROJECT_MAX_BYTES:
        return content
    for branch in project["history"]["branches"]:
        result = project["result"] if branch["id"] == project["history"]["active_branch"] else branch["result"]
        width, height = _grid(result)
        if width and height and result["background"] is not None:
            result["preview_data_url"] = None
    content = encode()
    if len(content) > PROJECT_MAX_BYTES:
        raise ProjectError("Project exceeds 64 MiB after omitting generated previews")
    return content
