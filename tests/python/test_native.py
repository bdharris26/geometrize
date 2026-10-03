from __future__ import annotations

import pytest
from PIL import Image

from geometrize_py.native import (
    MAX_IMAGE_SIZE,
    MAX_WORKING_IMAGE_SIZE,
    ImageSession,
    NativeBackendUnavailable,
    RunOptions,
    effective_max_threads,
    iter_image,
    native_available,
    run_image,
)
from geometrize_py.render import render_shapes_to_image


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_runner_returns_preview_and_shapes() -> None:
    image = Image.new("RGBA", (8, 8), (240, 30, 30, 255))
    image.putpixel((0, 0), (20, 20, 20, 255))
    result = run_image(image, RunOptions(steps=2, shape_types=("ellipse",), shape_count=10, mutations=10, max_size=64))

    assert result.width == 8
    assert result.height == 8
    assert result.attempts >= 1
    assert result.image.size == (8, 8)
    assert isinstance(result.shapes, list)


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_runner_streams_progress_events() -> None:
    image = Image.new("RGBA", (8, 8), (30, 120, 200, 255))
    image.putpixel((0, 0), (220, 20, 20, 255))
    options = RunOptions(
        steps=2,
        shape_types=("ellipse",),
        shape_count=10,
        mutations=10,
        max_size=64,
    )
    events = list(iter_image(image, options))

    assert events[0]["event"] == "start"
    assert events[-1]["event"] == "complete"
    assert any(event["event"] == "step" for event in events)
    assert events[0]["width"] == 8
    assert events[1]["attempt"] == 1
    assert events[-1]["result"].attempts >= 2


def test_native_unavailable_error_is_importable() -> None:
    assert issubclass(NativeBackendUnavailable, RuntimeError)


