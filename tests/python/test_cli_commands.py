from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from geometrize_py import cli, cli_jobs, native
from geometrize_py.images import open_image_bytes
from geometrize_py.source import SourceOptions

FAST = [
    "--steps",
    "2",
    "--max-size",
    "64",
    "--export-size",
    "96",
    "--shape-count",
    "16",
    "--mutations",
    "24",
    "--max-threads",
    "1",
]


def _image(path: Path) -> bytes:
    image = Image.new("RGBA", (32, 20), (240, 220, 200, 128))
    ImageDraw.Draw(image).rectangle((0, 0, 12, 19), fill=(20, 90, 180, 255))
    ImageDraw.Draw(image).ellipse((19, 3, 30, 15), fill=(180, 70, 20, 255))
    image.save(path)
    return path.read_bytes()


@pytest.fixture
def saved(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = tmp_path / "source.png"
    _image(source)
    project = tmp_path / "project.json"
    assert (
        cli.main(["run", str(source), "--output", str(tmp_path / "initial.png"), "--project", str(project), *FAST]) == 0
    )
    return source, project, json.loads(project.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "command, flags",
    [
        ("run", ["--steps", "0"]),
        ("run", ["--alpha", "0"]),
        ("run", ["--shape-count", "513"]),
        ("run", ["--mutations", "2049"]),
        ("run", ["--seed", "2147483648"]),
        ("run", ["--max-threads", "129"]),
        ("run", ["--max-size", "8193"]),
        ("run", ["--export-size", "31"]),
        ("run", ["--shape-types", "unknown"]),
        ("run", ["--focus-x", "nan", "--focus-y", ".5"]),
        ("run", ["--focus-off", "--focus-radius", ".1"]),
        ("run", ["--palette-strength", ".5"]),
        ("run", ["--palette-off", "--palette-strength", "0"]),
        ("run", ["--extract-palette", "33"]),
        ("run", ["--palette", "#12345"]),
        ("run", ["--palette", "#123", "--palette-file", "unused.json"]),
        ("run", ["--frame", "256"]),
        ("run", ["--matte", "#notrgb"]),
        ("run", ["--background", "none"]),
        ("run", ["--active-memory-mb", "0"]),
        ("project", ["--name", ""]),
        ("project", ["--name", "😀" * 41]),
        ("project", ["--steps", "-1"]),
        ("project", ["--frame", "1"]),
        ("project", ["--matte", "white"]),
        ("project", ["--background", "white"]),
        ("project", ["--max-size", "32"]),
        ("project", ["--at-shape", "-1"]),
    ],
)
def test_invalid_flags_fail_before_any_filesystem_access(monkeypatch, command: str, flags: list[str]) -> None:
    monkeypatch.setattr(Path, "stat", lambda *_args, **_kwargs: pytest.fail("stat before flag validation"))
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("read before flag validation"))
    prefix = ["project", "fork"] if command == "project" else ["run"]
    with pytest.raises(SystemExit) as error:
        cli.main([*prefix, "missing-source", "--output", "missing-output", *flags])
    assert error.value.code == 2


def test_batch_job_cap_is_usage_error_before_io(monkeypatch) -> None:
    monkeypatch.setattr(Path, "stat", lambda *_args, **_kwargs: pytest.fail("stat before job cap"))
    with pytest.raises(SystemExit) as error:
        cli.main(["batch", "input", "--output-dir", "out", "--seeds", ",".join(str(i) for i in range(4097))])
    assert error.value.code == 2


