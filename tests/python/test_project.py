from __future__ import annotations

import copy
import json
import sys

import pytest

from geometrize_py import exporting, native, project
from geometrize_py.contracts import OPTION_DEFAULTS, PROJECT_VERSION
from geometrize_py.exporting import build_export
from geometrize_py.native import RunOptions
from geometrize_py.project import (
    ProjectError,
    create_project,
    dumps_project,
    fork_project,
    load_project,
    loads_project,
    project_branch,
    project_scene,
    replace_branch_snapshot,
    validate_project,
)

_SOURCE = "DATA:IMAGE/PNG;BASE64,YW\r\nJj"


def _shape(**data):
    return {"type": "rectangle", "data": {"x1": 1, "y1": 2, "x2": 10, "y2": 12, **data},
            "color": [20, 40, 60, 128], "score": 0.25, "type_id": 1}


def _raw(version=3, *, complete=True):
    raw = {
        "format": "geometrize-project", "version": version, "saved_at": "ignored",
        "source": {"name": "Source", "data_url": _SOURCE, "width": 640, "height": 480},
        "options": {**copy.deepcopy(OPTION_DEFAULTS), "shape_types": ["rectangle"], "max_size": 64, "export_size": 64},
        "result": {"width": 64 if complete else None, "height": 48 if complete else None,
                   "background": [10, 20, 30, 255] if complete else None,
                   "shapes": [_shape(), _shape(x1=20, x2=30)] if complete else [], "preview_data_url": None},
        "telemetry": {"attempts": 9, "duration_ms": 100, "initial_score": 0.9, "batches": []},
    }
    raw["history"] = {"active_branch": "experiment-1", "view_shape_count": len(raw["result"]["shapes"]), "branches": [
        {"id": "experiment-1", "name": "Original", "parent_id": None, "fork_shape_count": 0,
         "options": copy.deepcopy(raw["options"])},
    ]}
    return raw


def _graph():
    raw = _raw()
    raw["history"] = {"active_branch": "child", "view_shape_count": 1, "branches": [
        {"id": "parent", "name": "Original", "parent_id": None, "fork_shape_count": 0,
         "options": copy.deepcopy(raw["options"]), "result": copy.deepcopy(raw["result"]),
         "telemetry": copy.deepcopy(raw["telemetry"])},
        {"id": "child", "name": "Child", "parent_id": "parent", "fork_shape_count": 1,
         "options": copy.deepcopy(raw["options"])},
    ]}
    return raw


@pytest.mark.parametrize("complete", [True, False])
def test_current_projects_validate_idempotently_without_reencoding_source(complete) -> None:
    raw = _raw(complete=complete)
    original = copy.deepcopy(raw)
    normalized = validate_project(raw)
    assert normalized["version"] == PROJECT_VERSION
    assert normalized["source"]["data_url"] == _SOURCE
    assert normalized["options"]["source"] == {"frame": 0, "matte": None}
    assert normalized["options"]["background"] is None
    assert normalized["result"]["width"] == (64 if complete else None)
    assert "saved_at" not in normalized
    assert loads_project(dumps_project(normalized)) == normalized
    assert raw == original


@pytest.mark.parametrize("version", [None, True, 0, 1, 2, 4, "3"])
def test_unsupported_project_versions_reject_before_geometry_validation(version, monkeypatch) -> None:
    raw = _raw(version)
    monkeypatch.setattr(project, "validate_shapes", lambda *_args: pytest.fail("Unsupported project copied geometry"))
    with pytest.raises(ProjectError, match="only version 3") as error:
        validate_project(raw)
    assert error.value.code == "unsupported_version"


@pytest.mark.parametrize("history", [None, {}, []])
def test_current_projects_require_an_explicit_experiment_graph(history) -> None:
    raw = _raw()
    raw["history"] = history
    with pytest.raises(ProjectError, match="history"):
        validate_project(raw)


def test_active_top_level_options_are_authoritative_before_source_graph_comparison() -> None:
    raw = _graph()
    source = {"frame": 1, "matte": [255, 255, 255]}
    raw["options"]["source"] = source
    raw["history"]["branches"][0]["options"]["source"] = source
    raw["history"]["branches"][1]["options"]["source"] = {"frame": 0, "matte": None}
    normalized = validate_project(raw)
    active = project_branch(normalized)
    assert active["options"] == normalized["options"]
    assert active["options"]["source"] == source
    active["options"]["source"]["matte"][0] = 0
    assert normalized["options"]["source"]["matte"] == [255, 255, 255]
    raw["history"]["branches"][1]["options"]["seed"] = False
    with pytest.raises(ProjectError, match="seed"):
        validate_project(raw)


