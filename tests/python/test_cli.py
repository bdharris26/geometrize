from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from geometrize_py import cli
from geometrize_py.cli_options import options_from_args
from geometrize_py.native import Focus


def test_no_args_starts_server_defaults(monkeypatch) -> None:
    called = {}

    def fake_run_server(host: str, port: int, open_browser: bool) -> None:
        called.update({"host": host, "port": port, "open_browser": open_browser})

    monkeypatch.setattr(cli, "run_server", fake_run_server)

    assert cli.main([]) == 0
    assert called == {"host": "127.0.0.1", "port": 7860, "open_browser": False}


def test_serve_args_are_parsed() -> None:
    args = cli.build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000", "--open"])

    assert args.command == "serve"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.open is True


def test_cli_accepts_8192_working_and_export_resolution() -> None:
    args = cli.build_parser().parse_args(
        ["run", "source.png", "--output", "result.png", "--max-size", "8192", "--export-size", "8192"]
    )
    options = options_from_args(args)
    assert options.max_size == options.export_size == 8192


def test_cli_focus_flags_have_normalized_defaults() -> None:
    parser = cli.build_parser()
    plain = parser.parse_args(["run", "source.png", "--output", "result.png"])
    assert options_from_args(plain).focus is None
    args = parser.parse_args(["run", "source.png", "--output", "result.png", "--focus-x", "0.75", "--focus-y", "0.25"])
    assert options_from_args(args).focus == Focus(0.75, 0.25)
    explicit = parser.parse_args(
        [
            "run",
            "source.png",
            "--output",
            "result.png",
            "--focus-x",
            "0.75",
            "--focus-y",
            "0.25",
            "--focus-radius",
            "0.1",
            "--focus-strength",
            "0",
        ]
    )
    assert options_from_args(explicit).focus == Focus(0.75, 0.25, 0.1, 0)


@pytest.mark.parametrize(
    "flags",
    [
        ["--focus-x", "0.5"],
        ["--focus-radius", "0.2"],
        ["--focus-x", "nan", "--focus-y", "0.5"],
        ["--focus-x", "0.5", "--focus-y", "1.1"],
    ],
)
def test_cli_focus_errors_are_reported_before_reading_image(capsys, flags: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "missing-source.png", "--output", "result.png", *flags])
    assert error.value.code == 2
    assert "focus" in capsys.readouterr().err


def test_serve_resource_budgets_are_forwarded(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_server", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert cli.main(["serve", "--workers", "3", "--active-memory-mb", "256"]) == 0
    assert calls[0][1] == {"worker_budget": 3, "active_memory_bytes": 256 * 1024 * 1024}


def test_doctor_json_explains_native_import_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "diagnostics", lambda: {"available": False, "import_error": "missing runtime"})
    assert cli.main(["doctor", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["import_error"] == "missing runtime"


def test_run_command_writes_all_output_formats(tmp_path, capsys) -> None:
    source_path = tmp_path / "source.png"
    png_path = tmp_path / "result.png"
    svg_path = tmp_path / "result.svg"
    json_path = tmp_path / "result.json"
    source = Image.new("RGB", (10, 6), (245, 245, 245))
    ImageDraw.Draw(source).rectangle((0, 0, 4, 5), fill=(24, 96, 180))
    source.save(source_path)

    exit_code = cli.main(
        [
            "run",
            str(source_path),
            "--output",
            str(png_path),
            "--svg",
            str(svg_path),
            "--json",
            str(json_path),
            "--steps",
            "1",
            "--shape-types",
            "rectangle",
            "--shape-count",
            "32",
            "--mutations",
            "32",
            "--max-size",
            "64",
            "--export-size",
            "96",
        ]
    )

    assert exit_code == 0
    assert "with 1 shapes" in capsys.readouterr().out
    with Image.open(png_path) as output:
        assert output.size == (96, 58)
    assert 'width="96" height="58"' in svg_path.read_text(encoding="utf-8")
    shapes = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(shapes) == 1
    assert shapes[0]["type"] == "rectangle"


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--output", "{source}"],
        ["--output", "{shared}", "--svg", "{shared}"],
        ["--output", "{shared}", "--json", "{shared}"],
        ["--output", "{png}", "--svg", "{shared}", "--json", "{shared}"],
    ],
)
def test_run_command_rejects_colliding_paths(tmp_path, capsys, extra_args: list[str]) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (2, 2), (1, 2, 3)).save(source)
    values = {
        "source": str(source),
        "shared": str(tmp_path / "shared.out"),
        "png": str(tmp_path / "result.png"),
    }
    arguments = [value.format(**values) for value in extra_args]

    with pytest.raises(SystemExit) as error:
        cli.main(["run", str(source), *arguments])

    assert error.value.code == 2
    assert "path must differ" in capsys.readouterr().err
