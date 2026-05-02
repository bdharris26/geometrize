# Geometrize Python Port

Goal: move the headless and app-orchestration layers toward Python while
keeping the existing C++ image approximation algorithm as the native core.

This package owns CLI parsing, job configuration, screenshot discovery, Python
image IO, and a small pybind11 bridge to the native `lib/geometrize`
`ImageRunner`. It does not port the shape-fitting algorithm itself.

Install the package into the repo-local venv:

```powershell
python -m venv C:\LocalRepos\geometrize\.venv
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m pip install -e C:\LocalRepos\geometrize
```

The editable install builds the native extension with `scikit-build-core`,
`pybind11`, CMake, and the local C++ compiler.

Run the current test suite:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m unittest discover -s C:\LocalRepos\geometrize\tests\python
```

Preview a 4000-triangle job without invoking the native core:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\geometrize-py.exe run --latest-screenshot --output C:\LocalRepos\geometrize\outputs\screenshot_4000_triangles.png --shape triangle --count 4000 --dry-run
```

Run a real native-core job:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\geometrize-py.exe run --latest-screenshot --output C:\LocalRepos\geometrize\outputs\screenshot_4000_triangles.png --shape triangle --count 4000
```

Write reusable shape data instead of a PNG:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\geometrize-py.exe run --latest-screenshot --output C:\LocalRepos\geometrize\outputs\screenshot_4000_triangles.json --shape triangle --count 4000 --export-format json
```

Run from a JSON job manifest:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\geometrize-py.exe run --job C:\LocalRepos\geometrize\job.json --dry-run
```
