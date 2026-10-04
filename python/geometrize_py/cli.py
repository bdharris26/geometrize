from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli_jobs
from .cli_options import (
    add_option_arguments,
    add_resources,
    add_source_arguments,
    branch_id,
    experiment_name,
    nonnegative,
    seeds,
    validate_arguments,
)
from .native import RunOptions, diagnostics
from .web import run_server


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        run_server("127.0.0.1", 7860, False)
        return 0
    if args.command == "doctor":
        return _doctor(args)
    try:
        validate_arguments(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        if args.command == "serve":
            run_server(
                args.host,
                args.port,
                args.open,
                worker_budget=args.workers,
                active_memory_bytes=args.active_memory_mb * 1024 * 1024,
            )
            return 0
        try:
            reads, writes = cli_jobs.command_paths(args)
            cli_jobs.preflight_paths(reads, writes)
        except ValueError as exc:
            parser.error(str(exc))
        return cli_jobs.execute(args, reads, writes)
    except (OSError, ValueError, RuntimeError, SyntaxError, RecursionError) as exc:
        print(f"geometrize: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("geometrize: interrupted", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geometrize", description="Geometrize UI, headless jobs, and saved experiments."
    )
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="start the browser UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7860)
    serve.add_argument("--open", action="store_true", help="open the UI in the default browser")
    add_resources(serve)

    run = commands.add_parser("run", help="fit one source image")
    run.add_argument("input", type=Path)
    _outputs(run, project=True)
    add_option_arguments(run)
    add_resources(run)

    batch = commands.add_parser("batch", help="fit ordered inputs × seeds serially")
    batch.add_argument("inputs", nargs="+", type=Path)
    batch.add_argument("--seeds", type=seeds, default=[RunOptions.seed], help="comma-separated seeds, in order")
    batch.add_argument("--output-dir", type=Path, required=True)
    batch.add_argument("--manifest", type=Path, help="default: OUTPUT_DIR/manifest.json")
    for extension in ("svg", "json", "project"):
        batch.add_argument("--" + extension, action="store_true", help=f"also save each job's {extension} output")
    add_option_arguments(batch, batch=True)
    add_resources(batch)

    project = commands.add_parser("project", help="inspect, export, or fork saved experiments")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    inspect = project_commands.add_parser("inspect", help="inspect settings and branches without native fitting")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--json", action="store_true", help="print structured inspection")
    add_resources(inspect)
    export = project_commands.add_parser(
        "export", help="export a full head or an explicit prefix without native fitting"
    )
    export.add_argument("input", type=Path)
    _branch(export)
    _outputs(export)
    export.add_argument(
        "--export-size",
        "--longest-dimension",
        dest="export_size",
        type=int,
        help="default: the experiment's saved export size",
    )
    add_resources(export)
    fork = project_commands.add_parser("fork", help="replay into a fresh child; optionally fit new shapes")
    fork.add_argument("input", type=Path)
    fork.add_argument("--output", type=Path, required=True, help="new project path; the input is preserved")
    fork.add_argument("--name", type=experiment_name, help="unique child name; default: EXPERIMENT fork")
    _branch(fork)
    add_option_arguments(fork, fork=True)
    add_resources(fork)

    source = commands.add_parser("source", help="inspect an admitted still or animation source")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_inspect = source_commands.add_parser("inspect")
    source_inspect.add_argument("input", type=Path)
    add_source_arguments(source_inspect)
    add_resources(source_inspect)
    palette = commands.add_parser("palette", help="extract repeatable palette colors from the chosen source")
    palette_commands = palette.add_subparsers(dest="palette_command", required=True)
    extraction = palette_commands.add_parser("extract")
    extraction.add_argument("input", type=Path)
    extraction.add_argument("--max-colors", type=int, default=8)
    extraction.add_argument("--output", type=Path, help="write JSON instead of printing it")
    add_source_arguments(extraction)
    add_resources(extraction)

    doctor = commands.add_parser("doctor", help="check the native backend")
    doctor.add_argument("--json", action="store_true", help="print native import diagnostics")
    return parser


def _outputs(parser: argparse.ArgumentParser, *, project: bool = False) -> None:
    parser.add_argument("--output", type=Path, required=True, help="PNG output path")
    parser.add_argument("--svg", type=Path, help="optional SVG output path")
    parser.add_argument("--json", type=Path, help="optional shape JSON output path")
    if project:
        parser.add_argument("--project", type=Path, help="optional project with original source and confirmed result")


def _branch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--branch", type=branch_id, help="experiment id; default: active experiment")
    parser.add_argument("--at-shape", type=nonnegative, help="prefix length; default: full experiment head")


def _doctor(args: argparse.Namespace) -> int:
    status = diagnostics()
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"native backend: {'available' if status['available'] else 'unavailable'}")
        print(f"Python {status['python']} on {status['platform']}")
        if status["module_path"]:
            print(f"extension: {status['module_path']}")
        if status["import_error"]:
            print(f"import error ({status['import_error_type']}): {status['import_error']}")
            print('Rebuild in this Python environment with: python -m pip install -e ".[dev]"')
    return 0 if status["available"] else 1
