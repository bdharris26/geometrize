from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType, derive_output_path


@dataclass(frozen=True, slots=True)
class BatchManifest:
    path: Path
    jobs: list[GeometrizeJob]


def load_batch_manifest(path: Path) -> BatchManifest:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("batch manifest must contain a JSON object")

    if int(data.get("version", 1)) != 1:
        raise ValueError("unsupported batch manifest version")

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("batch manifest defaults must be a JSON object")

    jobs_data = data.get("jobs")
    if not isinstance(jobs_data, list) or not jobs_data:
        raise ValueError("batch manifest jobs must be a non-empty list")

    base_dir = path.parent
    jobs: list[GeometrizeJob] = []
    for index, job_data in enumerate(jobs_data):
        if not isinstance(job_data, dict):
            raise ValueError(f"batch manifest job {index} must be a JSON object")
        merged = {**defaults, **job_data}
        jobs.append(_job_from_mapping(merged, base_dir, index))

    return BatchManifest(path=path, jobs=jobs)


def _job_from_mapping(data: dict[str, Any], base_dir: Path, index: int) -> GeometrizeJob:
    if "input_path" not in data:
        raise ValueError(f"batch manifest job {index} is missing required field: input_path")

    shape = ShapeType.from_cli(str(data.get("shape", ShapeType.ELLIPSE.value)))
    count = int(data["count"]) if "count" in data else None
    if count is None:
        raise ValueError(f"batch manifest job {index} is missing required field: count")

    export_format = ExportFormat.from_cli(str(data.get("export_format", ExportFormat.PNG.value)))
    input_path = _resolve_manifest_path(base_dir, data["input_path"])
    output_path = (
        _resolve_manifest_path(base_dir, data["output_path"])
        if "output_path" in data
        else derive_output_path(input_path, shape, count, export_format)
    )

    return GeometrizeJob(
        input_path=input_path,
        output_path=output_path,
        shape=shape,
        count=count,
        export_format=export_format,
        alpha=int(data.get("alpha", 128)),
        candidate_shape_count=int(data.get("candidate_shape_count", 50)),
        max_shape_mutations=int(data.get("max_shape_mutations", 100)),
        seed=int(data.get("seed", 9001)),
        max_threads=int(data.get("max_threads", 0)),
    )


def _resolve_manifest_path(base_dir: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return base_dir / path
