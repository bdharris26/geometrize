# AGENTS.md

This file is a quick orientation guide for agents entering this repo. It should
help you find useful work, not freeze the project into its current habits.

## Project Shape

- `geometrize.pro` is the qmake entrypoint for the desktop app.
- First-party application code is under `geometrize/`.
- The core image approximation algorithm is pulled in from the `lib/geometrize`
  submodule and included by `lib/geometrize/geometrize/geometrize.pri`.
- Other submodules provide scripting, serialization, GIF export, templates, web
  export assets, and translations. Check `.gitmodules` before treating code
  under `lib/`, `resources/templates`, `resources/web_export`, or `translations`
  as local app ownership.
- `resources/resources.pri` runs Python scripts from `scripts/` to generate Qt
  resource files during qmake configuration.

## First Places To Inspect

- App launch and mode selection: `geometrize/main.cpp`,
  `geometrize/cli/commandlineparser.*`.
- Image task lifecycle and threading: `geometrize/task/imagetask.*`,
  `geometrize/task/imagetaskworker.*`, and UI connections in
  `geometrize/dialog/imagetaskwindow.*`.
- Scripting surface: `geometrize/script/chaiscriptcreator.cpp` and
  `geometrize/script/bindings/`.
- Import/export behavior: `geometrize/image/`, `geometrize/exporter/`,
  `geometrize/serialization/`.
- Preferences, templates, and localization: `geometrize/preferences/`,
  `geometrize/manifest/`, `geometrize/localization/`, `translations/`.

## Build And Verification

- Expected setup: Qt 5.10+ or Qt 6, Python 3, and initialized submodules.
- Bootstrap submodules with `git submodule update --init --recursive`.
- Typical CLI build shape, from a Qt-enabled shell:
  `qmake C:\LocalRepos\geometrize\geometrize.pro` then `nmake` or `make`.
- CI history lives in `.appveyor.yml` and builds Linux, macOS, and Windows MSVC
  variants.
- Functional self-tests exist through `--functional_tests <scripts-dir>`, but
  coverage appears sparse. If you cannot run GUI tests locally, say so and still
  verify targeted logic as directly as possible.

## Improvement-Friendly Notes

- Prefer small, behavior-focused changes in first-party app code before editing
  submodules.
- Be careful around `ImageTask`: it crosses Qt signals, worker threads,
  ChaiScript state, and bitmap lifetimes.
- When changing scripting bindings, update both engine construction and the
  exposed ChaiScript API intentionally.
- When touching images, exports, templates, or translations, check whether the
  resource generation step needs to run and whether generated files are ignored.
- There are useful cleanup candidates: CLI parsing duplication, TODOs in UI and
  network paths, generated resource determinism, richer functional tests, and
  clearer boundaries between GUI orchestration and task state.
- Keep new guidance and docs short. This project already has many moving parts;
  the next agent should be helped, not buried.
