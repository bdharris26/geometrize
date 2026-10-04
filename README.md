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

Open `http://127.0.0.1:7860`, choose a PNG/APNG, JPEG, WebP, BMP, or GIF, and choose
Quick sketch, Balanced, or Fine detail. Set the number of shapes to add, then
start a render. Advanced controls expose all nine primitives, seed, candidates,
mutations, and a per-batch CPU limit. Changes apply to the next batch; New render
creates a blank experiment with the current settings and keeps the previous result.

Enable Focus area to guide candidate starts toward a circular region. Click or
drag the live result to move the center, and adjust radius (a fraction of the
shorter image edge) and strength (the share of candidates started in the region). Focus can
move or clear during a run; changes apply after the current optimizer attempt.
Scoring still measures the full image, and shapes can extend or move outside
the ring. Disabling focus or setting strength to zero preserves normal fitting.
Use Shift+drag to pan the result while focusing or painting.

Paint offers Click once and Hold to paint, including before the first render.
Click once queues up to 12 clicks with their captured focus positions. Holding
fits one shape at a time at the latest pointer position; releasing finishes the
active shape and stops further work. Leaving Paint clears pending work; Pause
finishes the current optimizer attempt. Holds also stop on an interruption or
when no candidate improves the fit. A click can add no shape when the image is
already matched. Projects save focus settings and the complete batch history.
The history panel keeps a fixed height, shows at most 50 batches, and lets you
page through older entries without shrinking the previews.

Scrub the Shape timeline to inspect an earlier prefix without changing the full
experiment. Head returns to its latest shape. Restore prefix or Fork experiment
creates a named child with the selected shapes and current settings; the original
stays selectable. Restoring recomputes native scores and resets attempts, batch
counts, and the random sequence. Fitting and Paint stay disabled while inspecting.
Switch between retained experiments to compare them, and use Name / remove to
rename one or remove an inactive experiment after its children have been removed.
Working resolution stays fixed to each experiment's scene until New render.

Source controls select a still frame and the matte behind transparent pixels.
New uploads use the first frame and a white matte; choose Black, Custom, or Keep
transparency as needed. Animated sources show their selected frame and retain
the original file for later selections. Frames use zero-based indexes; APNG's
default image, when present, is index 0. Discovery is limited to the first 256
frames, and selection also has a bounded decoding-work budget.
Sources with oversized metadata or legacy PNG "Raw profile type exif" text are
rejected before decoding; standard EXIF orientation is supported.
The starting background is separate: Average, White, Black, or a custom opaque
color initializes the reconstruction without changing the source. Apply to new
render commits frame, matte, and background edits together, retaining the previous
experiment. Continue and forks keep their existing target and canvas. Palette
extraction uses the selected, matted source shown in the preview.

Palette controls constrain the RGB colors of new shapes. Enter hex colors and
choose Use colors, or Extract source with a maximum of 1 to 32 colors. Extraction
uses a small sample, ignores fully transparent pixels, and can return fewer colors
than requested. Exact uses the chosen colors; Soft pulls the optimal fitted color
toward the palette with adjustable strength. Zero strength preserves ordinary fitting.
Opacity blending can produce intermediate pixel colors. Palette edits apply to
the next batch or paint stroke; retained shapes keep their original colors, and
each experiment saves its own palette.

The source and live result share zoom and pan controls. Pause finishes the
current fitting step and confirms the final counts before Continue becomes
available. The error chart shows the initial baseline and recent improvements;
lower error is better. A batch also stops when the source is already matched,
improvement stalls, or its attempt limit is reached.

PNG, SVG, and Shapes JSON export the visible timeline prefix. Changing
export size does not add shapes or rerun fitting. Working resolution and display
zoom are independent of export size. Both working and export resolution allow
up to 8192px on the longest edge. PNG rendering
preserves Pillow's rasterization and curve sampling; SVG and the live canvas use
continuous paths and antialiasing, so curved boundaries can differ more at larger
export sizes. The fitting engine uses its own
scanline and blending rules for the error score. Project JSON saves the source,
every experiment's full head and settings, complete batch history, and the selected
prefix. The app accepts project versions 1, 2, and 3. Older projects keep their
first-frame and transparency behavior; version 1 becomes a single Original
experiment. New projects retain source policies and a fingerprint of each fitted
target, so restore detects a changed target. Live sessions can Continue after
switching back; reopened or expired sessions require an explicit Restore or Fork
before fitting. A project supports
32 experiments, 200,000 retained shapes, and 1,000,000 polyline points in total.
Legacy preview-only results use New render to start fitting while retaining the preview.

## Command Line

