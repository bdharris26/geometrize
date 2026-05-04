from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from urllib.error import HTTPError

import pytest
from PIL import Image

from geometrize_py.images import image_to_data_url
from geometrize_py.native import native_available
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer


@pytest.fixture
def server() -> Iterator[GeometrizeServer]:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_health_endpoint_reports_native_state(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload == {"ok": True, "native": native_available()}


def test_index_supports_sample_without_required_file_input(server: GeometrizeServer) -> None:
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
        html = response.read().decode("utf-8")
    assert 'id="sample-button"' in html
    assert 'id="image-input" type="file" accept="image/*">' in html
    assert 'id="image-name"' in html
    assert 'id="steps" name="steps" type="range" min="1" max="4096" value="128"' in html
    assert "Shapes to add" in html
    assert "Candidates per shape" in html
    assert "Mutations per candidate" in html
    assert "Longest dimension" in html
    assert 'id="max-size" name="max_size" type="range" min="64" max="8192" step="64" value="1024"' in html
    assert 'id="max-size-number" type="number" min="64" max="8192" step="64" value="1024"' in html
    assert 'id="pause-button"' in html
    assert 'class="telemetry-console"' in html
    assert 'id="result-canvas"' in html
    assert 'id="score-graph"' in html
    assert 'id="impact-graph"' in html
    assert 'id="telemetry-acceptance"' in html


def test_static_route_rejects_path_traversal(server: GeometrizeServer) -> None:
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/static/%2e%2e/web.py", timeout=5)
    assert error.value.code == 404


def test_run_endpoint_rejects_non_object_json(server: GeometrizeServer) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run",
        data=b"[]",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert payload == {"error": "Expected a JSON object"}


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_run_endpoint_returns_rendered_image(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (6, 6), (50, 100, 180, 255))
    body = json.dumps(
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 1,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 6,
                "export_size": 64,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["width"] == 6
    assert payload["height"] == 6
    assert payload["export_width"] == 64
    assert payload["export_height"] == 64
    assert payload["preview"].startswith("data:image/png;base64,")
    assert payload["svg"].startswith('<?xml version="1.0"')
    assert 'width="64" height="64" viewBox="0 0 6 6"' in payload["svg"]
    assert payload["shape_count"] == len(payload["shapes"])
    assert payload["attempts"] >= 1
    if payload["shapes"]:
        assert "score" in payload["shapes"][0]


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_endpoint_returns_progress_events(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (6, 6), (80, 120, 40, 255))
    events = _post_stream(
        server,
        {
            "image": image_to_data_url(image),
            "options": {
                "steps": 2,
                "shape_types": ["ellipse"],
                "shape_count": 5,
                "mutations": 5,
                "max_size": 6,
                "export_size": 64,
            },
        },
    )
    assert events[0]["event"] == "start"
    assert events[0]["session_id"]
    assert events[0]["continued"] is False
    assert events[-1]["event"] == "complete"
    assert any(event["event"] == "step" for event in events)
    assert events[0]["width"] == 6
    assert events[-1]["attempts"] >= 2
    assert events[-1]["export_width"] == 64
    assert events[-1]["preview"].startswith("data:image/png;base64,")


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_endpoint_can_continue_session_with_new_shape_options(server: GeometrizeServer) -> None:
    image = Image.new("RGBA", (12, 12), (30, 40, 50, 255))
    for x in range(6, 12):
        for y in range(12):
            image.putpixel((x, y), (220, 180, 80, 255))

    first_events = _post_stream(
        server,
        {
            "image": image_to_data_url(image),
            "options": {"steps": 1, "shape_types": ["rectangle"], "shape_count": 8, "mutations": 8},
        },
    )
    session_id = first_events[0]["session_id"]
    first_count = first_events[-1]["shape_count"]

    second_events = _post_stream(
        server,
        {
            "session_id": session_id,
            "options": {"steps": 1, "shape_types": ["triangle"], "shape_count": 8, "mutations": 8},
        },
    )

    assert second_events[0]["continued"] is True
    assert second_events[0]["shape_count"] == first_count
    assert second_events[-1]["shape_count"] >= first_count
    assert second_events[-1]["session_id"] == session_id


def _post_stream(server: GeometrizeServer, payload: dict) -> list[dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/run/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return [json.loads(line) for line in response.read().decode("utf-8").splitlines()]
