from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def default_screenshot_roots() -> list[Path]:
    roots: list[Path] = []
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.append(Path(user_profile) / "Pictures" / "Screenshots")

    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        one_drive = os.environ.get(env_name)
        if one_drive:
            roots.append(Path(one_drive) / "Pictures" / "Screenshots")

    return _dedupe_existing_roots(roots)


def find_latest_screenshot(roots: Iterable[Path] | None = None) -> Path | None:
    search_roots = list(roots) if roots is not None else default_screenshot_roots()
    latest_path: Path | None = None
    latest_mtime = float("-inf")

    for root in search_roots:
        for candidate in _iter_screenshot_candidates(Path(root)):
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_path = candidate
                latest_mtime = mtime

    return latest_path


def _iter_screenshot_candidates(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    if root.is_file():
        if _looks_like_screenshot(root):
            yield root
        return

    try:
        paths = root.rglob("*")
        for path in paths:
            if path.is_file() and _looks_like_screenshot(path):
                yield path
    except OSError:
        return


def _looks_like_screenshot(path: Path) -> bool:
    if path.suffix.lower() not in SCREENSHOT_EXTENSIONS:
        return False
    return "screenshot" in path.name.lower() or path.parent.name.lower() == "screenshots"


def _dedupe_existing_roots(paths: Iterable[Path]) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved not in seen and path.exists():
            roots.append(path)
            seen.add(resolved)
    return roots