```powershell
.\.venv\Scripts\python.exe -m geometrize_py run screenshots\logo.png `
  --output build\logo.png `
  --svg build\logo.svg `
  --json build\logo.json `
  --project build\logo.geometrize.json `
  --steps 128 --shape-types ellipse,rotated_rectangle,triangle `
  --max-size 1024 --export-size 2048
```

Add `--focus-x 0.75 --focus-y 0.25` to bias placement around that normalized
center. `--focus-radius` defaults to `0.2` of the shorter edge, and
`--focus-strength` defaults to `0.75`; valid ranges are `0.01–1` and `0–1`.

Use `--frame 1` to choose an animated source frame, `--matte white` or a hex
color to fill transparent pixels, and `--background black` or a hex color to
set the starting canvas. CLI defaults preserve frame 0, source transparency,
and the average background. `--palette "#E0B84F,#58C3A8"` constrains new shapes;
`--extract-palette 8` extracts source colors, and `--palette-file palette.json`
reads RGB colors saved by `palette extract`. These choices are mutually exclusive.
`--palette-strength 1` uses exact colors; a lower value applies a softer preference.

`--steps` requests accepted shapes; `--shape-count` controls candidate starts per
attempt, and `--mutations` controls how long refinement continues without
improvement. A run can stop early when the target is matched, improvement stalls,
or a limit is reached.
`--max-threads` sets the per-job fitting limit within the total `--workers` budget.

Run several inputs or seed experiments serially:

```powershell
.\.venv\Scripts\python.exe -m geometrize_py batch screenshots\logo.png `
  --output-dir build\sketches --seeds 11,22,33 --project --svg `
  --steps 64 --max-size 512 --export-size 1024
```

PNG files use indexed names so inputs with matching basenames stay distinct.
The output directory's `manifest.json` records settings, results, errors, and
published artifacts; `--manifest` chooses another path. Independent jobs continue
after failures, and the command returns a nonzero status if any job fails. Output
paths are checked before fitting and each artifact is replaced atomically; a
multi-file job can publish some artifacts before a later write fails.
Ctrl+C stops later jobs and records the interrupted job and any published outputs
in the manifest when that file can still be written.

Inspect and export saved geometry, or create a retained experiment from a prefix:

```powershell
.\.venv\Scripts\python.exe -m geometrize_py project inspect build\logo.geometrize.json --json
.\.venv\Scripts\python.exe -m geometrize_py project export build\logo.geometrize.json `
  --at-shape 64 --output build\prefix.png --svg build\prefix.svg --export-size 2048
.\.venv\Scripts\python.exe -m geometrize_py project fork build\logo.geometrize.json `
  --at-shape 64 --seed 42 --steps 128 --name "Detail experiment" `
  --output build\detail.geometrize.json
```

`--branch` selects an experiment ID reported by inspection. Omit `--at-shape` to
use its full head. Inspection and geometry export work without the native
backend; preview-only legacy results remain inspectable. Forking replays into a
fresh native session, retains the parent, and resets fitting attempts. Its default
is replay only (`--steps 0`); positive steps add new shapes. Omitted settings inherit
from the selected experiment. Frame, matte, background, and working resolution
stay fixed; `--focus-off` and `--palette-off` clear inherited fitting constraints.

`source inspect INPUT` reports selected-frame metadata. `palette extract INPUT
--max-colors 8 --output palette.json` saves reusable colors. Both accept `--frame`
and `--matte` and work without native fitting. Invalid numeric flags are rejected
instead of silently clamped. Rendering, source helpers, and project commands accept
`--workers` and `--active-memory-mb`; large images or projects may need a larger
budget, for example `--active-memory-mb 8192` on a machine with sufficient RAM.

Use `.\.venv\Scripts\python.exe -m geometrize_py doctor` to confirm that the
native backend can be imported, or add `--json` for structured diagnostics.
`serve --workers 8 --active-memory-mb 512` sets the shared CPU and estimated
active-memory budgets for fitting and exports. Reservations are fixed for each
batch; requests beyond available capacity return a retryable busy response.
Session and encoded-export caches have separate bounds. The active-memory budget
is a conservative admission estimate, not an operating-system memory limit.
Large 8192px images need a larger budget; on a machine with sufficient RAM, use
`serve --active-memory-mb 8192`. Raising this budget also increases the default
resumable-session cache budget; the normal 512 MB configuration stays unchanged.

## Project Layout

- `python/geometrize_py/` contains the Python package, web server, static UI,
  image helpers, SVG exporter, CLI, and native backend wrapper.
- `cli_options.py` validates command-line settings, `cli_jobs.py` handles admitted
  jobs and output publication, and `project.py` validates saved experiment graphs.
- `python/geometrize_py/native_bindings.cpp` is the pybind11 bridge into the
  C++ core; `native_focus.h` and `native_palette.h` compose placement and color
  callbacks without changing the upstream engine.
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