def test_omitted_project_options_use_independent_defaults_without_clamping() -> None:
    raw = _raw()
    raw["options"] = {"shape_types": ["circle", "circle", "ellipse"], "max_size": 256}
    normalized = validate_project(raw)
    assert normalized["options"]["steps"] == OPTION_DEFAULTS["steps"]
    assert normalized["options"]["seed"] == OPTION_DEFAULTS["seed"]
    assert normalized["options"]["export_size"] == OPTION_DEFAULTS["export_size"]
    assert normalized["options"]["shape_types"] == ["circle", "ellipse"]
    for value in (None, False, 5000):
        raw["options"]["steps"] = value
        with pytest.raises(ProjectError, match="steps"):
            validate_project(raw)


def test_integral_json_decimals_match_browser_numbers_without_weakening_native_options() -> None:
    raw = _graph()
    raw["version"] = 3.0
    raw["source"].update(width=640.0, height=480.0)
    raw["result"].update(width=64.0, height=48.0, background=[10.0, 20.0, 30.0, 255.0], restored_shape_count=1.0)
    raw["result"]["shapes"][0].update(type_id=1.0, color=[20.0, 40.0, 60.0, 128.0])
    raw["options"].update(seed=8.0, palette={"colors": [[1.0, 2.0, 3.0]], "strength": 0.5},
                          source={"frame": 0.0, "matte": [255.0, 255.0, 255.0]}, background=[9.0, 19.0, 29.0])
    raw["history"]["branches"][0]["options"]["source"] = raw["options"]["source"]
    raw["history"].update(view_shape_count=1.0)
    raw["history"]["branches"][1]["fork_shape_count"] = 1.0
    raw["telemetry"].update(attempts=9.0, duration_ms=100.0)
    raw["telemetry"]["batches"] = [{
        "index": 1.0, "target": 2.0, "shapeTypes": ["rectangle"], "candidates": 4.0,
        "mutations": 8.0, "alpha": 128.0, "added": 2.0, "attempts": 9.0, "seed": 8.0,
        "effective_threads": 1.0, "max_threads": 1.0, "start_shape_count": 0.0, "start_attempts": 0.0,
        "source": raw["options"]["source"], "palette": raw["options"]["palette"], "background": [9.0, 19.0, 29.0],
    }]
    normalized = loads_project(json.dumps(raw))
    assert type(normalized["options"]["seed"]) is int
    assert normalized["options"]["source"] == {"frame": 0, "matte": [255, 255, 255]}
    assert all(type(channel) is int for channel in normalized["options"]["palette"]["colors"][0])
    assert type(normalized["result"]["shapes"][0]["type_id"]) is int
    assert type(normalized["history"]["view_shape_count"]) is int
    batch = normalized["telemetry"]["batches"][0]
    assert all(type(batch[key]) is int for key in ("index", "seed", "effective_threads"))
    assert validate_project(normalized) == normalized
    with pytest.raises(ValueError):
        RunOptions(seed=8.0)


@pytest.mark.parametrize("changes", [
    {"shape_types": "circle"}, {"shape_types": [{}]}, {"alpha": True}, {"seed": 10**10000},
    {"max_size": None, "export_size": None}, {"focus": {"x": False, "y": 0}},
    {"unsupported_option": True},
    {"source": {"frame": None}}, {"palette": {"colors": [[0, 0, 0]], "strength": None}},
])
def test_project_options_reject_invalid_values(changes) -> None:
    raw = _raw()
    raw["options"].update(changes)
    with pytest.raises(ProjectError):
        validate_project(raw)


def test_empty_ui_drafts_remain_inspectable_without_invented_geometry() -> None:
    raw = _raw(complete=False)
    raw["options"]["shape_types"] = []
    raw["result"]["shapes"] = []
    normalized = validate_project(raw)
    assert project_branch(normalized)["options"]["shape_types"] == []
    assert normalized["result"]["background"] is None
    assert loads_project(dumps_project(normalized)) == normalized
    with pytest.raises(ProjectError) as error:
        project_scene(normalized)
    assert error.value.code == "missing_geometry"
    with pytest.raises(ValueError, match="At least one"):
        RunOptions(**normalized["options"])


