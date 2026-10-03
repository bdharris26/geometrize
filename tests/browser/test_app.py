from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, expect, sync_playwright

from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer


@pytest.fixture
def server_url() -> Iterator[str]:
    server = GeometrizeServer(("127.0.0.1", 0), GeometrizeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_sample_continue_and_project_round_trip(server_url: str, tmp_path: Path) -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: console_errors.append(message.text) if message.type == "error" else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(server_url, wait_until="networkidle")

        source_buffer = io.BytesIO()
        Image.new("RGB", (3, 2), (20, 80, 160)).save(source_buffer, format="PNG")
        page.locator("#image-input").set_input_files(
            {
                "name": "source.unknown",
                "mimeType": "",
                "buffer": source_buffer.getvalue(),
            }
        )
        expect(page.locator("#status")).to_have_text("Ready")
        expect(page.locator("#save-project-button")).to_be_enabled()
        unknown_project_path = tmp_path / "unknown-source.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(unknown_project_path)
        unknown_project = json.loads(unknown_project_path.read_text(encoding="utf-8"))
        assert unknown_project["source"]["data_url"].startswith("data:image/png;base64,")
        page.locator("#project-input").set_input_files(unknown_project_path)
        expect(page.locator("#status")).to_have_text(
            "Project loaded — Run starts a new render",
            timeout=30_000,
        )

        _run_sample(page, shapes=4)
        expect(page.locator("#max-size")).to_be_disabled()
        expect(page.locator("#max-size-number")).to_be_disabled()
        expect(page.locator("#export-size")).to_be_enabled()
        page.locator("#steps").fill("2")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_role("button", name="Continue", exact=True).wait_for(timeout=120_000)
        assert "6 accepted" in page.locator("#telemetry-acceptance").inner_text()

        page.locator(".advanced-controls summary").click()
        page.locator("#alpha").fill("0")
        page.locator("#seed").fill(str(2**31))
        page.locator("#shape-count").fill("")
        page.locator("#mutations").fill("")
        project_path = tmp_path / "sample.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(project_path)

        project = json.loads(project_path.read_text(encoding="utf-8"))
        assert project["format"] == "geometrize-project"
        assert project["version"] == 1
        assert project["options"]["max_size"] == 128
        assert project["options"]["export_size"] == 256
        assert project["options"]["alpha"] == 1
        assert project["options"]["seed"] == 2**31 - 1
        assert project["options"]["shape_count"] == 64
        assert project["options"]["mutations"] == 128
        assert len(project["result"]["shapes"]) == 6
        expect(page.locator("#alpha")).to_have_value("1")
        expect(page.locator("#seed")).to_have_value(str(2**31 - 1))
        expect(page.locator("#shape-count")).to_have_value("64")
        expect(page.locator("#mutations")).to_have_value("128")

        page.reload(wait_until="networkidle")
        page.locator("#project-input").set_input_files(project_path)
        expect(page.locator("#status")).to_have_text(
            "Project loaded — Run starts a new render",
            timeout=30_000,
        )

        assert page.locator("#run-button").inner_text() == "Run"
        expect(page.locator("#max-size")).to_have_value("128")
        expect(page.locator("#export-size")).to_have_value("256")
        assert page.locator("#download-png").get_attribute("aria-disabled") == "false"
        assert page.locator("#download-json").get_attribute("aria-disabled") == "false"

        shapes_path = tmp_path / "shapes.json"
        shapes_path.write_text("[]", encoding="utf-8")
        page.locator("#project-input").set_input_files(shapes_path)
        expect(page.locator("#status")).to_have_text(
            "Could not open project: This is a Shapes JSON export, not a Geometrize project",
        )
        expect(page.locator("#max-size")).to_have_value("128")

        invalid_radius_project = json.loads(json.dumps(project))
        invalid_radius_project["result"]["preview_data_url"] = None
        invalid_radius_project["result"]["shapes"] = [
            {
                "type": "circle",
                "color": {"r": 0, "g": 0, "b": 0, "a": 255},
                "data": {"x": 10, "y": 10, "r": -1},
            }
        ]
        invalid_radius_path = tmp_path / "invalid-radius.geometrize-project.json"
        invalid_radius_path.write_text(json.dumps(invalid_radius_project), encoding="utf-8")
        page.locator("#project-input").set_input_files(invalid_radius_path)
        expect(page.locator("#status")).to_have_text(
            "Could not open project: Shape 1 r cannot be negative",
        )
        expect(page.locator("#max-size")).to_have_value("128")
        expect(page.locator("#image-name")).to_have_text(project["source"]["name"])

        browser.close()

    assert console_errors == []
    assert page_errors == []


def test_pause_keeps_controls_locked_until_final_snapshot(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        page.get_by_role("button", name="Sample", exact=True).click()
        page.locator("#steps-number").fill("512")
        page.locator("#max-size-number").fill("128")
        page.locator(".advanced-controls summary").click()
        page.locator("#max-threads").fill("1")
        page.get_by_role("button", name="Run", exact=True).click()
        expect(page.get_by_role("button", name="Pause", exact=True)).to_be_enabled(timeout=30_000)
        expect(page.locator("#image-input")).to_be_disabled()
        expect(page.get_by_role("button", name="Sample", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Pause", exact=True).click()
        pause_state = page.evaluate("""() => ({
          status: document.querySelector('#status').textContent,
          sourceDisabled: document.querySelector('#image-input').disabled
        })""")
        assert "Paus" in pause_state["status"]
        if "Pausing" in pause_state["status"]:
            assert pause_state["sourceDisabled"]
        expect(page.locator("#telemetry-state")).to_have_text("Paused", timeout=120_000)
        expect(page.locator("#image-input")).to_be_enabled()
        expect(page.get_by_role("button", name="Sample", exact=True)).to_be_enabled()
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#download-png")).to_have_attribute("aria-disabled", "false")

        project_path = tmp_path / "paused.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(project_path)
        project = json.loads(project_path.read_text(encoding="utf-8"))
        batch = project["telemetry"]["batches"][-1]
        assert batch["added"] == len(project["result"]["shapes"])
        assert batch["attempts"] == project["telemetry"]["attempts"]

        browser.close()


def test_exports_follow_resolution_without_adding_shapes(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        _run_sample(page, shapes=3)

        initial_acceptance = page.locator("#telemetry-acceptance").inner_text()
        first_path = tmp_path / "first.png"
        with page.expect_download() as download_info:
            page.locator("#download-png").click()
        download_info.value.save_as(first_path)
        with Image.open(first_path) as image:
            assert image.size[0] == 256

        page.locator("#export-size-number").fill("333")
        odd_path = tmp_path / "odd-size.png"
        with page.expect_download() as download_info:
            page.locator("#download-png").click()
        download_info.value.save_as(odd_path)
        with Image.open(odd_path) as image:
            assert image.size[0] == 333
        assert page.locator("#telemetry-acceptance").inner_text() == initial_acceptance

        page.locator("#export-size-number").fill("512")
        second_path = tmp_path / "second.png"
        with page.expect_download() as download_info:
            page.locator("#download-png").click()
        download_info.value.save_as(second_path)
        with Image.open(second_path) as image:
            assert image.size[0] == 512
        assert page.locator("#telemetry-acceptance").inner_text() == initial_acceptance

        project_path = tmp_path / "export.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(project_path)
        page.reload(wait_until="networkidle")
        page.locator("#project-input").set_input_files(project_path)
        expect(page.locator("#status")).to_have_text("Project loaded — Run starts a new render")
        expect(page.locator("#download-svg")).to_have_attribute("aria-disabled", "false")
        svg_path = tmp_path / "restored.svg"
        with page.expect_download() as download_info:
            page.locator("#download-svg").click()
        download_info.value.save_as(svg_path)
        assert 'width="512"' in svg_path.read_text(encoding="utf-8")

        browser.close()


def test_stale_export_completion_does_not_change_new_scene_status(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        _run_sample(page, shapes=1)
        page.evaluate("""() => {
          const realFetch = window.fetch.bind(window);
          window.fetch = (...args) => {
            if (args[0] === '/api/export') {
              return new Promise(resolve => { window.resolveOldExport = resolve; });
            }
            return realFetch(...args);
          };
        }""")
        page.locator("#download-png").click()
        page.wait_for_function("typeof window.resolveOldExport === 'function'")
        expect(page.locator("#status")).to_have_text("Preparing 256px export")

        page.get_by_role("button", name="Sample", exact=True).click()
        expect(page.locator("#status")).to_have_text("Ready")
        page.evaluate("""() => window.resolveOldExport(new Response('{}', {
          status: 200, headers: { 'Content-Type': 'application/json' }
        }))""")
        page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
        expect(page.locator("#status")).to_have_text("Ready")
        browser.close()


def test_controls_zoom_and_incomplete_stream(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(server_url, wait_until="networkidle")
        assert page.locator("input[name='shape']").count() == 9
        assert ".tif" not in page.locator("#image-input").get_attribute("accept")
        page.locator("#image-input").set_input_files({
            "name": "unsupported.tiff", "mimeType": "image/tiff", "buffer": b"TIFF",
        })
        expect(page.locator("#status")).to_have_text(
            "Could not load image: Choose a PNG, JPEG, WebP, BMP, or GIF image"
        )
        page.locator("#preset").select_option("quick")
        expect(page.locator("#steps-number")).to_have_value("64")
        expect(page.locator("#shape-count")).to_have_value("16")
        expect(page.locator("#mutations")).to_have_value("32")
        expect(page.locator("#max-size-number")).to_have_value("256")

        _run_sample(page, shapes=1)
        boxes = page.evaluate(
            """() => {
              const source = document.querySelector('#source-preview').getBoundingClientRect();
              const result = document.querySelector('#result-canvas').getBoundingClientRect();
              return [source.width, source.height, result.width, result.height];
            }"""
        )
        assert abs(boxes[0] - boxes[2]) < 3
        assert abs(boxes[1] - boxes[3]) < 3
        page.get_by_role("button", name="Zoom in").click()
        expect(page.locator("#zoom-value")).to_have_text("125%")
        source_stage = page.locator("#source-stage").bounding_box()
        assert source_stage
        page.mouse.move(source_stage["x"] + 100, source_stage["y"] + 100)
        page.mouse.down()
        page.mouse.move(source_stage["x"] + 130, source_stage["y"] + 110)
        page.mouse.up()
        transform = page.evaluate(
            """() => [
              document.querySelector('#source-preview').style.transform,
              document.querySelector('#result-canvas').style.transform
            ]"""
        )
        assert transform[0] == transform[1]
        assert "lower is better" in page.locator("#telemetry-improvement").inner_text()

        page.route("**/api/run/stream", lambda route: route.fulfill(
            status=200,
            content_type="application/x-ndjson",
            body=json.dumps({
                "event": "start", "session_id": "missing-session", "width": 8, "height": 8,
                "background": {"r": 0, "g": 0, "b": 0, "a": 255}, "shapes": [],
                "attempts": 0, "initial_score": 0.5,
            }) + "\n",
        ))
        page.route("**/api/sessions/missing-session/snapshot", lambda route: route.fulfill(
            status=404, content_type="application/json",
            body=json.dumps({"code": "unknown_session", "error": "Unknown render session"}),
        ))
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(page.locator("#telemetry-state")).to_have_text("Error")
        expect(page.locator("#run-button")).to_have_text("Run")
        expect(page.locator("#download-png")).to_have_attribute("aria-disabled", "false")
        assert "ended before a final result" in page.locator("#status").inner_text()
        with page.expect_download() as download_info:
            page.locator("#download-png").click()
        assert download_info.value.suggested_filename == "geometrize.png"
        page.get_by_role("button", name="New render", exact=True).click()
        expect(page.locator("#download-png")).to_have_attribute("aria-disabled", "true")

        browser.close()


@pytest.mark.parametrize(
    ("stop_reason", "expected_state", "expected_status"),
    [
        ("paused", "Paused", "Stream interrupted; recovered final result"),
        ("error", "Render failed", "Render failed; recovered the last confirmed result"),
    ],
)
def test_incomplete_stream_reconciles_from_idle_snapshot(
    server_url: str, stop_reason: str, expected_state: str, expected_status: str
) -> None:
    first = {
        "type": "circle", "color": {"r": 0, "g": 0, "b": 0, "a": 255},
        "data": {"x": 2, "y": 2, "r": 1}, "score": 0.7,
    }
    second = {
        "type": "circle", "color": {"r": 0, "g": 0, "b": 0, "a": 255},
        "data": {"x": 5, "y": 5, "r": 1}, "score": 0.5,
    }
    background = {"r": 255, "g": 255, "b": 255, "a": 255}
    events = [
        {
            "event": "start", "session_id": "recoverable", "run_id": "one-run",
            "width": 8, "height": 8, "background": background,
            "shapes": [], "attempts": 0, "revision": 0, "initial_score": 0.9,
        },
        {
            "event": "step", "shapes": [first], "attempts": 6, "revision": 1,
            "batch_shape_count": 1, "batch_goal": 2,
        },
    ]
    snapshot = {
        "event": "snapshot", "session_id": "recoverable", "width": 8, "height": 8,
        "background": background, "shapes": [first, second], "attempts": 7,
        "revision": 2, "initial_score": 0.9, "score": 0.5,
        "stop_reason": stop_reason,
        "batch_summary": {
            "index": 1, "target": 2, "shapeTypes": ["circle"],
            "candidates": 16, "mutations": 32, "alpha": 128, "seed": 9001,
            "max_threads": 0, "effective_threads": 1,
            "start_shape_count": 0, "start_attempts": 0,
            "added": 2, "attempts": 7, "state": expected_state, "reason": stop_reason,
        },
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        page.get_by_role("button", name="Sample", exact=True).click()
        page.route("**/api/run/stream", lambda route: route.fulfill(
            status=200, content_type="application/x-ndjson",
            body="\n".join(json.dumps(event) for event in events) + "\n",
        ))
        page.route("**/api/sessions/recoverable/snapshot", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(snapshot),
        ))
        page.get_by_role("button", name="Run", exact=True).click()
        expect(page.locator("#status")).to_have_text(expected_status)
        expect(page.locator("#telemetry-state")).to_have_text(expected_state)
        expect(page.locator("#telemetry-acceptance")).to_have_text("2 accepted / 7 attempts")
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#batch-history .batch-chip strong")).to_have_text("+2")
        browser.close()


def test_failed_recovery_saves_only_the_last_confirmed_scene(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        _run_sample(page, shapes=3)
        original_path = tmp_path / "confirmed.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(original_path)
        original = json.loads(original_path.read_text(encoding="utf-8"))

        page.reload(wait_until="networkidle")
        page.locator("#project-input").set_input_files(original_path)
        expect(page.locator("#status")).to_have_text("Project loaded — Run starts a new render")
        confirmed_preview = page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()")
        confirmed_acceptance = page.locator("#telemetry-acceptance").inner_text()
        confirmed_history = page.locator("#batch-history").inner_text()

        width = original["result"]["render_width"]
        height = original["result"]["render_height"]
        start = {
            "event": "start", "session_id": "unconfirmed", "run_id": "broken-run",
            "continued": False, "width": width, "height": height,
            "background": {"r": 255, "g": 255, "b": 255, "a": 255},
            "shapes": [], "attempts": 0, "revision": 0, "initial_score": 0.9,
        }
        step = {
            "event": "step", "shapes": [{
                "type": "rectangle", "color": {"r": 0, "g": 0, "b": 0, "a": 255},
                "data": {"x1": 0, "y1": 0, "x2": width, "y2": height}, "score": 0.5,
            }],
            "attempts": 1, "revision": 1, "batch_shape_count": 1, "batch_goal": 2,
        }
        page.route("**/api/run/stream", lambda route: route.fulfill(
            status=200, content_type="application/x-ndjson",
            body=f"{json.dumps(start)}\n{json.dumps(step)}\n",
        ))
        page.route("**/api/sessions/unconfirmed/snapshot", lambda route: route.fulfill(
            status=503, content_type="application/json",
            body=json.dumps({"code": "snapshot_unavailable", "error": "Snapshot unavailable"}),
        ))
        page.get_by_role("button", name="Run", exact=True).click()
        expect(page.locator("#run-button")).to_have_text("Recover result")
        expect(page.locator("#telemetry-state")).to_have_text("Error")
        assert page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()") == confirmed_preview
        assert page.locator("#telemetry-acceptance").inner_text() == confirmed_acceptance
        assert page.locator("#batch-history").inner_text() == confirmed_history

        saved_path = tmp_path / "after-failed-recovery.geometrize-project.json"
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save project", exact=True).click()
        download_info.value.save_as(saved_path)
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        assert saved["result"]["shapes"] == original["result"]["shapes"]
        assert saved["result"]["preview_data_url"] == original["result"]["preview_data_url"]
        assert saved["result"]["render_width"] == original["result"]["render_width"]
        assert saved["result"]["render_height"] == original["result"]["render_height"]
        assert [saved["result"]["background"][key] for key in ("r", "g", "b", "a")] == original["result"]["background"]
        assert saved["telemetry"] == original["telemetry"]
        browser.close()


def _run_sample(page: Page, shapes: int) -> None:
    expect(page.locator("#native-state")).to_have_text("Core ready")
    page.get_by_role("button", name="Sample", exact=True).click()
    page.locator("#steps").fill(str(shapes))
    page.locator("#max-size").fill("128")
    page.locator("#export-size").fill("256")
    page.get_by_role("button", name="Run", exact=True).click()
    page.get_by_role("button", name="Continue", exact=True).wait_for(timeout=120_000)

    assert page.locator("#download-png").get_attribute("aria-disabled") == "false"
    assert page.locator("#download-svg").get_attribute("aria-disabled") == "false"
    assert page.locator("#download-json").get_attribute("aria-disabled") == "false"
