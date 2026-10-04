# Geometrize

Geometrize is a small Python web app that recreates images as geometric
primitives. The UI, CLI, and packaging are Python-first; the fitting algorithm
still runs in the native C++ core from `lib/geometrize`.

![Geometrize logo](screenshots/logo.png)

## Fork Status

This is Boice Harris's Python-first fork of
[Tw1ddle/geometrize](https://github.com/Tw1ddle/geometrize). It intentionally
keeps the upstream fitting engine as a pinned submodule while replacing the Qt
desktop application with a small Python package, CLI, and dependency-light
browser UI. Python-fork releases use `geometrize-py-v*` tags so they remain
distinct from the historical upstream `v1.*` releases.

## Quick Start

Requires Python 3.10+ and a C++17 compiler for the native extension. Run these
commands from the repository root:

```powershell
git submodule update --init --recursive
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m geometrize_py serve --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860`, choose a PNG, JPEG, WebP, BMP, or GIF, and choose
Quick sketch, Balanced, or Fine detail. Set the number of shapes to add, then
start a render. Advanced controls expose all nine primitives, seed, candidates,
mutations, and a per-batch CPU limit. Changes apply to the next batch; New render
clears the result so Run starts a fresh fit with the current settings.

Enable Focus area to guide candidate starts toward a circular region. Click or
drag the live result to move the center, and adjust radius (a fraction of the
shorter image edge) and strength (the share of candidates started in the region). Focus can
move or clear during a run; changes apply after the current optimizer attempt.
Scoring still measures the full image, and shapes can extend or move outside
the ring. Disabling focus or setting strength to zero preserves normal fitting.
Use Shift+drag to pan the result while focusing or painting.

Paint mode tries to add one accepted shape per click on the live result. It
also works before the first render. Up to 12 pending clicks are processed in
order with their captured focus positions. Leaving Paint clears queued clicks
and finishes the active stroke; Pause finishes the current optimizer attempt.
A click can add no shape when the image is already matched or no candidate
improves the fit. Projects save focus settings and batch history.

The source and live result share zoom and pan controls. Pause finishes the
current fitting step and confirms the final counts before Continue becomes
available. The error chart shows the initial baseline and recent improvements;
lower error is better. A batch also stops when the source is already matched,
improvement stalls, or its attempt limit is reached.

PNG and SVG exports are generated on demand from the current result. Changing
export size does not add shapes or rerun fitting. Working resolution and display
zoom are independent of export size, which is capped at 4096px. PNG rendering
preserves Pillow's rasterization and curve sampling; SVG and the live canvas use
continuous paths and antialiasing, so curved boundaries can differ more at larger
export sizes. The fitting engine uses its own
scanline and blending rules for the error score. Project JSON restores the
source, result, settings, and history, and allows exporting the saved result.
Continuing after reopening a project starts a new native fit.

## Command Line

```powershell
.\.venv\Scripts\python.exe -m geometrize_py run screenshots\logo.png `
  --output build\logo.png `
  --svg build\logo.svg `
  --json build\logo.json `
  --steps 128 --shape-types ellipse,rotated_rectangle,triangle `
  --max-size 1024 --export-size 2048
```

Add `--focus-x 0.75 --focus-y 0.25` to bias placement around that normalized
center. `--focus-radius` defaults to `0.2` of the shorter edge, and
`--focus-strength` defaults to `0.75`; valid ranges are `0.01–1` and `0–1`.

Use `.\.venv\Scripts\python.exe -m geometrize_py doctor` to confirm that the
native backend can be imported, or add `--json` for structured diagnostics.
`serve --workers 8 --active-memory-mb 512` sets the shared CPU and estimated
active-memory budgets for fitting and exports. Reservations are fixed for each
batch; requests beyond available capacity return a retryable busy response.
Session and encoded-export caches have separate bounds. The active-memory budget
is a conservative admission estimate, not an operating-system memory limit.

## Project Layout

- `python/geometrize_py/` contains the Python package, web server, static UI,
  image helpers, SVG exporter, CLI, and native backend wrapper.
- `python/geometrize_py/native_bindings.cpp` is the pybind11 bridge into the
  C++ core; `native_focus.h` composes the core's placement callbacks.
- `lib/geometrize/` is the upstream core engine submodule.
- `tests/python/` covers the native wrapper, CLI, rendering, exports, and HTTP
  API.
- `tests/browser/` exercises pause/continue, current-size downloads, project
  round trips, stream recovery, and geometry comparisons in Chromium.
- `screenshots/` keeps example inputs and historical output samples.

## Development

Run the Python tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the linter after installing the development extras:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Run the browser tests separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests\browser
```

Installing the package builds the extension with scikit-build-core and CMake.

For a quick packaging smoke test:

```powershell
.\.venv\Scripts\python.exe -m pip wheel . -w build\wheel-smoke --no-deps
```

## License

The application remains GPL-3.0-or-later. The bundled native
`geometrize-lib` core is MIT-licensed; its required attribution is included in
`THIRD_PARTY_NOTICES.md` and in built wheels. Project copyright notices are in
`COPYRIGHT.md`, and the complete GPLv3 terms are in `LICENSE`.