@pytest.mark.parametrize("axis", ["width", "height"])
def test_fitting_grid_uses_each_render_dimension_independently(axis) -> None:
    raw = _raw()
    raw["result"].update(width=400, height=200, **{f"render_{axis}": 100})
    normalized = validate_project(raw)
    scene = project_scene(normalized)
    assert (scene.width, scene.height) == ((100, 200) if axis == "width" else (400, 100))
    assert normalized["source"]["width"] == 640
    assert normalized["result"]["width"] == 400


def test_native_free_scene_export_selects_head_by_default_and_explicit_prefix(monkeypatch) -> None:
    monkeypatch.setattr(native, "_native", None)
    normalized = validate_project(_graph())
    assert normalized["history"]["view_shape_count"] == 1
    assert len(project_scene(normalized).shapes) == 2
    prefix = project_scene(normalized, shape_count=1)
    assert prefix.revision == 1 and prefix.attempts == 9
    artifact = build_export(prefix, 32)
    assert artifact.png.startswith(b"\x89PNG") and "<svg" in artifact.svg
    with pytest.raises(ProjectError):
        project_scene(normalized, shape_count=True)
    with pytest.raises(ProjectError) as error:
        project_branch(normalized, "unknown")
    assert error.value.code == "unknown_branch"


@pytest.mark.parametrize("broken", ["coordinate", "color", "score", "type-id", "radius", "points"])
def test_every_inactive_shape_is_validated_on_load(broken) -> None:
    raw = _graph()
    result = raw["history"]["branches"][0]["result"]
    shape = result["shapes"][0]
    if broken == "coordinate":
        shape["data"]["x1"] = 10**20
    elif broken == "color":
        shape["color"] = [0, False, 0, 255]
    elif broken == "score":
        shape["score"] = float("nan")
    elif broken == "type-id":
        shape["type_id"] = -1
    elif broken == "radius":
        result["shapes"][0] = {"type": "circle", "color": [0, 0, 0, 255], "data": {"x": 0, "y": 0, "r": -1}}
    else:
        result["shapes"][0] = {"type": "polyline", "color": [0, 0, 0, 255], "data": {"points": [[False, 0]]}}
    with pytest.raises(ProjectError):
        validate_project(raw)


@pytest.mark.parametrize("broken", [
    "duplicate-id", "duplicate-name", "unknown-parent", "cycle", "active-array", "cursor-bool", "fork-count",
    "active-snapshot", "source", "grid", "background", "digest",
])
def test_invalid_graph_metadata_rejects_before_geometry_copy(monkeypatch, broken) -> None:
    raw = _graph()
    parent, child = raw["history"]["branches"]
    if broken == "duplicate-id":
        parent["id"] = child["id"]
    elif broken == "duplicate-name":
        child["name"] = " original "
    elif broken == "unknown-parent":
        child["parent_id"] = "missing"
    elif broken == "cycle":
        parent["parent_id"] = child["id"]
    elif broken == "active-array":
        raw["history"]["active_branch"] = []
    elif broken == "cursor-bool":
        raw["history"]["view_shape_count"] = False
    elif broken == "fork-count":
        child["fork_shape_count"] = 3
    elif broken == "active-snapshot":
        child["result"] = None
    elif broken == "source":
        parent["options"]["source"] = {"frame": 1, "matte": None}
    elif broken == "grid":
        parent["result"]["render_width"] = 32
    elif broken == "background":
        parent["result"]["background"] = [0, 0, 0, 255]
    else:
        parent["result"]["target_digest"] = "a" * 64
        raw["result"]["target_digest"] = "b" * 64
    monkeypatch.setattr(project, "validate_shapes", lambda *_args: pytest.fail("Invalid graph copied geometry"))
    with pytest.raises(ProjectError):
        validate_project(raw)


def test_optional_digest_and_case_normalization_do_not_change_graph_identity() -> None:
    raw = _graph()
    raw["result"]["target_digest"] = "a" * 64
    raw["history"]["branches"][0]["result"]["target_digest"] = "A" * 64
    assert validate_project(raw)["result"]["target_digest"] == "a" * 64
    del raw["history"]["branches"][0]["result"]["target_digest"]
    assert validate_project(raw)["result"]["target_digest"] == "a" * 64


