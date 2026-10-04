"""Pure CLI grammar and strict options; no filesystem or image work."""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Any

from .contracts import OPTION_LIMITS, PALETTE_DEFAULTS, PALETTE_MAX_COLORS
from .native import Focus, RunOptions, normalize_focus
from .palette import Palette, normalize_max_colors, normalize_palette
from .resources import DEFAULT_ACTIVE_MEMORY_BYTES, DEFAULT_WORKER_BUDGET


def hex_colors(value: str) -> tuple[tuple[int, int, int], ...]:
    tokens = [token for token in re.split(r"[,\s]+", value.strip()) if token]
    if not tokens or len(tokens) > PALETTE_MAX_COLORS:
        raise ValueError(f"Palette needs 1–{PALETTE_MAX_COLORS} hex colors")
    colors = []
    for token in tokens:
        if re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", token) is None:
            raise ValueError("Colors must use #RGB or #RRGGBB")
        digits = token[1:]
        if len(digits) == 3:
            digits = "".join(channel * 2 for channel in digits)
        colors.append(tuple(int(digits[index : index + 2], 16) for index in (0, 2, 4)))
    return Palette(tuple(colors)).colors


def matte_color(value: str) -> tuple[int, int, int] | None:
    return _color(value, "none")


def background_color(value: str) -> tuple[int, int, int] | None:
    return _color(value, "average")


def _color(value: str, empty: str) -> tuple[int, int, int] | None:
    if value.lower() == empty:
        return None
    if value.lower() in {"white", "black"}:
        return (255, 255, 255) if value.lower() == "white" else (0, 0, 0)
    colors = hex_colors(value)
    if len(colors) != 1:
        raise ValueError(f"Color must be {empty}, white, black, or one hex color")
    return colors[0]


def seeds(value: str) -> list[int]:
    try:
        result = [int(token.strip()) for token in value.split(",")]
    except ValueError as exc:
        raise ValueError("Seeds must be comma-separated integers") from exc
    for seed in result:
        RunOptions(seed=seed)
    return result


def nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError("Value must be a nonnegative integer")
    return number


def experiment_name(value: str) -> str:
    value = value.strip()
    if not value or len(value.encode("utf-16-le")) // 2 > 80:
        raise ValueError("Experiment name must contain 1 to 80 characters")
    return value


def branch_id(value: str) -> str:
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) is None:
        raise ValueError("Branch must be an experiment id of 1 to 64 ASCII letters, digits, _ or -")
    return value


def add_resources(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKER_BUDGET, help="total fitting worker budget")
    parser.add_argument(
        "--active-memory-mb",
        type=int,
        default=DEFAULT_ACTIVE_MEMORY_BYTES // (1024 * 1024),
        help="active read, decode, fitting, and export memory budget",
    )


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frame", type=int, default=0, help="zero-based source frame, including any APNG poster")
    parser.add_argument("--matte", type=matte_color, default=None, help="none (default), white, black, or #RGB/#RRGGBB")


def add_option_arguments(parser: argparse.ArgumentParser, *, fork: bool = False, batch: bool = False) -> None:
    defaults = argparse.SUPPRESS if fork else None
    for name in OPTION_LIMITS:
        if name in {"max_size", "export_size"}:
            continue
        if batch and name == "seed":
            continue
        parser.add_argument(
            "--" + name.replace("_", "-"),
            type=int,
            default=(0 if name == "steps" and fork else defaults if fork else getattr(RunOptions, name)),
        )
    parser.add_argument(
        "--shape-types",
        default=defaults if fork else ",".join(RunOptions.shape_types),
        help="comma-separated shape names",
    )
    parser.add_argument("--focus-x", type=float, help="normalized focus x; use with --focus-y")
    parser.add_argument("--focus-y", type=float, help="normalized focus y; use with --focus-x")
    parser.add_argument("--focus-radius", type=float, help="focus radius, fraction of shorter edge (0.01–1)")
    parser.add_argument("--focus-strength", type=float, help="focus placement strength (0–1)")
    parser.add_argument("--focus-off", action="store_true", help="clear inherited focus")
    palette = parser.add_mutually_exclusive_group()
    palette.add_argument(
        "--palette", type=hex_colors, help="comma-separated #RGB/#RRGGBB colors; quote shell arguments"
    )
    palette.add_argument("--palette-file", type=Path, help="bounded JSON RGB array or palette extraction output")
    palette.add_argument(
        "--extract-palette", type=int, help=f"extract 1–{PALETTE_MAX_COLORS} colors from the chosen source"
    )
    palette.add_argument("--palette-off", action="store_true", help="clear inherited palette")
    parser.add_argument(
        "--palette-mode", choices=("exact", "soft"), help="exact (default) or soft (default strength 0.75)"
    )
    parser.add_argument(
        "--palette-strength", type=float, help="palette strength 0–1; 1 exact, 0 preserves settings without bias"
    )
    if not fork:
        add_source_arguments(parser)
        parser.add_argument(
            "--background",
            type=background_color,
            default=None,
            help="average (default), white, black, or #RGB/#RRGGBB starting canvas",
        )
        parser.add_argument(
            "--max-size", type=int, default=RunOptions.max_size, help="working longest dimension (32–8192)"
        )
    parser.add_argument(
        "--export-size",
        "--longest-dimension",
        dest="export_size",
        type=int,
        default=defaults if fork else RunOptions.export_size,
        help="export longest dimension (32–8192)",
    )


