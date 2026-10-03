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

The source and live result share zoom and pan controls. Pause finishes the
current fitting step and confirms the final counts before Continue becomes
available. The error chart shows the initial baseline and recent improvements;
lower error is better. A batch also stops when the source is already matched,
improvement stalls, or its attempt limit is reached.

PNG and SVG exports are generated on demand from the current result. Changing
export size does not add shapes or rerun fitting. Working resolution and display
zoom are independent of export size, which is capped at 4096px. PNG rendering
preserves Pillow's rasterization; SVG and the live canvas use continuous geometry
and antialiasing, so edge pixels can differ. The fitting engine uses its own
scanline and blending rules for the error score. Project JSON restores the
source, result, settings, and history, and allows exporting the saved result.
Continuing after reopening a project starts a new native fit.

## Command Line

```powershell
.\.venv\Scripts\python.exe -m geometrize_py run C:\LocalRepos\geometrize\screenshots\logo.png `
  --output C:\LocalRepos\geometrize\build\logo.png `
  --svg C:\LocalRepos\geometrize\build\logo.svg `
  --json C:\LocalRepos\geometrize\build\logo.json `
  --steps 128 --shape-types ellipse,rotated_rectangle,triangle `
  --max-size 1024 --export-size 2048
```

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
  C++ core.
- `lib/geometrize/` is the upstream core engine submodule.
- `tests/python/` covers the native wrapper, CLI, rendering, exports, and HTTP
  API.
- `tests/browser/` exercises pause/continue, current-size downloads, project
  round trips, stream recovery, and geometry comparisons in Chromium.
- `screenshots/` keeps example inputs and historical output samples.

## Development

Run the tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the linter after installing the development extras:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Run the real browser smoke test separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests\browser
```

The extension is built by scikit-build-core and CMake when installing the
package. A working C++17 compiler is required for a fresh native build, but no
desktop UI toolchain is needed.

For a quick packaging smoke test:

```powershell
.\.venv\Scripts\python.exe -m pip wheel . -w build\wheel-smoke --no-deps
```

## License

The application remains GPL-3.0-or-later. The bundled native
`geometrize-lib` core is MIT-licensed; its required attribution is included in
`THIRD_PARTY_NOTICES.md` and in built wheels. Project copyright notices are in
`COPYRIGHT.md`, and the complete GPLv3 terms are in `LICENSE`.