@pytest.mark.parametrize("budget", ["shapes", "points", "branches", "batches"])
def test_resource_caps_precede_geometry_copies(monkeypatch, budget) -> None:
    raw = _graph()
    if budget == "shapes":
        monkeypatch.setattr(project, "PROJECT_MAX_HISTORY_SHAPES", 3)
    elif budget == "points":
        polyline = {"type": "polyline", "color": [0, 0, 0, 255], "data": {"points": [[0, 0], [1, 1]]}}
        raw["result"]["shapes"] = [polyline]
        raw["history"]["branches"][0]["result"]["shapes"] = [polyline]
        monkeypatch.setattr(project, "PROJECT_MAX_HISTORY_POINTS", 3)
    elif budget == "branches":
        monkeypatch.setattr(project, "PROJECT_MAX_BRANCHES", 1)
    else:
        raw["telemetry"]["batches"] = [{}, {}]
        monkeypatch.setattr(project, "PROJECT_MAX_BATCHES", 1)
    monkeypatch.setattr(project, "validate_shapes", lambda *_args: pytest.fail("Over-budget scene copied geometry"))
    with pytest.raises(ProjectError):
        validate_project(raw)


def test_per_scene_polyline_cap_uses_existing_shared_validator(monkeypatch) -> None:
    raw = _raw()
    raw["result"]["shapes"] = [{"type": "polyline", "color": [0, 0, 0, 255], "data": {"points": [[0, 0], [1, 1]]}}]
    monkeypatch.setattr(exporting, "PROJECT_MAX_POINTS", 1)
    with pytest.raises(ProjectError, match="polyline points"):
        validate_project(raw)


@pytest.mark.parametrize("retained", [False, -1, 3, "1"])
def test_retained_shape_count_remains_strict(retained) -> None:
    raw = _raw()
    raw["result"]["restored_shape_count"] = retained
    with pytest.raises(ProjectError):
        validate_project(raw)


def test_json_reads_reject_oversize_invalid_and_nonfinite_content_before_validation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "project.json"
    path.write_text(dumps_project(validate_project(_raw())), encoding="utf-8")
    assert load_project(path) == loads_project(path.read_bytes())
    for content in (b"{}", b"[{}]", b'{"value":NaN}', b"\xff"):
        with pytest.raises(ProjectError):
            loads_project(content)
    monkeypatch.setattr(project, "PROJECT_MAX_BYTES", 128)
    monkeypatch.setattr(project.json, "loads", lambda *_args, **_kwargs: pytest.fail("Oversized JSON parsed"))
    with pytest.raises(ProjectError, match="64 MiB"):
        loads_project(b" " * 129)


def test_json_loader_parses_admitted_ascii_text_directly_and_decodes_utf8_bytes_once(monkeypatch) -> None:
    content = dumps_project(validate_project(_raw()))
    actual_loads = json.loads
    parsed = []

    def observe(value, **kwargs):
        assert isinstance(value, str)
        parsed.append(value)
        return actual_loads(value, **kwargs)

    monkeypatch.setattr(project.json, "loads", observe)
    expected = loads_project(content)
    assert parsed[-1] is content
    assert loads_project(content.encode("utf-8")) == expected
    assert loads_project(b"\xef\xbb\xbf" + content.encode("utf-8")) == expected
    assert loads_project("\ufeff" + content) == expected
    assert len(parsed) == 4
    for encoded in (content.encode("utf-16"), content.encode("utf-32"), b"\xff"):
        with pytest.raises(ProjectError, match="valid JSON"):
            loads_project(encoded)
    assert len(parsed) == 4


@pytest.mark.parametrize("content", [b" " * 129, " " * 129, "\u00e9" * 65])
def test_json_loader_bounds_original_utf8_bytes_before_parsing(content, monkeypatch) -> None:
    monkeypatch.setattr(project, "PROJECT_MAX_BYTES", 128)
    monkeypatch.setattr(project.json, "loads", lambda *_args, **_kwargs: pytest.fail("Oversized JSON parsed"))
    with pytest.raises(ProjectError, match="64 MiB"):
        loads_project(content)


def test_json_loader_rejects_deep_nonproject_content_and_invalid_unicode() -> None:
    depth = sys.getrecursionlimit() * 2
    for content in ("[" * depth + "0" + "]" * depth, "\ud800", b'{"value":Infinity}'):
        with pytest.raises(ProjectError):
            loads_project(content)