def validate_arguments(args: argparse.Namespace) -> None:
    if args.workers < 1 or args.active_memory_mb < 1:
        raise ValueError("Worker and active memory budgets must be positive")
    command = args.command
    if command == "batch" and len(args.inputs) * len(args.seeds) > 4096:
        raise ValueError("Batch can contain at most 4096 jobs")
    if command in {"run", "batch"} or command == "project" and args.project_command == "fork":
        fork = command == "project"
        options_from_args(args, allow_inherited_palette=fork)
        if getattr(args, "extract_palette", None) is not None:
            normalize_max_colors(args.extract_palette)
    elif command in {"source", "palette"}:
        RunOptions(source={"frame": args.frame, "matte": args.matte})
        if command == "palette":
            normalize_max_colors(args.max_colors)
    elif command == "project" and args.project_command == "export":
        if args.export_size is not None:
            RunOptions(export_size=args.export_size)


def options_from_args(
    args: argparse.Namespace,
    inherited: RunOptions | dict[str, Any] | None = None,
    *,
    palette_value: Any = None,
    allow_inherited_palette: bool = False,
) -> RunOptions:
    values = inherited.to_dict() if isinstance(inherited, RunOptions) else dict(inherited or {})
    fork = args.command == "project"
    for name in (*OPTION_LIMITS, "shape_types"):
        if hasattr(args, name) and not (fork and name == "steps" and args.steps == 0):
            values[name] = getattr(args, name)
    if fork and args.steps < 0:
        raise ValueError("--steps must be 0 for replay-only or a positive fitting count")
    if not fork:
        values["source"] = {"frame": args.frame, "matte": args.matte}
        values["background"] = args.background
    focus_flags = {name: getattr(args, f"focus_{name}", None) for name in ("x", "y", "radius", "strength")}
    if args.focus_off:
        if any(value is not None for value in focus_flags.values()):
            raise ValueError("--focus-off cannot be combined with focus coordinates or strength")
        values["focus"] = None
    elif any(value is not None for value in focus_flags.values()):
        if focus_flags["x"] is None or focus_flags["y"] is None:
            raise ValueError("--focus-x and --focus-y are required together when setting focus")
        previous = normalize_focus(values.get("focus"))
        values["focus"] = {
            key: value if value is not None else getattr(previous or Focus(0.5, 0.5), key)
            for key, value in focus_flags.items()
        }
    source = args.palette is not None or args.palette_file is not None or args.extract_palette is not None
    strength = _palette_strength(args)
    if args.palette_off:
        if strength is not None:
            raise ValueError("--palette-off cannot be combined with palette mode or strength")
        values["palette"] = None
    elif args.palette is not None:
        values["palette"] = Palette(args.palette, 1 if strength is None else strength)
    elif palette_value is not None:
        colors = palette_value.get("colors") if isinstance(palette_value, dict) else palette_value
        file_strength = palette_value.get("strength", 1) if isinstance(palette_value, dict) else 1
        file_palette = Palette(colors, file_strength)
        values["palette"] = Palette(file_palette.colors, file_palette.strength if strength is None else strength)
    elif strength is not None and not source:
        previous = normalize_palette(values.get("palette"))
        if previous is None and not allow_inherited_palette:
            raise ValueError("--palette-strength/--palette-mode needs colors or an inherited palette")
        if previous is not None:
            values["palette"] = Palette(previous.colors, strength)
    return RunOptions(**values)


def fork_overrides(args: argparse.Namespace, options: RunOptions) -> dict[str, Any]:
    values = options.to_dict()
    keys = {key for key in (*OPTION_LIMITS, "shape_types") if hasattr(args, key)}
    if args.steps == 0:
        keys.discard("steps")
    if args.focus_off or any(getattr(args, f"focus_{key}") is not None for key in ("x", "y", "radius", "strength")):
        keys.add("focus")
    if args.palette_off or any(
        getattr(args, key) is not None
        for key in ("palette", "palette_file", "extract_palette", "palette_mode", "palette_strength")
    ):
        keys.add("palette")
    return {key: values[key] for key in keys}


def _palette_strength(args: argparse.Namespace) -> float | None:
    strength = args.palette_strength
    mode = args.palette_mode
    if strength is not None and (not math.isfinite(strength) or not 0 <= strength <= 1):
        raise ValueError("--palette-strength must be a finite number from 0 to 1")
    if mode == "exact" and strength not in {None, 1}:
        raise ValueError("Exact palette mode requires --palette-strength 1")
    if strength is not None:
        return strength
    return PALETTE_DEFAULTS["soft_strength"] if mode == "soft" else 1 if mode == "exact" else None
