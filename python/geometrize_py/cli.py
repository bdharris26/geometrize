from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .images import image_to_png_bytes, open_image_bytes
from .native import Focus, RunOptions, diagnostics, run_image
from .render import export_dimensions, render_shapes_to_image
from .resources import DEFAULT_ACTIVE_MEMORY_BYTES, DEFAULT_WORKER_BUDGET
from .svg import shapes_to_svg
from .web import run_server


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command is None:
        run_server("127.0.0.1", 7860, False)
        return 0
    if command == "serve":
        if args.workers < 1 or args.active_memory_mb < 1:
            parser.error("Worker and active memory budgets must be positive")
        run_server(
            args.host,
            args.port,
            args.open,
            worker_budget=args.workers,
            active_memory_bytes=args.active_memory_mb * 1024 * 1024,
        )
        return 0
    if command == "run":
        try:
            _validate_distinct_paths(args)
            options = options_from_args(args)
        except ValueError as exc:
            parser.error(str(exc))
        return run_once(args, options)
    if command == "doctor":
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
    parser.error(f"Unknown command: {command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geometrize",
        description="Run the Python Geometrize UI or headless renderer.",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="start the browser UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7860)
    serve.add_argument("--open", action="store_true", help="open the UI in the default browser")
    serve.add_argument("--workers", type=int, default=DEFAULT_WORKER_BUDGET, help="total fitting worker budget")
    serve.add_argument(
        "--active-memory-mb",
        type=int,
        default=DEFAULT_ACTIVE_MEMORY_BYTES // (1024 * 1024),
        help="memory budget for active image decoding, fitting, and export work",
    )

    run = subparsers.add_parser("run", help="render an image once from the command line")
    run.add_argument("input", type=Path)
    run.add_argument("--output", type=Path, required=True, help="PNG output path")
    run.add_argument("--svg", type=Path, help="optional SVG output path")
    run.add_argument("--json", type=Path, help="optional shape JSON output path")
    add_option_arguments(run)

    doctor = subparsers.add_parser("doctor", help="check the native backend")
    doctor.add_argument("--json", action="store_true", help="print structured native import diagnostics")
    doctor.set_defaults(command="doctor")
    return parser


def add_option_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--steps", type=int, default=RunOptions.steps)
    parser.add_argument("--shape-types", default=",".join(RunOptions.shape_types))
    parser.add_argument("--alpha", type=int, default=RunOptions.alpha)
    parser.add_argument("--shape-count", type=int, default=RunOptions.shape_count)
    parser.add_argument("--mutations", type=int, default=RunOptions.mutations)
    parser.add_argument("--seed", type=int, default=RunOptions.seed)
    parser.add_argument("--max-threads", type=int, default=RunOptions.max_threads)
    parser.add_argument("--focus-x", type=float, help="focus center x, normalized from 0 to 1")
    parser.add_argument("--focus-y", type=float, help="focus center y, normalized from 0 to 1")
    parser.add_argument(
        "--focus-radius", type=float, help="focus radius as a fraction of the shorter image edge (0.01–1)",
    )
    parser.add_argument("--focus-strength", type=float, help="fraction of candidate starts biased toward focus (0–1)")
    parser.add_argument(
        "--stagnation-limit",
        type=int,
        default=RunOptions.stagnation_limit,
        help="stop after this many consecutive rejected attempts; 0 disables this cutoff",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=RunOptions.max_size,
        help="optimizer working resolution (longest dimension, capped at 2048)",
    )
    parser.add_argument(
        "--export-size",
        "--longest-dimension",
        dest="export_size",
        type=int,
        default=RunOptions.export_size,
        help="output resolution (longest dimension, capped at 4096)",
    )


def options_from_args(args: argparse.Namespace) -> RunOptions:
    focus = None
    if any(value is not None for value in (args.focus_x, args.focus_y, args.focus_radius, args.focus_strength)):
        if args.focus_x is None or args.focus_y is None:
            raise ValueError("--focus-x and --focus-y are required together when setting focus")
        focus = {
            "x": args.focus_x,
            "y": args.focus_y,
            "radius": Focus.radius if args.focus_radius is None else args.focus_radius,
            "strength": Focus.strength if args.focus_strength is None else args.focus_strength,
        }
    return RunOptions.from_mapping(
        {
            "steps": args.steps,
            "shape_types": args.shape_types,
            "alpha": args.alpha,
            "shape_count": args.shape_count,
            "mutations": args.mutations,
            "seed": args.seed,
            "max_threads": args.max_threads,
            "max_size": args.max_size,
            "export_size": args.export_size,
            "stagnation_limit": args.stagnation_limit,
            "focus": focus,
        }
    )


def run_once(args: argparse.Namespace, options: RunOptions | None = None) -> int:
    options = options or options_from_args(args)
    image = open_image_bytes(args.input.read_bytes())
    result = run_image(image, options)
    export_width, export_height = export_dimensions(result.width, result.height, options.export_size)
    output = render_shapes_to_image(
        result.shapes,
        result.width,
        result.height,
        result.background,
        export_width,
        export_height,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image_to_png_bytes(output))
    if args.svg:
        svg = shapes_to_svg(result.shapes, result.width, result.height, result.background, export_width, export_height)
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        args.svg.write_text(svg, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result.shapes, indent=2), encoding="utf-8")
    print(f"wrote {args.output} with {len(result.shapes)} shapes")
    return 0


def _validate_distinct_paths(args: argparse.Namespace) -> None:
    paths = [
        ("input", args.input),
        ("PNG output", args.output),
        ("SVG output", args.svg),
        ("JSON output", args.json),
    ]
    seen: dict[str, str] = {}
    for label, path in paths:
        if path is None:
            continue
        key = os.path.normcase(str(path.resolve()))
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(f"{label} path must differ from the {previous} path")
        seen[key] = label
