from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from geometrize_py.jobs import ExportFormat, GeometrizeJob, ShapeType


REQUIRED_JOB_FIELDS = ("input_path", "output_path", "shape", "count")


def load_job(path: Path) -> GeometrizeJob:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("job manifest must contain a JSON object")

    for field in REQUIRED_JOB_FIELDS:
        if field not in data:
            raise ValueError(f"job manifest is missing required field: {field}")

    base_dir = path.parent
    return GeometrizeJob(
        input_path=_resolve_manifest_path(base_dir, data["input_path"]),
        output_path=_resolve_manifest_path(base_dir, data["output_path"]),
        shape=ShapeType.from_cli(str(data["shape"])),
        count=int(data["count"]),
        export_format=ExportFormat.from_cli(str(data.get("export_format", ExportFormat.PNG.value))),
        alpha=int(data.get("alpha", 128)),
        candidate_shape_count=int(data.get("candidate_shape_count", 50)),
        max_shape_mutations=int(data.get("max_shape_mutations", 100)),
        seed=int(data.get("seed", 9001)),
        max_threads=int(data.get("max_threads", 0)),
    )


def save_job(job: GeometrizeJob, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job_to_dict(job), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_manifest_path(base_dir: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return base_dir / path


def job_to_dict(job: GeometrizeJob) -> dict[str, Any]:
    return {
        "input_path": str(job.input_path),
        "output_path": str(job.output_path),
        "shape": job.shape.cli_name,
        "count": job.count,
        "export_format": job.export_format.value,
        "alpha": job.alpha,
        "candidate_shape_count": job.candidate_shape_count,
        "max_shape_mutations": job.max_shape_mutations,
        "seed": job.seed,
        "max_threads": job.max_threads,
    }
