from __future__ import annotations

import argparse

import pytest

from geometrize_py.cli_options import add_option_arguments, add_resources, fork_overrides, options_from_args
from geometrize_py.native import Focus, RunOptions
from geometrize_py.palette import Palette


def _args(flags: list[str], *, fork: bool = False) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_option_arguments(parser, fork=fork)
    add_resources(parser)
    parser.set_defaults(command="project" if fork else "run", project_command="fork")
    return parser.parse_args(flags)


def test_options_keep_legacy_defaults_and_do_not_clamp() -> None:
    plain = options_from_args(_args([]))
    assert plain == RunOptions()
    assert plain.source.frame == 0 and plain.source.matte is None and plain.background is None
    for flags in (["--steps", "0"], ["--alpha", "0"], ["--max-size", "8193"], ["--seed", "-1"]):
        with pytest.raises(ValueError):
            options_from_args(_args(flags))


@pytest.mark.parametrize("strength", [0, 0.25, 1])
def test_custom_palette_strengths_and_short_hex_are_normalized(strength: float) -> None:
    options = options_from_args(_args(["--palette", "#f00,#00FF00,#ff0000", "--palette-strength", str(strength)]))
    assert options.palette == Palette(((255, 0, 0), (0, 255, 0)), strength)


def test_palette_mode_defaults_and_invalid_strength_groups() -> None:
    assert options_from_args(_args(["--palette", "#123", "--palette-mode", "soft"])).palette.strength == 0.75
    for flags in (
        ["--palette-strength", ".5"],
        ["--palette-off", "--palette-strength", "0"],
        ["--palette", "#123", "--palette-mode", "exact", "--palette-strength", ".5"],
        ["--palette", "#123", "--palette-strength", "nan"],
    ):
        with pytest.raises(ValueError, match="palette|Exact"):
            options_from_args(_args(flags))


def test_source_matte_and_background_are_explicit_rgb() -> None:
    options = options_from_args(_args(["--frame", "2", "--matte", "#123", "--background", "black"]))
    assert options.source.to_dict() == {"frame": 2, "matte": [17, 34, 51]}
    assert options.background == (0, 0, 0)


def test_fork_omission_inherits_and_steps_zero_is_only_execution_control() -> None:
    original = RunOptions(
        steps=8,
        seed=42,
        max_size=64,
        export_size=96,
        focus=Focus(0.25, 0.75, 0.1, 0.8),
        palette=Palette(((10, 20, 30),), 0.4),
        source={"frame": 2, "matte": [3, 4, 5]},
        background=(6, 7, 8),
    )
    args = _args([], fork=True)
    assert args.steps == 0
    assert options_from_args(args, original) == original
    assert fork_overrides(args, original) == {}
    changed = options_from_args(
        _args(["--steps", "2", "--palette-strength", "0", "--focus-x", ".6", "--focus-y", ".4"], fork=True), original
    )
    assert changed.steps == 2 and changed.palette.colors == original.palette.colors and changed.palette.strength == 0
    assert changed.focus == Focus(0.6, 0.4, 0.1, 0.8)
    assert changed.source == original.source and changed.background == original.background and changed.max_size == 64


def test_fork_explicit_off_clears_and_strength_alone_requires_inherited_colors() -> None:
    original = RunOptions(focus=Focus(0.5, 0.5), palette=Palette(((10, 20, 30),)))
    args = _args(["--focus-off", "--palette-off"], fork=True)
    changed = options_from_args(args, original)
    assert changed.focus is None and changed.palette is None
    assert fork_overrides(args, changed) == {"focus": None, "palette": None}
    with pytest.raises(ValueError, match="inherited palette"):
        options_from_args(_args(["--palette-strength", ".5"], fork=True), RunOptions())


def test_deferred_palette_file_and_extraction_resolution_keep_triplets_independent() -> None:
    args = _args(["--palette-file", "colors.json", "--palette-mode", "soft"])
    colors = [[1, 2, 3], [4, 5, 6]]
    options = options_from_args(args, palette_value={"colors": colors, "requested_max_colors": 2})
    colors[0][0] = 100
    assert options.palette == Palette(((1, 2, 3), (4, 5, 6)), 0.75)
    with pytest.raises(ValueError):
        options_from_args(args, palette_value={"colors": [[1, 2, 3]], "strength": None})