def test_run_options_keep_high_resolution_budget() -> None:
    assert RunOptions().max_size == 1024
    assert RunOptions().export_size == 1024
    assert MAX_WORKING_IMAGE_SIZE == 2048
    assert MAX_IMAGE_SIZE == 4096
    assert RunOptions.from_mapping({"max_size": 99999}).max_size == MAX_WORKING_IMAGE_SIZE
    assert RunOptions.from_mapping({"steps": 99999}).steps == 4096
    assert RunOptions.from_mapping({"export_size": 99999}).export_size == MAX_IMAGE_SIZE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("steps", 0),
        ("alpha", 256),
        ("shape_count", 0),
        ("mutations", 2049),
        ("seed", -1),
        ("max_threads", 129),
        ("max_size", 0),
        ("export_size", MAX_IMAGE_SIZE + 1),
    ],
)
def test_direct_run_options_reject_invalid_values(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        RunOptions(**{field: value})


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_transparent_source_uses_native_opaque_background() -> None:
    session = ImageSession.from_image(
        Image.new("RGBA", (2, 2), (200, 50, 10, 0)),
        RunOptions(steps=1, max_size=32, export_size=32),
    )

    result = session.result()
    reconstructed = render_shapes_to_image([], 2, 2, result.background, 2, 2)

    assert result.background == (200, 50, 10, 255)
    assert result.image.getpixel((0, 0)) == result.background
    assert reconstructed.getpixel((0, 0)) == result.background


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_native_score_starts_at_baseline_and_tracks_accepted_shape() -> None:
    image = Image.new("RGBA", (12, 12), (20, 50, 70, 255))
    for x in range(6, 12):
        for y in range(12):
            image.putpixel((x, y), (220, 180, 30, 255))
    session = ImageSession.from_image(image, RunOptions(steps=1, max_threads=1, shape_count=8, mutations=8))

    assert session.initial_score > 0
    assert session.score == session.initial_score
    events = list(session.run_batch(RunOptions(steps=1, max_threads=1, shape_count=8, mutations=8)))

    assert session.stop_reason in {"target_reached", "attempt_limit", "no_further_improvement"}
    assert session.score <= session.initial_score
    assert session.revision == len(session.shapes)
    if session.shapes:
        assert session.score == pytest.approx(session.shapes[-1]["score"])
        assert events[-1]["revision"] == session.revision


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_uniform_image_needs_no_candidate_attempts() -> None:
    session = ImageSession.from_image(Image.new("RGBA", (8, 8), (20, 40, 60, 255)), RunOptions(steps=1))

    assert session.initial_score == 0
    assert list(session.run_batch(RunOptions(steps=1))) == []
    assert session.attempts == 0
    assert session.stop_reason == "adequate_fit"


def test_automatic_thread_count_has_small_cpu_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("geometrize_py.native.os.cpu_count", lambda: 32)
    assert effective_max_threads(0) == 8
    assert effective_max_threads(3) == 3
    assert RunOptions().to_native_dict()["max_threads"] == 8


def test_perfect_fit_stops_before_any_native_step() -> None:
    fake = _FakeRunner(initial_score=0.0)
    session = ImageSession(1, 1, (0, 0, 0, 255), fake)

    assert list(session.run_batch(RunOptions(steps=1))) == []
    assert fake.attempts == 0
    assert session.stop_reason == "adequate_fit"
    assert session.batch_summary["state"] == "complete"


def test_rejection_streak_stops_batch_with_explicit_reason() -> None:
    fake = _FakeRunner()
    session = ImageSession(1, 1, (0, 0, 0, 255), fake)

    events = list(session.run_batch(RunOptions(steps=1, stagnation_limit=2)))

    assert len(events) == 2
    assert session.stop_reason == "no_further_improvement"
    assert session.batch_summary["attempts"] == 2
    assert session.batch_summary["added"] == 0


def test_attempt_limit_remains_available_when_stagnation_is_disabled() -> None:
    fake = _FakeRunner()
    session = ImageSession(1, 1, (0, 0, 0, 255), fake)

    events = list(session.run_batch(RunOptions(steps=1, stagnation_limit=0)))

    assert len(events) == 65
    assert session.stop_reason == "attempt_limit"


def test_cancellation_records_last_accepted_shape_and_can_resume() -> None:
    fake = _FakeRunner(accept_shapes=True)
    session = ImageSession(1, 1, (0, 0, 0, 255), fake)
    options = RunOptions(steps=3)

    assert session.try_acquire_run()
    try:
        session.prepare_run()
        batch = session.run_reserved_batch(options)
        first = next(batch)
        session.request_cancel()
        assert list(batch) == []
    finally:
        session.release_run()

    assert first["revision"] == 1
    assert session.revision == 1
    assert len(session.shapes) == 1
    assert session.stop_reason == "paused"
    assert session.batch_summary["state"] == "paused"
    assert session.batch_summary["added"] == 1

    resumed = list(session.run_batch(RunOptions(steps=1)))
    assert len(resumed) == 1
    assert session.stop_reason == "target_reached"
    assert session.revision == 2


def test_closed_stream_keeps_latest_shape_in_paused_summary() -> None:
    session = ImageSession(1, 1, (0, 0, 0, 255), _FakeRunner(accept_shapes=True))
    batch = session.run_batch(RunOptions(steps=3))

    first = next(batch)
    batch.close()

    assert first["revision"] == 1
    assert session.revision == 1
    assert session.stop_reason == "paused"
    assert session.batch_summary["state"] == "paused"
    assert session.batch_summary["added"] == 1


class _FakeRunner:
    def __init__(self, initial_score: float = 0.5, accept_shapes: bool = False) -> None:
        self.initial_score = initial_score
        self.score = initial_score
        self.attempts = 0
        self.accept_shapes = accept_shapes

    def step(self, _options: dict) -> dict:
        self.attempts += 1
        shapes = []
        if self.accept_shapes:
            self.score *= 0.9
            shapes.append({"score": self.score, "type": "rectangle", "data": {}, "color": {}})
        return {"attempt": self.attempts, "shapes": shapes}
