# Geometrize Python Port

Goal: move the headless and app-orchestration layers toward Python while
keeping the existing C++ image approximation algorithm as the native core.

This package is intentionally thin at first. It owns CLI parsing, job
configuration, screenshot discovery, and the boundary where a native runner will
plug in later.

Install the package into the repo-local venv:

```powershell
python -m venv C:\LocalRepos\geometrize\.venv
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m pip install -e C:\LocalRepos\geometrize
```

Run the current test suite:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m unittest discover -s C:\LocalRepos\geometrize\tests\python
```

Preview a 4000-triangle job without invoking the native core:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\geometrize-py.exe run --latest-screenshot --output C:\LocalRepos\geometrize\outputs\screenshot_4000_triangles.png --shape triangle --count 4000 --dry-run
```
