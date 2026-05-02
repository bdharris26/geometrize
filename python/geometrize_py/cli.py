from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from geometrize_py.images import inspect_image
from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType, derive_output_path
from geometrize_py.native import NativeCoreUnavailable, NativeRunner
from geometrize_py.screenshots import find_latest_screenshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return _run(args)
        if args.command == "latest-screenshot":
            return _latest_screenshot()
        if args.command == "inspect":
            return _inspect(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.print_help(sys.stderr)
    return 2


def entrypoint() -> None:
    raise SystemExit(main())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geometrize-py")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run or preview a headless geometrize job.")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Image path to geometrize.")
    source.add_argument("--latest-screenshot", action="store_true", help="Use the newest screenshot image.")
    run.add_argument("--output", type=Path, help="Output path. Defaults beside the input image.")
    run.add_argument("--shape", default="ellipse", choices=ShapeType.cli_choices())
    run.add_argument("--count", type=int, required=True, help="Number of accepted shapes to request.")
    run.add_argument("--export-format", default="png", choices=[format_.value for format_ in ExportFormat])
    run.add_argument("--alpha", type=int, default=128)
    run.add_argument("--candidate-shape-count", type=int, default=50)
    run.add_argument("--max-shape-mutations", type=int, default=100)
    run.add_argument("--seed", type=int, default=9001)
    run.add_argument("--max-threads", type=int, default=0)
    run.add_argument("--dry-run", action="store_true", help="Print the job plan without invoking native code.")

    subparsers.add_parser("latest-screenshot", help="Print the newest screenshot path.")

    inspect = subparsers.add_parser("inspect", help="Print image metadata as JSON.")
    inspect.add_argument("input", type=Path, help="Image path to inspect.")
    return parser


def _run(args: argparse.Namespace) -> int:
    input_path = _resolve_input_path(args)
    if not input_path.exists():
        print(f"error: input image does not exist: {input_path}", file=sys.stderr)
        return 2

    shape = ShapeType.from_cli(args.shape)
    export_format = ExportFormat.from_cli(args.export_format)
    output_path = args.output or derive_output_path(input_path, shape, args.count, export_format)
    job = GeometrizeJob(
        input_path=input_path,
        output_path=output_path,
        shape=shape,
        count=args.count,
        export_format=export_format,
        alpha=args.alpha,
        candidate_shape_count=args.candidate_shape_count,
        max_shape_mutations=args.max_shape_mutations,
        seed=args.seed,
        max_threads=args.max_threads,
    )

    if args.dry_run:
        print(json.dumps(job.as_plan(runner="dry-run"), indent=2, sort_keys=True))
        return 0

    try:
        result = NativeRunner().run(job)
    except NativeCoreUnavailable as exc:
        print(f"error: native core unavailable: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"output_path": str(result.output_path), "shapes_written": result.shapes_written}))
    return 0


def _latest_screenshot() -> int:
    screenshot = find_latest_screenshot()
    if screenshot is None:
        print("error: no screenshot image found", file=sys.stderr)
        return 2
    print(screenshot)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    info = inspect_image(args.input)
    print(json.dumps(info.as_dict(), indent=2, sort_keys=True))
    return 0


def _resolve_input_path(args: argparse.Namespace) -> Path:
    if args.latest_screenshot:
        screenshot = find_latest_screenshot()
        if screenshot is None:
            raise ValueError("no screenshot image found")
        return screenshot
    return args.input
