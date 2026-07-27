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

Open `http://127.0.0.1:7860`, choose an image, tune the shape settings, and add
a batch of primitives. The UI draws accepted shapes into a live preview, can
pause or continue the same evolving result with different shape settings, charts
the convergence and score impact, and exports PNG, SVG, and JSON shape data.
Working resolution is kept separate from export resolution so sharper output
does not require a much larger native optimizer session. Project JSON files can
restore the source, result, settings, and run history after restarting the app.

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
native backend can be imported.

## Project Layout

- `python/geometrize_py/` contains the Python package, web server, static UI,
  image helpers, SVG exporter, CLI, and native backend wrapper.
- `python/geometrize_py/native_bindings.cpp` is the pybind11 bridge into the
  C++ core.
- `lib/geometrize/` is the upstream core engine submodule.
- `tests/python/` covers the native wrapper, CLI, rendering, exports, and HTTP
  API.
- `tests/browser/` exercises the real Sample, Continue, download, and project
  round-trip workflow in Chromium.
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
`THIRD_PARTY_NOTICES.md` and in built wheels.
