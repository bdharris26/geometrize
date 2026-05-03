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
- CLI entrypoints: `python/geometrize_py/cli.py`.
- Native bridge contract: `python/geometrize_py/native.py` and
  `python/geometrize_py/native_bindings.cpp`.
- SVG export: `python/geometrize_py/svg.py`.
- Tests: `tests/python/`.

## Build And Verification

- Expected setup: Python 3.10+, Pillow, pytest for development, initialized
  submodules, and a C++17 compiler for fresh native extension builds.
- Bootstrap submodules with `git submodule update --init --recursive`.
- Install locally with `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`.
- Start the UI with
  `.\.venv\Scripts\python.exe -m geometrize_py serve --host 127.0.0.1 --port 7860`.
- Run tests with `.\.venv\Scripts\python.exe -m pytest`.
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
- Keep new docs concise. The point of this port is a small native-feeling
  Python project, not a migration archive.
