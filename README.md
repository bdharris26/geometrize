# Geometrize

Geometrize is a small Python web app that recreates images as geometric
primitives. The UI, CLI, and packaging are Python-first; the fitting algorithm
still runs in the native C++ core from `lib/geometrize`.

![Geometrize logo](screenshots/logo.png)

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

## Command Line

```powershell
.\.venv\Scripts\python.exe -m geometrize_py run C:\LocalRepos\geometrize\screenshots\logo.png `
  --output C:\LocalRepos\geometrize\build\logo.png `
  --svg C:\LocalRepos\geometrize\build\logo.svg `
  --json C:\LocalRepos\geometrize\build\logo.json `
  --steps 128 --shape-types ellipse,rotated_rectangle,triangle `
  --longest-dimension 2048
```

Use `.\.venv\Scripts\python.exe -m geometrize_py doctor` to confirm that the
native backend can be imported.

## Project Layout

- `python/geometrize_py/` contains the Python package, web server, static UI,
  image helpers, SVG exporter, CLI, and native backend wrapper.
- `python/geometrize_py/native_bindings.cpp` is the pybind11 bridge into the
  C++ core.
- `lib/geometrize/` is the upstream core engine submodule.
- `tests/python/` covers the native wrapper, SVG export, and HTTP API.
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

The extension is built by scikit-build-core and CMake when installing the
package. A working C++17 compiler is required for a fresh native build, but no
desktop UI toolchain is needed.

For a quick packaging smoke test:

```powershell
.\.venv\Scripts\python.exe -m pip wheel . -w build\wheel-smoke --no-deps
```