def test_project_names_use_browser_utf16_limits_and_serialize_unicode_safely() -> None:
    original = validate_project(_raw())
    accepted = fork_project(original, name="\U0001f31f" * 40, shape_count=0)
    assert project_branch(accepted)["name"] == "\U0001f31f" * 40
    assert loads_project(dumps_project(accepted)) == accepted
    with pytest.raises(ProjectError, match="1 to 80"):
        fork_project(original, name="\U0001f31f" * 40 + "x", shape_count=0)
    raw = _raw()
    raw["source"]["name"] = "\U0001f31f" * 256
    assert validate_project(raw)["source"]["name"] == raw["source"]["name"]
    raw["source"]["name"] += "x"
    assert validate_project(raw)["source"]["name"] == "Source image"
    raw["source"]["name"] = "legacy\ud800"
    normalized = validate_project(raw)
    assert dumps_project(normalized).isascii()
    assert loads_project(dumps_project(normalized)) == normalized


def test_batch_text_limits_do_not_split_non_bmp_characters() -> None:
    raw = _raw()
    raw["telemetry"]["batches"] = [{
        "index": 1, "target": 2, "shapeTypes": ["rectangle"], "candidates": 4,
        "mutations": 8, "alpha": 128, "added": 2, "attempts": 9,
        "state": "a" * 39 + "\U0001f31f", "reason": "b" * 79 + "\U0001f31f",
    }]
    batch = validate_project(raw)["telemetry"]["batches"][0]
    assert batch["state"] == "a" * 39
    assert batch["reason"] == "b" * 79


def test_requested_options_mapping_and_confirmed_session_fields_create_a_canonical_project() -> None:
    options = RunOptions(max_threads=0, max_size=64, export_size=128, stagnation_limit=8,
                         focus={"x": 0.2, "y": 0.8}, palette={"colors": [[1, 2, 3]], "strength": 1},
                         source={"frame": 1, "matte": [255, 255, 255]}, background=[9, 19, 29])
    mapping = options.to_dict()
    assert mapping["max_threads"] == 0 and options.to_native_dict()["max_threads"] >= 1
    assert RunOptions(**mapping) == options
    raw = _raw()
    snapshot = {**raw["result"], "attempts": 3, "initial_score": 0.75, "session_id": "ephemeral",
                "target_digest": "a" * 64}
    result = create_project(raw["source"], options, snapshot)
    assert result["options"] == mapping
    assert result["telemetry"]["attempts"] == 3
    assert result["result"]["target_digest"] == "a" * 64
    assert "ephemeral" not in dumps_project(result)
    mapping["palette"]["colors"][0][0] = 255
    assert options.palette.colors == ((1, 2, 3),)


def test_prefix_fork_is_pure_and_resets_experiment_telemetry_while_retaining_parent() -> None:
    original = validate_project(_graph())
    before = copy.deepcopy(original)
    child = fork_project(original, name="  New experiment  ", shape_count=1, overrides={"seed": 42})
    branch = project_branch(child)
    assert branch["name"] == "New experiment" and branch["parent_id"] == "child"
    assert branch["fork_shape_count"] == child["result"]["restored_shape_count"] == 1
    assert child["telemetry"] == {"attempts": 0, "duration_ms": 0, "initial_score": None, "batches": []}
    assert child["result"]["preview_data_url"] is None
    assert child["options"]["seed"] == 42
    assert child["options"]["steps"] == original["options"]["steps"]
    assert len(project_scene(fork_project(original, name="Whole head")).shapes) == 2
    assert project_branch(child, "child")["result"] == original["result"]
    assert original == before
    assert loads_project(dumps_project(child)) == child


@pytest.mark.parametrize("override", [{"max_size": 128}, {"background": [0, 0, 0]},
                                       {"source": {"frame": 1}}, {"source": {"matte": [0, 0, 0]}}])
def test_fork_rejects_frozen_target_creation_changes(override) -> None:
    original = validate_project(_raw())
    before = copy.deepcopy(original)
    with pytest.raises(ProjectError) as error:
        fork_project(original, name="Changed target", overrides=override)
    assert error.value.code == "frozen_target" and original == before


