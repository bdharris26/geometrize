from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
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

        _run_sample(page, shapes=4)
        expect(page.locator("#max-size")).to_be_disabled()
        expect(page.locator("#max-size-number")).to_be_disabled()
        expect(page.locator("#export-size")).to_be_enabled()
        page.locator("#steps").fill("2")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_role("button", name="Continue", exact=True).wait_for(timeout=120_000)
        assert "6 accepted" in page.locator("#telemetry-acceptance").inner_text()

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

        browser.close()

    assert console_errors == []
    assert page_errors == []


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