def test_low_memory_rejects_before_decoder_and_does_not_publish(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "large.png"
    Image.new("RGB", (2000, 2000), (10, 20, 30)).save(source)
    monkeypatch.setattr(cli_jobs, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("decoder must not open"))
    assert cli.main(["run", str(source), "--output", str(tmp_path / "result.png"), "--active-memory-mb", "8"]) == 1
    assert "active memory budget" in capsys.readouterr().err
    assert not (tmp_path / "result.png").exists() and list(tmp_path.glob(".geometrize-*.tmp")) == []


def test_runtime_missing_input_is_concise_exit_one(tmp_path: Path, capsys) -> None:
    assert cli.main(["run", str(tmp_path / "missing.png"), "--output", str(tmp_path / "result.png")]) == 1
    error = capsys.readouterr().err
    assert "missing.png" in error and "Traceback" not in error


def test_soft_zero_cli_preserves_seeded_pixels_and_geometry(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _image(source)
    for name, extra in (("off", []), ("soft0", ["--palette", "#123,#abc", "--palette-strength", "0"])):
        assert (
            cli.main(
                [
                    "run",
                    str(source),
                    "--output",
                    str(tmp_path / f"{name}.png"),
                    "--json",
                    str(tmp_path / f"{name}.json"),
                    *FAST,
                    *extra,
                ]
            )
            == 0
        )
    assert (tmp_path / "off.png").read_bytes() == (tmp_path / "soft0.png").read_bytes()
    assert json.loads((tmp_path / "off.json").read_text()) == json.loads((tmp_path / "soft0.json").read_text())


def test_exact_palette_and_export_size_use_command_options(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _image(source)
    assert (
        cli.main(
            [
                "run",
                str(source),
                "--output",
                str(tmp_path / "result.png"),
                "--json",
                str(tmp_path / "shapes.json"),
                "--steps",
                "1",
                "--shape-count",
                "8",
                "--mutations",
                "8",
                "--max-threads",
                "1",
                "--max-size",
                "32",
                "--export-size",
                "64",
                "--palette",
                "#000,#fff",
            ]
        )
        == 0
    )
    shapes = json.loads((tmp_path / "shapes.json").read_text())
    assert len(shapes) == 1
    assert all(
        tuple(shape["color"][key] for key in ("r", "g", "b")) in {(0, 0, 0), (255, 255, 255)} for shape in shapes
    )
    with Image.open(tmp_path / "result.png") as image:
        assert image.size == (64, 40)


def test_source_helpers_and_palette_file_share_selected_frame_matte_and_original_bytes(tmp_path: Path, capsys) -> None:
    first = Image.new("RGBA", (80, 40), (200, 20, 30, 255))
    second = Image.new("RGBA", first.size, (20, 40, 200, 128))
    ImageDraw.Draw(second).rectangle((0, 0, 35, 39), fill=(40, 180, 50, 255))
    stream = io.BytesIO()
    first.save(stream, format="PNG", save_all=True, append_images=[second], duration=[50, 100], blend=[0, 0])
    raw = stream.getvalue()
    source = tmp_path / "animation.apng"
    source.write_bytes(raw)
    policy = ["--frame", "1", "--matte", "#123456"]
    assert cli.main(["source", "inspect", str(source), *policy]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["frame_count"] == 2 and inspected["mime_type"] == "image/apng"
    assert inspected["source"] == {"frame": 1, "matte": [18, 52, 86]}
    palette = tmp_path / "palette.json"
    assert cli.main(["palette", "extract", str(source), "--max-colors", "2", "--output", str(palette), *policy]) == 0
    extracted = json.loads(palette.read_text())
    project = tmp_path / "animation-project.json"
    assert (
        cli.main(
            [
                "run",
                str(source),
                "--output",
                str(tmp_path / "frame.png"),
                "--project",
                str(project),
                "--palette-file",
                str(palette),
                "--background",
                "#334455",
                *FAST,
                *policy,
            ]
        )
        == 0
    )
    saved = json.loads(project.read_text())
    assert saved["options"]["palette"]["colors"] == extracted["colors"]
    assert saved["options"]["source"] == inspected["source"]
    assert saved["result"]["background"] == {"r": 51, "g": 68, "b": 85, "a": 255}
    assert base64.b64decode(saved["source"]["data_url"].split(",", 1)[1]) == raw
    with open_image_bytes(raw, SourceOptions(1, (18, 52, 86))) as target:
        target.thumbnail((64, 64), Image.Resampling.LANCZOS)
        assert saved["result"]["target_digest"] == hashlib.sha256(target.tobytes()).hexdigest()


def test_project_inspect_and_prefix_export_are_native_free_and_use_saved_render_grid(
    saved: tuple[Path, Path, dict],
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _source, path, project = saved
    project["result"].update(width=96, height=60, render_width=32, render_height=20)
    project["history"]["view_shape_count"] = 0
    path.write_text(json.dumps(project), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setattr(native, "_native", None)
    from geometrize_py import project as project_api

    monkeypatch.setattr(project_api, "project_branch", lambda *_args: pytest.fail("inspection must not copy geometry"))
    capsys.readouterr()
    assert cli.main(["project", "inspect", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["saved_view_shape_count"] == 0 and report["branches"][0]["shape_count"] == 2
    assert (
        cli.main(
            [
                "project",
                "export",
                str(path),
                "--output",
                str(tmp_path / "prefix.png"),
                "--svg",
                str(tmp_path / "prefix.svg"),
                "--json",
                str(tmp_path / "prefix.json"),
                "--at-shape",
                "1",
                "--export-size",
                "96",
            ]
        )
        == 0
    )
    assert len(json.loads((tmp_path / "prefix.json").read_text())) == 1
    with Image.open(tmp_path / "prefix.png") as output:
        assert output.size == (96, 60)
    assert path.read_bytes() == original
    assert (
        cli.main(
            [
                "project",
                "export",
                str(path),
                "--output",
                str(tmp_path / "head.png"),
                "--json",
                str(tmp_path / "head.json"),
            ]
        )
        == 0
    )
    assert len(json.loads((tmp_path / "head.json").read_text())) == 2


def test_inspection_still_validates_geometry_without_native_or_branch_copies(saved, monkeypatch, capsys) -> None:
    _source, path, project = saved
    project["result"]["shapes"][0]["type"] = "unsupported-shape"
    path.write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setattr(native, "_native", None)
    capsys.readouterr()
    assert cli.main(["project", "inspect", str(path), "--json"]) == 1
    result = capsys.readouterr()
    assert "unsupported" in result.err and result.out == ""


@pytest.mark.parametrize("version", [1, 2])
def test_obsolete_project_versions_fail_without_replacing_outputs(saved, tmp_path: Path, version: int) -> None:
    _source, path, project = saved
    project["version"] = version
    path.write_text(json.dumps(project), encoding="utf-8")
    output = tmp_path / "unchanged.png"
    output.write_bytes(b"original output")
    assert cli.main(["project", "export", str(path), "--output", str(output)]) == 1
    assert output.read_bytes() == b"original output"


def test_replay_only_fork_keeps_inherited_options_and_fresh_counters(
    saved: tuple[Path, Path, dict],
    tmp_path: Path,
) -> None:
    source, path, project = saved
    original = path.read_bytes()
    child_path = tmp_path / "fork.json"
    assert cli.main(["project", "fork", str(path), "--output", str(child_path), "--at-shape", "1", "--steps", "0"]) == 0
    child = json.loads(child_path.read_text())
    assert len(child["result"]["shapes"]) == child["result"]["restored_shape_count"] == 1
    assert child["options"] == project["options"] and child["options"]["steps"] == 2
    assert child["telemetry"]["attempts"] == 0 and child["telemetry"]["batches"] == []
    assert child["telemetry"]["initial_score"] is not None
    assert child["result"]["target_digest"] == project["result"]["target_digest"]
    assert child["history"]["branches"][0]["result"]["shapes"] == project["result"]["shapes"]
    assert (
        path.read_bytes() == original
        and base64.b64decode(child["source"]["data_url"].split(",", 1)[1]) == source.read_bytes()
    )


@pytest.mark.parametrize("extract", [False, True])
def test_replay_only_palette_fork_reserves_one_worker_without_fitting_buffers(
    saved,
    tmp_path: Path,
    monkeypatch,
    extract: bool,
) -> None:
    _source, path, project = saved
    project["options"].update(max_threads=128, palette={"colors": [[10, 20, 30]], "strength": 1})
    path.write_text(json.dumps(project), encoding="utf-8")
    admitted = []
    use = cli_jobs.Admission.use
    restore = native.ImageSession._from_validated_scene

    def track_use(admission, scratch: int, *, workers: int = 0) -> None:
        use(admission, scratch, workers=workers)
        admitted.append(workers)

    def replay(image, options, *args, **kwargs):
        assert options.max_threads == admitted[-1] == 1
        return restore(image, options, *args, **kwargs)

    monkeypatch.setattr(cli_jobs.Admission, "use", track_use)
    monkeypatch.setattr(native.ImageSession, "_from_validated_scene", replay)
    monkeypatch.setattr(cli_jobs, "_fit_memory", lambda *_args: pytest.fail("replay does not fit palette colors"))
    output = tmp_path / "replay.json"
    extra = ["--extract-palette", "2"] if extract else []
    assert cli.main(["project", "fork", str(path), "--output", str(output), "--workers", "128", *extra]) == 0
    child = json.loads(output.read_text())
    assert max(admitted) == 1 and child["options"]["max_threads"] == 128
    assert child["options"]["palette"] is not None and child["telemetry"]["attempts"] == 0


def test_replay_preflight_rejects_before_opening_source_decoder(saved, tmp_path: Path, monkeypatch, capsys) -> None:
    _source, path, _project = saved

    class UnsafeReplay:
        def replay_memory(self, *_args):
            raise ValueError("Geometry cannot safely enter native replay")

    monkeypatch.setattr(cli_jobs, "require_restore_native", UnsafeReplay)
    monkeypatch.setattr(cli_jobs, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("decoder must not open"))
    output = tmp_path / "unsafe.json"
    assert cli.main(["project", "fork", str(path), "--output", str(output)]) == 1
    assert "safely enter native replay" in capsys.readouterr().err and not output.exists()


def test_fork_explicit_overrides_clear_constraints_and_accept_new_shapes(saved, tmp_path: Path) -> None:
    _source, path, project = saved
    project["options"].update(
        focus={"x": 0.5, "y": 0.5, "radius": 0.2, "strength": 0.75}, palette={"colors": [[10, 20, 30]], "strength": 1}
    )
    path.write_text(json.dumps(project), encoding="utf-8")
    output = tmp_path / "changed.json"
    assert (
        cli.main(
            [
                "project",
                "fork",
                str(path),
                "--output",
                str(output),
                "--at-shape",
                "1",
                "--name",
                "Changed",
                "--steps",
                "1",
                "--seed",
                "42",
                "--focus-off",
                "--palette-off",
            ]
        )
        == 0
    )
    child = json.loads(output.read_text())
    assert child["options"]["focus"] is None and child["options"]["palette"] is None
    assert child["options"]["seed"] == 42 and child["options"]["steps"] == 1
    assert child["result"]["restored_shape_count"] == 1 and len(child["result"]["shapes"]) == 2
    assert child["telemetry"]["batches"][0]["added"] == 1


def test_current_project_empty_shape_selection_requires_override_before_replay(saved, tmp_path: Path) -> None:
    _source, path, project = saved
    project["options"]["shape_types"] = []
    path.write_text(json.dumps(project), encoding="utf-8")
    output = tmp_path / "selected-shapes-fork.json"
    assert cli.main(["project", "fork", str(path), "--output", str(output)]) == 1
    assert not output.exists()
    assert cli.main(["project", "fork", str(path), "--output", str(output), "--shape-types", "rectangle"]) == 0
    assert json.loads(output.read_text())["options"]["shape_types"] == ["rectangle"]


def test_default_fork_name_obeys_utf16_limit(saved, tmp_path: Path) -> None:
    _source, path, project = saved
    project["history"]["branches"][0]["name"] = "😀" * 40
    path.write_text(json.dumps(project), encoding="utf-8")
    output = tmp_path / "unicode.json"
    assert cli.main(["project", "fork", str(path), "--output", str(output)]) == 0
    child = json.loads(output.read_text())
    name = child["history"]["branches"][-1]["name"]
    assert name.endswith(" fork") and len(name.encode("utf-16-le")) // 2 <= 80


def test_batch_orders_input_seed_jobs_and_keeps_same_basenames_safe(tmp_path: Path) -> None:
    sources = [tmp_path / folder / "source.png" for folder in ("a", "b")]
    for source in sources:
        source.parent.mkdir()
        _image(source)
    output = tmp_path / "out"
    assert (
        cli.main(
            [
                "batch",
                *(str(path) for path in sources),
                "--output-dir",
                str(output),
                "--seeds",
                "42,43",
                "--json",
                "--project",
                *FAST,
            ]
        )
        == 0
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert [(entry["input"], entry["seed"]) for entry in manifest["jobs"]] == [
        (str(path.resolve()), seed) for path in sources for seed in (42, 43)
    ]
    assert manifest["state"] == "complete" and all(entry["state"] == "complete" for entry in manifest["jobs"])
    assert all(len(entry["committed_outputs"]) == 3 and entry["effective_workers"] == 1 for entry in manifest["jobs"])
    assert len(list(output.glob("*.png"))) == 4
    assert len({Path(entry["planned_outputs"]["output"]).name for entry in manifest["jobs"]}) == 4


def test_batch_preflights_later_input_collisions_before_fitting(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.png"
    _image(first)
    output = tmp_path / "out"
    output.mkdir()
    later = output / "0001-first-seed42.png"
    original = _image(later)
    monkeypatch.setattr(
        cli_jobs, "open_image_bytes", lambda *_args, **_kwargs: pytest.fail("must preflight whole plan")
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["batch", str(first), str(later), "--output-dir", str(output), "--seeds", "42", *FAST])
    assert error.value.code == 2 and later.read_bytes() == original
    assert not (output / "manifest.json").exists()


def test_batch_partial_failure_manifest_records_committed_paths_and_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    _image(source)
    output = tmp_path / "out"
    output.mkdir()
    blocked = output / "0001-source-seed42.svg"
    blocked.write_bytes(b"old SVG")
    replace = os.replace

    def fail_svg(staged: Path, target: Path) -> None:
        if target == blocked:
            raise PermissionError("SVG locked")
        replace(staged, target)

    monkeypatch.setattr(os, "replace", fail_svg)
    assert cli.main(["batch", str(source), "--output-dir", str(output), "--seeds", "42,43", "--svg", *FAST]) == 1
    manifest = json.loads((output / "manifest.json").read_text())
    one, two = manifest["jobs"]
    assert manifest["state"] == one["state"] == "error" and two["state"] == "complete"
    assert one["committed_outputs"] == [str((output / "0001-source-seed42.png").resolve())]
    assert blocked.read_bytes() == b"old SVG"
    assert len(two["committed_outputs"]) == 2 and list(output.glob(".geometrize-*.tmp")) == []


def test_batch_interrupt_records_partial_publication_and_stops_future_jobs(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    _image(source)
    output = tmp_path / "out"
    replace = os.replace

    def interrupt_svg(staged: Path, target: Path) -> None:
        if target.suffix == ".svg":
            raise KeyboardInterrupt
        replace(staged, target)

    monkeypatch.setattr(os, "replace", interrupt_svg)
    assert cli.main(["batch", str(source), "--output-dir", str(output), "--seeds", "42,43", "--svg", *FAST]) == 130
    manifest = json.loads((output / "manifest.json").read_text())
    one, two = manifest["jobs"]
    assert manifest["state"] == one["state"] == "interrupted" and two["state"] == "planned"
    assert one["committed_outputs"] == [str((output / "0001-source-seed42.png").resolve())]
    assert not (output / "0002-source-seed43.png").exists() and list(output.glob(".geometrize-*.tmp")) == []


def test_batch_output_reporting_failure_retains_already_committed_paths(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    _image(source)
    output = tmp_path / "out"
    printed = []

    def fail_first_report(*args, **_kwargs) -> None:
        if args and args[0].startswith("wrote "):
            printed.append(args)
            if len(printed) == 1:
                raise OSError("output stream closed after publication")

    monkeypatch.setattr(cli_jobs, "print", fail_first_report, raising=False)
    assert cli.main(["batch", str(source), "--output-dir", str(output), "--seeds", "42,43", *FAST]) == 1
    manifest = json.loads((output / "manifest.json").read_text())
    first, second = manifest["jobs"]
    assert first["state"] == "error" and second["state"] == "complete"
    assert first["committed_outputs"] == [str((output / "0001-source-seed42.png").resolve())]


def test_batch_long_path_retention_is_admitted_before_manifest_construction(tmp_path: Path, monkeypatch) -> None:
    args = cli.build_parser().parse_args(["batch", "source.png", "--output-dir", str(tmp_path / ("long-" * 200))])
    reserved = []

    def reject(_workers, _memory_mb, *, retained: int = 0):
        reserved.append(retained)
        raise ValueError("plan reservation rejected")

    monkeypatch.setattr(cli_jobs, "Admission", reject)
    monkeypatch.setattr(
        cli_jobs, "publish", lambda *_args, **_kwargs: pytest.fail("must admit before encoding manifest")
    )
    with pytest.raises(ValueError, match="plan reservation rejected"):
        cli_jobs._batch(args, [], [])
    planned = list(cli_jobs._batch_jobs(args))[0][3]
    assert reserved[0] >= 8192 + sum(len(str(path)) * 16 for path in planned.values())
