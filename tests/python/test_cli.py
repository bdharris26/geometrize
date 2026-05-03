from __future__ import annotations

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
