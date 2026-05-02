from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ShapeType(Enum):
    RECTANGLE = "rectangle"
    ROTATED_RECTANGLE = "rotated_rectangle"
    TRIANGLE = "triangle"
    ELLIPSE = "ellipse"
    ROTATED_ELLIPSE = "rotated_ellipse"
    CIRCLE = "circle"
    LINE = "line"
    QUADRATIC_BEZIER = "quadratic_bezier"
    POLYLINE = "polyline"

    @classmethod
    def from_cli(cls, value: str) -> "ShapeType":
        normalized = value.strip().lower().replace("-", "_")
        for shape in cls:
            if shape.value == normalized:
                return shape
        choices = ", ".join(shape.cli_name for shape in cls)
        raise ValueError(f"unknown shape {value!r}; expected one of: {choices}")

    @property
    def cli_name(self) -> str:
        return self.value.replace("_", "-")

    @classmethod
    def cli_choices(cls) -> list[str]:
        choices: list[str] = []
        for shape in cls:
            choices.append(shape.cli_name)
            if shape.cli_name != shape.value:
                choices.append(shape.value)
        return choices


class ExportFormat(Enum):
    PNG = "png"
    SVG = "svg"
    JSON = "json"

    @classmethod
    def from_cli(cls, value: str) -> "ExportFormat":
        normalized = value.strip().lower().lstrip(".")
        for export_format in cls:
            if export_format.value == normalized:
                return export_format
        choices = ", ".join(format_.value for format_ in cls)
        raise ValueError(f"unknown export format {value!r}; expected one of: {choices}")


@dataclass(slots=True)
class GeometrizeJob:
    input_path: Path
    output_path: Path
    shape: ShapeType
    count: int
    export_format: ExportFormat = ExportFormat.PNG
    alpha: int = 128
    candidate_shape_count: int = 50
    max_shape_mutations: int = 100
    seed: int = 9001
    max_threads: int = 0

    def __post_init__(self) -> None:
        self.input_path = Path(self.input_path)
        self.output_path = Path(self.output_path)
        self.shape = self.shape if isinstance(self.shape, ShapeType) else ShapeType.from_cli(str(self.shape))
        self.export_format = (
            self.export_format
            if isinstance(self.export_format, ExportFormat)
            else ExportFormat.from_cli(str(self.export_format))
        )
        self._validate_positive("count", self.count)
        self._validate_alpha()
        self._validate_positive("candidate_shape_count", self.candidate_shape_count)
        self._validate_positive("max_shape_mutations", self.max_shape_mutations)
        self._validate_non_negative("seed", self.seed)
        self._validate_non_negative("max_threads", self.max_threads)

    def as_plan(self, runner: str) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "shape": self.shape.cli_name,
            "count": self.count,
            "export_format": self.export_format.value,
            "alpha": self.alpha,
            "candidate_shape_count": self.candidate_shape_count,
            "max_shape_mutations": self.max_shape_mutations,
            "seed": self.seed,
            "max_threads": self.max_threads,
            "runner": runner,
        }

    def _validate_alpha(self) -> None:
        if not 0 <= self.alpha <= 255:
            raise ValueError("alpha must be between 0 and 255")

    @staticmethod
    def _validate_positive(name: str, value: int) -> None:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")

    @staticmethod
    def _validate_non_negative(name: str, value: int) -> None:
        if value < 0:
            raise ValueError(f"{name} must be greater than or equal to zero")


def derive_output_path(input_path: Path, shape: ShapeType, count: int, export_format: ExportFormat) -> Path:
    input_path = Path(input_path)
    suffix = f"_geometrized_{count}_{shape.cli_name.replace('-', '_')}.{export_format.value}"
    return input_path.with_name(input_path.stem + suffix)
