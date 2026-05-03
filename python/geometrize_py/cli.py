from __future__ import annotations

import argparse
import json
from pathlib import Path

from .images import image_to_png_bytes, open_image_bytes
from .native import RunOptions, native_available, run_image
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
        run_server(args.host, args.port, args.open)
        return 0
    if command == "run":
        return run_once(args)
    if command == "doctor":
        print(f"native backend: {'available' if native_available() else 'unavailable'}")
        return 0 if native_available() else 1
    parser.error(f"Unknown command: {command}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geometrize", description="Run the Python Geometrize UI or headless renderer.")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="start the browser UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7860)
    serve.add_argument("--open", action="store_true", help="open the UI in the default browser")

    run = subparsers.add_parser("run", help="render an image once from the command line")
    run.add_argument("input", type=Path)
    run.add_argument("--output", type=Path, required=True, help="PNG output path")
    run.add_argument("--svg", type=Path, help="optional SVG output path")
    run.add_argument("--json", type=Path, help="optional shape JSON output path")
    add_option_arguments(run)

    doctor = subparsers.add_parser("doctor", help="check the native backend")
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
    parser.add_argument("--max-size", type=int, default=RunOptions.max_size)


def options_from_args(args: argparse.Namespace) -> RunOptions:
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
        }
    )


def run_once(args: argparse.Namespace) -> int:
    image = open_image_bytes(args.input.read_bytes())
    result = run_image(image, options_from_args(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image_to_png_bytes(result.image))
    svg = shapes_to_svg(result.shapes, result.width, result.height, result.background)
    if args.svg:
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        args.svg.write_text(svg, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result.shapes, indent=2), encoding="utf-8")
    print(f"wrote {args.output} with {len(result.shapes)} shapes")
    return 0


def entrypoint() -> int:
    return main()