def test_fork_limits_names_and_snapshot_target_identity_are_enforced(monkeypatch) -> None:
    original = validate_project(_raw())
    with pytest.raises(ProjectError, match="unique"):
        fork_project(original, name=" ORIGINAL ")
    monkeypatch.setattr(project, "PROJECT_MAX_HISTORY_SHAPES", 3)
    with pytest.raises(ProjectError, match="budget"):
        fork_project(original, name="Too many shapes")
    child = fork_project(original, name="Prefix", shape_count=1)
    identifier = child["history"]["active_branch"]
    snapshot = {**child["result"], "attempts": 0, "initial_score": 0.9}
    updated = replace_branch_snapshot(child, identifier, snapshot)
    assert updated["telemetry"]["initial_score"] == 0.9
    for changes in ({"width": 32}, {"background": [0, 0, 0, 255]}, {"source": {"frame": 1}}):
        with pytest.raises(ProjectError) as error:
            replace_branch_snapshot(child, identifier, {**snapshot, **changes})
        assert error.value.code == "frozen_target"


def test_snapshot_replacement_preserves_digest_and_updates_only_its_detached_branch() -> None:
    original = validate_project(_graph())
    original["result"]["target_digest"] = "a" * 64
    original["history"]["branches"][0]["result"]["target_digest"] = "a" * 64
    before = copy.deepcopy(original)
    parent = project_branch(original, "parent")
    snapshot = {**parent["result"], "attempts": 3, "initial_score": 0.4}
    snapshot.pop("target_digest")
    updated = replace_branch_snapshot(original, "parent", snapshot)
    replacement = project_branch(updated, "parent")
    assert replacement["result"]["target_digest"] == "a" * 64
    assert replacement["telemetry"]["attempts"] == 3
    assert updated["result"] == before["result"]
    assert updated["telemetry"] == before["telemetry"]
    assert updated["history"]["view_shape_count"] == 1
    replacement["result"]["shapes"][0]["data"]["x1"] = 999
    assert original == before
    assert loads_project(dumps_project(updated)) == updated
    with pytest.raises(ProjectError) as error:
        replace_branch_snapshot(original, "parent", {**snapshot, "target_digest": "b" * 64})
    assert error.value.code == "frozen_target" and original == before


def test_cached_previews_are_validated_and_discarded_without_changing_source_or_geometry(monkeypatch) -> None:
    raw = _graph()
    preview = "data:image/png;base64," + "a" * 2000
    raw["result"]["preview_data_url"] = preview
    raw["history"]["branches"][0]["result"]["preview_data_url"] = preview
    normalized = validate_project(raw)
    monkeypatch.setattr(project, "PROJECT_MAX_BYTES", 5000)
    serialized = loads_project(dumps_project(normalized))
    assert serialized["result"]["preview_data_url"] is None
    assert project_branch(serialized, "parent")["result"]["preview_data_url"] is None
    assert normalized["result"]["preview_data_url"] is None
    assert raw["result"]["preview_data_url"] == preview
    assert serialized["source"] == normalized["source"]
    assert serialized["result"]["shapes"] == normalized["result"]["shapes"]
    monkeypatch.setattr(project, "PROJECT_MAX_BYTES", 128)
    with pytest.raises(ProjectError, match="exceeds 64 MiB"):
        dumps_project(serialized)


@pytest.mark.parametrize("preview", ["", "http://example.com/preview.png", "data:image/svg+xml;base64,YWJj", True])
def test_unsupported_cached_previews_are_not_silently_accepted(preview) -> None:
    raw = _raw()
    raw["result"]["preview_data_url"] = preview
    with pytest.raises(ProjectError, match="result preview"):
        validate_project(raw)


@pytest.mark.parametrize("location", ["background", "color"])
def test_rgba_arrays_require_exactly_four_channels(location) -> None:
    raw = _raw()
    if location == "background":
        raw["result"]["background"] = [10, 20, 30, 255, 0]
    else:
        raw["result"]["shapes"][0]["color"] = [10, 20, 30, 255, 0]
    with pytest.raises(ProjectError, match="four RGBA"):
        validate_project(raw)


@pytest.mark.parametrize("change", [
    {"background": None}, {"width": None}, {"height": None},
    {"width": None, "height": None, "background": None, "shapes": [], "preview_data_url": _SOURCE},
])
@pytest.mark.parametrize("inactive", [False, True])
def test_incomplete_and_bitmap_only_heads_reject_in_every_experiment(change, inactive) -> None:
    raw = _graph()
    result = raw["history"]["branches"][0]["result"] if inactive else raw["result"]
    result.update(change)
    with pytest.raises(ProjectError, match="fitting grid and background"):
        validate_project(raw)
