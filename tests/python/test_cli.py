from __future__ import annotations

import json

from PIL import Image, ImageDraw

from geometrize_py import cli


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
