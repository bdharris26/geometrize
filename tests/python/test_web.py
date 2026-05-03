from __future__ import annotations

import json
import threading
import urllib.request

import pytest
from PIL import Image

from geometrize_py.images import image_to_data_url
from geometrize_py.native import native_available
from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer


def test_health_endpoint_reports_native_state() -> None:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"ok": True, "native": native_available()}
    finally:
        server.shutdown()
        server.server_close()


def test_index_supports_sample_without_required_file_input() -> None:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert 'id="sample-button"' in html
        assert 'id="image-input" type="file" accept="image/*">' in html
        assert 'id="max-size" name="max_size" type="range" min="64" max="8192" step="64" value="1024"' in html
        assert 'id="max-size-number" type="number" min="64" max="8192" step="64" value="1024"' in html
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_run_endpoint_returns_rendered_image() -> None:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image = Image.new("RGBA", (6, 6), (50, 100, 180, 255))
        body = json.dumps(
            {
                "image": image_to_data_url(image),
                "options": {"steps": 1, "shape_types": ["ellipse"], "shape_count": 5, "mutations": 5},
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
        assert payload["preview"].startswith("data:image/png;base64,")
        assert payload["svg"].startswith('<?xml version="1.0"')
        assert payload["shape_count"] == len(payload["shapes"])
        assert payload["attempts"] >= 1
        if payload["shapes"]:
            assert "score" in payload["shapes"][0]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not native_available(), reason="native backend is not built")
def test_stream_endpoint_returns_progress_events() -> None:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image = Image.new("RGBA", (6, 6), (80, 120, 40, 255))
        body = json.dumps(
            {
                "image": image_to_data_url(image),
                "options": {"steps": 2, "shape_types": ["ellipse"], "shape_count": 5, "mutations": 5},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/run/stream",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            events = [json.loads(line) for line in response.read().decode("utf-8").splitlines()]
        assert [event["event"] for event in events] == ["start", "step", "step", "complete"]
        assert events[0]["width"] == 6
        assert events[-1]["attempts"] == 2
        assert events[-1]["preview"].startswith("data:image/png;base64,")
    finally:
        server.shutdown()
        server.server_close()
