# AGENTS.md

This repo is now a Python-first Geometrize app. Keep this guide short and
current so the next agent can find the important edges quickly.

## Project Shape

- `pyproject.toml` is the package and test entrypoint.
- `CMakeLists.txt` builds the `geometrize_py._native` pybind11 extension.
- First-party Python code lives under `python/geometrize_py/`.
- The browser UI is static HTML/CSS/JS served by `python/geometrize_py/web.py`.
- The C++ approximation engine remains the `lib/geometrize` submodule. Treat it
  as upstream core ownership unless the task explicitly needs core changes.
- Historical screenshots remain under `screenshots/` and are useful for smoke
  inputs and README visuals.

## First Places To Inspect

- UI server and API: `python/geometrize_py/web.py`.
- Shared limits, shapes, and presets: `python/geometrize_py/contracts.py`;
  the UI loads these from `/api/config`.
- Browser modules: `static/app.js` coordinates `preview.js`, `stream.js`,
  `telemetry.js`, `project.js`, `focus.js`, `palette.js`, `source.js`, and
  `history.js` under `python/geometrize_py/`.
- Source preparation: `source.py`, `image_probe.py`, and `images.py`; probe encoded
  bounds and reserve decoder memory before opening or seeking with Pillow.
  `apng.py` corrects frame composition while Pillow decodes each frame's pixels.
- CLI entrypoints: `cli.py`; strict flags in `cli_options.py`, admitted jobs and
  atomic output publication in `cli_jobs.py` under `python/geometrize_py/`.
- Python project contracts: `project.py`; inspect/export are native-free, while
  forks replay the saved grid/background/target before optional fitting.
- Native bridge contract: `python/geometrize_py/native.py` and
  `python/geometrize_py/native_bindings.cpp`; placement is in `native_focus.h`.
- Exports and resource budgets: `exporting.py`, `render.py`, `svg.py`, and
  `resources.py` under `python/geometrize_py/`.
- Tests: `tests/python/` and `tests/browser/`.

## Build And Verification

- `README.md` is the entrypoint for setup and usage; keep package notes linked
  to it instead of duplicating commands.
- Expected setup: Python 3.10+, Pillow, pytest for development, initialized
  submodules, and a C++17 compiler for fresh native extension builds.
- Bootstrap submodules with `git submodule update --init --recursive`.
- Install locally with `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`.
- Start the UI with
  `.\.venv\Scripts\python.exe -m geometrize_py serve --host 127.0.0.1 --port 7860`.
- Run Python tests with `.\.venv\Scripts\python.exe -m pytest`; browser tests
  are separate: `.\.venv\Scripts\python.exe -m pytest tests\browser` (see README
  for Playwright setup).
- Use `.\.venv\Scripts\python.exe -m geometrize_py doctor` when native import
  behavior is in question.

## Improvement-Friendly Notes

- Prefer Python app changes over touching `lib/geometrize`.
- Preserve the native bridge shape: Python owns image IO, UI state, SVG/JSON
  presentation, and request handling; C++ owns shape fitting.
- Keep the web UI dependency-light unless a new dependency clearly earns its
  weight.
- If browser behavior changes, verify with the local server and Playwright or
  the in-app browser.
- Streams require a terminal snapshot; cooperative Pause uses a server-issued
  run token. Keep exports independent of native fitting and preserve PNG pixels
  when optimizing rasterization.
- Focus changes candidate setup only; full-image mutation, rasterization, and
  energy stay upstream. Live focus uses a separate control lock and the active
  run token; Paint uses ordinary one-shape batches. Clicks capture their focus;
  a hold samples the latest pointer only when fitting is ready for another shape.
- Timeline inspection is read-only; branch heads stay intact. Explicit Restore/Fork
  creates a native session with reset attempts/RNG and retained shape counts.
  Project v3 stores the full graph and source policy; v1/v2 keep legacy defaults.
  Live session IDs stay in memory. Frame/matte edits create a new root; forks keep
  the target and saved background. Verify optional target digests during replay.
- Palette constraints apply to new shape RGB; alpha blending stays native. Off or
  zero strength must preserve the original fitting/RNG path and pixels.
- Keep new docs concise. The point of this port is a small native-feeling
  Python project, not a migration archive.
