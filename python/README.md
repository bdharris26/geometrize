# Python Package Notes

The current Python package is `geometrize_py`. The root `README.md` is the
source of truth for setup, CLI usage, and development commands.

Useful package entrypoints:

```powershell
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m geometrize_py serve --host 127.0.0.1 --port 7860
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m geometrize_py doctor
C:\LocalRepos\geometrize\.venv\Scripts\python.exe -m pytest
```

Keep this note short; implementation details should live beside the code or in
tests rather than in migration-era docs.
