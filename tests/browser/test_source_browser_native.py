from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, PngImagePlugin
from playwright.sync_api import Browser, Page, expect


def _save(page: Page, path: Path) -> dict:
    with page.expect_download() as download:
        page.locator("#save-project-button").click()
    download.value.save_as(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _prepared(page: Page) -> Image.Image:
    data_url = page.locator("#source-preview").get_attribute("src")
    assert data_url and data_url.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA")


def _matte(image: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    return Image.alpha_composite(Image.new("RGBA", image.size, (*rgb, 255)), image)


def _run(page: Page) -> None:
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)


def _fitting_settings(page: Page) -> None:
    page.locator("#max-size-number").fill("64")
    page.locator("#steps-number").fill("2")
    page.locator(".advanced-controls summary").click()
    page.locator("#shape-count").fill("16")
    page.locator("#mutations").fill("24")
    page.locator("#max-threads").fill("1")


@pytest.mark.parametrize("matte", [(16, 32, 48), None])
def test_wrapped_project_source_prepares_restores_continues_and_preserves_original_bytes(
    server_url: str, tmp_path: Path, matte: tuple[int, int, int] | None,
    browser: Browser,
) -> None:
    original = Image.new("RGBA", (80, 40), (70, 90, 110, 128))
    ImageDraw.Draw(original).rectangle((0, 0, 35, 39), fill=(180, 110, 50, 255))
    ImageDraw.Draw(original).ellipse((45, 5, 75, 35), fill=(20, 170, 50, 255))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Original source", "Keep these encoded bytes, including PNG metadata")
    buffer = io.BytesIO()
    original.save(buffer, format="PNG", pnginfo=metadata)
    encoded = buffer.getvalue()
    payload = base64.b64encode(encoded).decode()
    wrapped_url = "data:image/png;base64," + "\r\n".join(
        payload[index:index + 76] for index in range(0, len(payload), 76)
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server_url, wait_until="networkidle")
    page.locator("#image-input").set_input_files({
        "name": "original.png", "mimeType": "image/png", "buffer": encoded,
    })
    expect(page.locator("#status")).to_have_text("Ready")
    page.locator(".source-controls summary").click()
    page.locator("#source-matte").select_option("custom" if matte else "none")
    if matte:
        page.locator("#source-matte-color").fill("#102030")
    page.locator("#source-apply").click()
    expect(page.locator("#status")).to_have_text("New render ready")
    _fitting_settings(page)
    _run(page)
    project = _save(page, tmp_path / "unwrapped.json")
    project["source"]["data_url"] = wrapped_url
    project_path = tmp_path / "wrapped.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    with page.expect_response("**/api/source/prepare") as preparation:
        page.locator("#project-input").set_input_files(project_path)
    assert preparation.value.status == 200
    assert preparation.value.request.post_data_json["image"] == wrapped_url
    expect(page.locator("#status")).to_contain_text("Restore or fork to continue")
    expected = _matte(original, matte) if matte else original
    assert _prepared(page).tobytes() == expected.tobytes()
    fitted = expected.copy()
    fitted.thumbnail((64, 64), Image.Resampling.LANCZOS)
    expected_digest = hashlib.sha256(fitted.tobytes()).hexdigest()
    loaded = _save(page, tmp_path / "loaded.json")
    assert loaded["source"]["data_url"] == wrapped_url

    with page.expect_response("**/api/restore") as restoration:
        page.locator("#experiment-fork").click()
    assert restoration.value.status == 200
    assert restoration.value.request.post_data_json["image"] == wrapped_url
    expect(page.locator("#status")).to_contain_text("ready to Continue")
    restored = _save(page, tmp_path / "restored.json")
    assert restored["result"]["restored_shape_count"] == len(project["result"]["shapes"])
    assert restored["telemetry"]["attempts"] == 0
    assert restored["result"]["background"] == project["result"]["background"]
    assert restored["result"]["target_digest"] == expected_digest
    with page.expect_request("**/api/run/stream") as continuation:
        _run(page)
    assert continuation.value.post_data_json["session_id"] == restoration.value.json()["session_id"]
    saved = _save(page, tmp_path / "continued.json")
    assert len(saved["result"]["shapes"]) > len(restored["result"]["shapes"])
    assert saved["options"]["source"] == {"frame": 0, "matte": list(matte) if matte else None}
    assert saved["source"]["data_url"] == wrapped_url
    saved_payload = saved["source"]["data_url"].split(",", 1)[1]
    assert base64.b64decode(saved_payload.replace("\r", "").replace("\n", ""), validate=True) == encoded
    assert errors == []


def test_native_transparent_source_metadata_mattes_and_canvas_background_roundtrip(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    original = Image.new("RGBA", (1600, 800), (200, 100, 50, 128))
    ImageDraw.Draw(original).rectangle((200, 100, 700, 600), fill=(20, 220, 80, 255))
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    encoded = buffer.getvalue()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(server_url, wait_until="networkidle")
    page.locator("#image-input").set_input_files({
        "name": "transparent.png", "mimeType": "image/png", "buffer": encoded,
    })
    expect(page.locator("#status")).to_have_text("Ready")
    expect(page.locator("#source-meta")).to_have_text("1600 x 800")
    prepared = _prepared(page)
    assert prepared.size == (1024, 512)
    assert prepared.getpixel((0, 0)) == _matte(original, (255, 255, 255)).getpixel((0, 0))
    page.locator(".source-controls summary").click()
    expect(page.locator("#source-frame")).to_be_disabled()
    _fitting_settings(page)
    _run(page)
    white = _save(page, tmp_path / "white.json")
    fitted = _matte(original, (255, 255, 255))
    fitted.thumbnail((64, 64), Image.Resampling.LANCZOS)
    assert white["result"]["target_digest"] == hashlib.sha256(fitted.tobytes()).hexdigest()
    assert white["source"]["data_url"] == "data:image/png;base64," + base64.b64encode(encoded).decode()
    page.locator("#source-matte").select_option("black")
    page.locator("#canvas-background").select_option("custom")
    page.locator("#canvas-background-color").fill("#123456")
    page.locator("#restart-button").click()
    expect(page.locator("#status")).to_have_text("New render ready")
    assert _prepared(page).getpixel((0, 0)) == _matte(original, (0, 0, 0)).getpixel((0, 0))
    _run(page)
    black = _save(page, tmp_path / "black.json")
    assert black["options"]["source"] == {"frame": 0, "matte": [0, 0, 0]}
    assert black["result"]["background"] == [18, 52, 86, 255]
    assert black["result"]["target_digest"] != white["result"]["target_digest"]
    assert black["history"]["branches"][0]["result"]["shapes"] == white["result"]["shapes"]
    page.locator("#source-matte").select_option("none")
    page.locator("#source-apply").click()
    expect(page.locator("#status")).to_have_text("New render ready")
    none_preview = original.copy()
    none_preview.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    assert _prepared(page).tobytes() == none_preview.tobytes()
    _run(page)
    transparent = _save(page, tmp_path / "transparent.json")
    none_fitted = original.copy()
    none_fitted.thumbnail((64, 64), Image.Resampling.LANCZOS)
    assert transparent["result"]["target_digest"] == hashlib.sha256(none_fitted.tobytes()).hexdigest()
    page.locator("#project-input").set_input_files(tmp_path / "black.json")
    expect(page.locator("#status")).to_contain_text("Restore or fork to continue")
    page.locator("#experiment-fork").click()
    expect(page.locator("#status")).to_contain_text("ready to Continue")
    restored = _save(page, tmp_path / "restored.json")
    assert restored["result"]["target_digest"] == black["result"]["target_digest"]
    assert restored["result"]["background"] == black["result"]["background"]
    assert [shape["color"] for shape in restored["result"]["shapes"]] == [
        shape["color"] for shape in black["result"]["shapes"]
    ]


@pytest.mark.parametrize("format_name", ["GIF", "APNG"])
def test_native_animation_frame_policy_static_preview_and_original_source_roundtrip(
    server_url: str, tmp_path: Path, format_name: str,
    browser: Browser,
) -> None:
    poster = Image.new("RGBA", (80, 40), (70, 90, 110, 255))
    ImageDraw.Draw(poster).rectangle((0, 0, 35, 39), fill=(180, 110, 50, 255))
    first = Image.new("RGBA", poster.size, (200, 50, 30, 128))
    ImageDraw.Draw(first).rectangle((40, 0, 79, 39), fill=(20, 170, 50, 255))
    second = Image.new("RGBA", poster.size, (20, 30, 200, 255))
    buffer = io.BytesIO()
    if format_name == "APNG":
        poster.save(buffer, format="PNG", save_all=True, default_image=True,
                    append_images=[first, second], duration=[50, 100], disposal=[0, 0], blend=[0, 0])
        expected = first
    else:
        poster.save(buffer, format="GIF", save_all=True, append_images=[first, second], duration=[50, 100, 150])
        with Image.open(io.BytesIO(buffer.getvalue())) as animated:
            animated.seek(1)
            expected = animated.convert("RGBA")
    encoded = buffer.getvalue()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server_url, wait_until="networkidle")
    page.locator("#image-input").set_input_files({
        "name": "animation.unknown", "mimeType": "application/octet-stream", "buffer": encoded,
    })
    expect(page.locator("#status")).to_have_text("Ready")
    page.locator(".source-controls summary").click()
    expect(page.locator("#source-options-meta")).to_contain_text("3 frames")
    if format_name == "APNG":
        expect(page.locator("#source-options-meta")).to_contain_text("default image is index 0")
    before = _save(page, tmp_path / "animation-original.json")
    assert before["source"]["data_url"] == (
        f"data:image/{format_name.lower()};base64," + base64.b64encode(encoded).decode()
    )
    _fitting_settings(page)
    _run(page)
    original_head = _save(page, tmp_path / "animation-original-head.json")
    page.locator("#source-frame").fill("1")
    page.locator("#source-matte").select_option("custom")
    page.locator("#source-matte-color").fill("#102030")
    page.locator("#canvas-background").select_option("custom")
    page.locator("#canvas-background-color").fill("#334455")
    page.locator("#source-apply").click()
    expect(page.locator("#status")).to_have_text("New render ready")
    assert _prepared(page).tobytes() == _matte(expected, (16, 32, 48)).tobytes()
    page.locator(".palette-controls summary").click()
    page.locator("#palette-extract-count").fill("2")
    page.locator("#palette-extract").click()
    expect(page.locator("#palette-mode")).to_have_value("exact")
    _run(page)
    saved = _save(page, tmp_path / "animation-frame.json")
    assert saved["options"]["source"] == {"frame": 1, "matte": [16, 32, 48]}
    assert saved["result"]["background"] == [51, 68, 85, 255]
    assert saved["history"]["branches"][0]["result"]["shapes"] == original_head["result"]["shapes"]
    assert saved["source"]["data_url"] == before["source"]["data_url"]
    assert saved["telemetry"]["batches"][0]["source"] == saved["options"]["source"]
    expected_colors = _matte(expected, (16, 32, 48)).getcolors()
    assert expected_colors and {tuple(rgb) for rgb in saved["options"]["palette"]["colors"]} == {
        rgb[:3] for _, rgb in expected_colors
    }
    if format_name == "APNG":
        artifacts = Path.cwd() / ".tools" / "feature-roadmap"
        artifacts.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(artifacts / "source-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        page.screenshot(path=str(artifacts / "source-mobile.png"), full_page=True)
    page.reload(wait_until="networkidle")
    page.locator("#project-input").set_input_files(tmp_path / "animation-frame.json")
    expect(page.locator("#status")).to_contain_text("Restore or fork to continue")
    expect(page.locator("#source-frame")).to_have_value("1")
    assert _prepared(page).tobytes() == _matte(expected, (16, 32, 48)).tobytes()
    page.locator("#experiment-fork").click()
    expect(page.locator("#status")).to_contain_text("ready to Continue")
    restored = _save(page, tmp_path / "animation-restored.json")
    assert restored["result"]["target_digest"] == saved["result"]["target_digest"]
    page.locator("#experiment-select").select_option("experiment-1")
    expect(page.locator("#status")).to_contain_text("Original selected")
    expect(page.locator("#source-frame")).to_have_value("0")
    expect(page.locator("#source-matte")).to_have_value("white")
    assert _prepared(page).tobytes() == poster.tobytes()
    assert errors == []
