from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from geometrize_py.batch import load_batch_manifest
from geometrize_py.images import inspect_image
from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType, derive_output_path
from geometrize_py.manifests import load_job
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
        if args.command == "batch":
            return _batch(args)
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
    source.add_argument("--job", type=Path, help="JSON job manifest to run.")
    run.add_argument("--output", type=Path, help="Output path. Defaults beside the input image.")
    run.add_argument("--shape", default="ellipse", choices=ShapeType.cli_choices())
    run.add_argument("--count", type=int, help="Number of accepted shapes to request.")
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

    batch = subparsers.add_parser("batch", help="Run or preview a batch manifest.")
    batch.add_argument("--manifest", type=Path, required=True, help="Batch JSON manifest.")
    batch.add_argument("--dry-run", action="store_true", help="Print resolved job plans without invoking native code.")
    batch.add_argument("--continue-on-error", action="store_true", help="Keep running after individual job failures.")
    return parser


def _run(args: argparse.Namespace) -> int:
    job = _resolve_job(args)
    if not job.input_path.exists():
        print(f"error: input image does not exist: {job.input_path}", file=sys.stderr)
        return 2

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


def _batch(args: argparse.Namespace) -> int:
    manifest = load_batch_manifest(args.manifest)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "jobs_total": len(manifest.jobs),
                    "plans": [job.as_plan(runner="dry-run") for job in manifest.jobs],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    summary: dict[str, object] = {
        "jobs_total": len(manifest.jobs),
        "jobs_succeeded": 0,
        "jobs_failed": 0,
        "results": [],
    }
    results: list[dict[str, object]] = []
    runner = NativeRunner()

    for index, job in enumerate(manifest.jobs):
        try:
            if not job.input_path.exists():
                raise ValueError(f"input image does not exist: {job.input_path}")
            result = runner.run(job)
            results.append(
                {
                    "index": index,
                    "output_path": str(result.output_path),
                    "shapes_written": result.shapes_written,
                    "attempts": result.attempts,
                }
            )
            summary["jobs_succeeded"] = int(summary["jobs_succeeded"]) + 1
        except (NativeCoreUnavailable, ValueError) as exc:
            results.append({"index": index, "error": str(exc), "output_path": str(job.output_path)})
            summary["jobs_failed"] = int(summary["jobs_failed"]) + 1
            if not args.continue_on_error:
                summary["results"] = results
                print(json.dumps(summary, indent=2, sort_keys=True))
                return 2

    summary["results"] = results
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if int(summary["jobs_failed"]) else 0


def _resolve_input_path(args: argparse.Namespace) -> Path:
    if args.latest_screenshot:
        screenshot = find_latest_screenshot()
        if screenshot is None:
            raise ValueError("no screenshot image found")
        return screenshot
    return args.input


def _resolve_job(args: argparse.Namespace) -> GeometrizeJob:
    if args.job:
        return load_job(args.job)
    if args.count is None:
        raise ValueError("--count is required unless --job is supplied")

    input_path = _resolve_input_path(args)
    shape = ShapeType.from_cli(args.shape)
    export_format = ExportFormat.from_cli(args.export_format)
    output_path = args.output or derive_output_path(input_path, shape, args.count, export_format)
    return GeometrizeJob(
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
