from __future__ import annotations

import base64
import io
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, expect, sync_playwright

from geometrize_py.web import GeometrizeRequestHandler, GeometrizeServer

STATIC = Path(__file__).resolve().parents[2] / "python/geometrize_py/static"


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


def _module(page: Page, filename: str) -> None:
    page.evaluate("""async source => {
      const url = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
      try { Object.assign(window, await import(url)); }
      finally { URL.revokeObjectURL(url); }
    }""", (STATIC / filename).read_text(encoding="utf-8"))


def _image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 100), (80, 130, 170)).save(buffer, format="PNG")
    return buffer.getvalue()


def _click_fraction(page: Page, x: float, y: float) -> None:
    box = page.locator("#result-canvas").bounding_box()
    assert box
    page.mouse.click(box["x"] + x * box["width"], box["y"] + y * box["height"])


def _save_project(page: Page, path: Path) -> dict:
    with page.expect_download() as download:
        page.get_by_role("button", name="Save project", exact=True).click()
    download.value.save_as(path)
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("size", [(400, 120), (120, 400)])
def test_focus_mapping_gestures_and_overlay_keep_preview_pixels(size: tuple[int, int]) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.set_content("""<style>
          .stage {position:relative;width:45vw;height:40vh;overflow:hidden;touch-action:none}
          .media {position:absolute;left:50%;top:50%;transform-origin:center;pointer-events:none}
          [hidden] {display:none}
        </style><div id="source" class="stage"><img id="source-img" class="media"></div>
        <div id="result" class="stage" tabindex="0"><img id="result-img" class="media" hidden>
        <canvas id="canvas" class="media"></canvas><svg id="overlay" class="media" hidden>
        <circle class="focus-ring"></circle><circle class="focus-center"></circle></svg></div><output id="zoom"></output>""")
        _module(page, "preview.js")
        page.evaluate("""([width,height]) => {
          window.moves = [];
          window.preview = new Preview({sourceStage: document.querySelector('#source'),
            resultStage: document.querySelector('#result'), sourceImage: document.querySelector('#source-img'),
            resultImage: document.querySelector('#result-img'), resultCanvas: document.querySelector('#canvas'),
            zoomOutput: document.querySelector('#zoom'), focusOverlay: document.querySelector('#overlay'),
            onFocusMove: point => { moves.push(point); preview.setFocus({...preview.focus, ...point}); }});
          preview.sourceSize = {width,height};
          preview.beginResult(width, height, [70, 120, 170, 255]);
          window.originalPixels = preview.currentPreview();
          preview.setFocus({x:0.5, y:0.5, radius:0.2, strength:0.75});
        }""", list(size))

        def verify_point() -> None:
            box = page.locator("#canvas").bounding_box()
            assert box
            point = page.evaluate("""() => {
              const box = document.querySelector('#canvas').getBoundingClientRect();
              return preview.resultPoint(box.left + box.width * 0.8, box.top + box.height * 0.25);
            }""")
            assert point["x"] == pytest.approx(0.8)
            assert point["y"] == pytest.approx(0.25)
            page.mouse.click(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.25)
            move = page.evaluate("moves.at(-1)")
            assert move["x"] == pytest.approx(0.8)
            assert move["y"] == pytest.approx(0.25)
            assert page.evaluate("preview.currentPreview() === originalPixels")
            assert page.locator("#overlay").bounding_box() == pytest.approx(box)
            assert float(page.locator(".focus-ring").get_attribute("r") or "0") == 24

        verify_point()
        assert page.evaluate("preview.pan") == {"x": 0, "y": 0}
        media = page.locator("#canvas").bounding_box()
        assert media
        page.mouse.move(media["x"] + media["width"] * 0.2, media["y"] + media["height"] * 0.3)
        page.mouse.down()
        page.mouse.move(media["x"] + media["width"] * 0.4, media["y"] + media["height"] * 0.6)
        page.mouse.up()
        assert page.evaluate("preview.focus.x") == pytest.approx(0.4)
        assert page.evaluate("preview.focus.y") == pytest.approx(0.6)
        assert page.evaluate("preview.pan") == {"x": 0, "y": 0}
        result = page.locator("#result").bounding_box()
        assert result
        # Letterboxing is outside the actual media, including while focused.
        before = page.evaluate("moves.length")
        page.mouse.click(result["x"] + 10, result["y"] + 5)
        assert page.evaluate("moves.length") == before
        assert page.evaluate("preview.resultPoint(-100, -100)") is None

        page.keyboard.press("ArrowRight")
        assert page.evaluate("preview.focus.x") == pytest.approx(0.41)
        page.keyboard.press("Home")
        assert page.evaluate("preview.focus.x") == 0.5
        page.keyboard.down("Shift")
        page.mouse.move(result["x"] + 180, result["y"] + 160)
        page.mouse.down()
        page.mouse.move(result["x"] + 193, result["y"] + 168)
        page.mouse.up()
        page.keyboard.up("Shift")
        assert page.evaluate("preview.pan.x") > 0
        pan_before = page.evaluate("preview.pan.x")
        source = page.locator("#source").bounding_box()
        assert source
        page.mouse.move(source["x"] + 180, source["y"] + 160)
        page.mouse.down()
        page.mouse.move(source["x"] + 190, source["y"] + 160)
        page.mouse.up()
        assert page.evaluate("preview.pan.x") > pan_before
        assert page.locator("#source-img").evaluate("e => e.style.transform") == page.locator("#canvas").evaluate("e => e.style.transform")
        page.mouse.move(result["x"] + 180, result["y"] + 160)
        page.mouse.wheel(0, -100)
        expect(page.locator("#zoom")).to_have_text("115%")
        verify_point()
        page.set_viewport_size({"width": 1000, "height": 700})
        page.evaluate("preview.layout()")
        verify_point()
        page.evaluate("preview.setFocus(null)")
        page.mouse.move(result["x"] + 100, result["y"] + 100)
        page.mouse.down()
        page.mouse.move(result["x"] + 110, result["y"] + 100)
        page.mouse.up()
        assert page.evaluate("preview.pan.x") > pan_before
        expect(page.locator("#overlay")).to_be_hidden()
        browser.close()


def test_focus_updates_coalesce_serially_and_ignore_old_run_replies() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _module(page, "focus.js")
        result = page.evaluate("""async () => {
          const calls = [], states = [], deferred = [];
          const tick = () => new Promise(resolve => setTimeout(resolve, 0));
          const focus = x => ({x, y:0.5, radius:0.2, strength:0.75});
          const updates = new FocusUpdates({onState: (state, message) => states.push([state, message]),
            post: (path, payload) => {calls.push({path, payload}); return new Promise((resolve, reject) => deferred.push({resolve,reject}));}});
          updates.begin({sessionId:'old/session',runId:'old-run',contentVersion:1,runVersion:1}, null);
          updates.update(focus(0.1)); updates.update(focus(0.2)); updates.update(focus(0.3));
          const inFlightCount = calls.length;
          deferred[0].resolve({focus:focus(0.1), applies:'next_attempt'}); await tick();
          const afterFirst = calls.length;
          updates.end(); updates.update(focus(0.9));
          updates.begin({sessionId:'new',runId:'new-run',contentVersion:2,runVersion:2}, null);
          const beforeOldReply = calls.length;
          deferred[1].reject(new Error('late old-run failure')); await tick();
          deferred[2].resolve({focus:focus(0.9), applies:'next_attempt'}); await tick();
          updates.update(null); deferred[3].reject(new Error('current failure')); await tick();
          return {calls, states, inFlightCount, afterFirst, beforeOldReply, desired:updates.desired};
        }""")
        assert result["inFlightCount"] == 1
        assert result["afterFirst"] == result["beforeOldReply"] == 2
        assert [call["payload"]["focus"]["x"] for call in result["calls"][:3]] == [0.1, 0.3, 0.9]
        assert result["calls"][0]["path"] == "/api/sessions/old%2Fsession/focus"
        assert result["calls"][2]["payload"]["run_id"] == "new-run"
        assert result["calls"][3]["payload"]["focus"] is None
        assert [item[1] for item in result["states"] if item[0] == "error"] == ["current failure"]
        assert result["desired"] is None
        assert errors == []
        browser.close()


def test_paint_queue_bounds_positions_cancellation_and_failure() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _module(page, "focus.js")
        result = page.evaluate("""async () => {
          const calls = [], states = [], deferred = [];
          const tick = () => new Promise(resolve => setTimeout(resolve, 0));
          const focus = x => ({x, y:0.5, radius:0.2, strength:0.75});
          const queue = new PaintQueue({capacity:2, onState: (state, detail) => states.push([state, detail]),
            runStroke: value => {calls.push(value); return new Promise((resolve,reject) => deferred.push({resolve,reject}));}});
          queue.setEnabled(true); const first = focus(0.1); queue.enqueue(first); first.x = 1;
          queue.enqueue(focus(0.2)); queue.enqueue(focus(0.3)); const overflow = queue.enqueue(focus(0.4));
          const initialCount = calls.length;
          deferred[0].resolve({added:1}); await tick();
          queue.setEnabled(false); queue.setEnabled(true); queue.enqueue(focus(0.8));
          deferred[1].reject(new Error('cancelled old stroke')); await tick();
          queue.enqueue(focus(0.9)); deferred[2].reject(new Error('stroke failed')); await tick();
          const stoppedCount = calls.length;
          queue.enqueue(focus(0.95)); queue.enqueue(focus(0.99));
          deferred[3].resolve({added:0,reason:'Fit already adequate'}); await tick();
          deferred[4].resolve({added:1}); await tick();
          return {calls, states, overflow, initialCount, stoppedCount, pending:queue.pending.length};
        }""")
        assert result["overflow"] is False
        assert result["initialCount"] == 1
        assert result["stoppedCount"] == 3
        assert [call["x"] for call in result["calls"]] == [0.1, 0.2, 0.8, 0.95, 0.99]
        assert result["pending"] == 0
        assert [detail["detail"] for state, detail in result["states"] if state == "error"] == ["stroke failed"]
        assert any(state == "rejected" and detail["detail"] == "Fit already adequate" for state, detail in result["states"])
        browser.close()


def _mock_runs(page: Page) -> None:
    page.evaluate("""() => {
      const originalFetch = window.fetch;
      window.runRequests = []; window.focusRequests = []; window.focusReplies = [];
      let shapes = [], active, attempts = 0;
      const background = {r:70,g:120,b:170,a:255};
      const encode = value => new TextEncoder().encode(JSON.stringify(value) + '\\n');
      window.finishStroke = (reason = 'target_reached', broken = false) => {
        const {controller, options, runId} = active;
        if (reason === 'target_reached') shapes.push({type:'circle',color:{r:20,g:20,b:20,a:255},
          data:{x:options.focus.x*320,y:options.focus.y*100,r:4},score:0.7-shapes.length*0.1});
        attempts++;
        const event = {event:'complete',session_id:'paint-session',run_id:runId,width:320,height:100,
          background,shapes:[...shapes],attempts,revision:shapes.length,initial_score:0.9,stop_reason:reason,
          batch_summary:{index:runRequests.length,target:options.steps,shapeTypes:options.shape_types,
            candidates:options.shape_count,mutations:options.mutations,alpha:options.alpha,seed:options.seed,
            max_threads:0,effective_threads:1,start_shape_count:Math.max(0,shapes.length-1),start_attempts:attempts-1,
            added:reason==='target_reached'?1:0,attempts:1,state:'Target reached',reason,
            focus:options.focus,initial_focus:options.focus}};
        window.lastSnapshot = {...event, event:'snapshot'};
        if (!broken) controller.enqueue(encode(event)); controller.close(); active = null;
      };
      window.fetch = (url, settings = {}) => {
        if (url === '/api/run/stream') {
          const payload = JSON.parse(settings.body); runRequests.push(payload);
          if (!payload.session_id) { shapes = []; attempts = 0; }
          const runId = 'stroke-' + runRequests.length;
          return Promise.resolve(new Response(new ReadableStream({start(controller) {
            active = {controller,options:payload.options,runId};
            controller.enqueue(encode({event:'start',session_id:'paint-session',run_id:runId,
              continued:Boolean(payload.session_id),width:320,height:100,background,shapes:[...shapes],
              attempts,revision:shapes.length,initial_score:0.9,focus:payload.options.focus}));
          }}), {headers:{'Content-Type':'application/x-ndjson'}}));
        }
        if (String(url).endsWith('/focus')) {
          focusRequests.push(JSON.parse(settings.body));
          return new Promise(resolve => focusReplies.push(resolve));
        }
        return originalFetch(url, settings);
      };
    }""")


def test_paint_clicks_send_one_step_each_and_focus_edits_do_not_retarget_strokes(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(server_url, wait_until="networkidle")
        _mock_runs(page)
        page.locator("#image-input").set_input_files({"name":"wide.png","mimeType":"image/png","buffer":_image()})
        expect(page.locator("#status")).to_have_text("Ready")
        page.get_by_role("button", name="Paint", exact=True).click()
        expect(page.locator("#paint-toggle")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#result-canvas")).to_be_visible()
        blank = _save_project(page, tmp_path / "blank.json")
        assert blank["result"]["preview_data_url"] is None
        assert blank["result"]["width"] is None
        assert blank["result"]["shapes"] == []
        expect(page.locator("#download-png")).to_have_attribute("aria-disabled", "true")

        _click_fraction(page, 0.2, 0.25)
        expect(page.locator("#pause-button")).to_be_enabled()
        expect(page.locator("#run-button")).to_be_disabled()
        page.locator("#run-form").dispatch_event("submit")
        _click_fraction(page, 0.7, 0.75)
        page.locator("#focus-strength-number").fill("90")
        page.locator("#focus-strength-number").dispatch_event("change")
        assert page.evaluate("runRequests.length") == 1
        assert page.evaluate("focusRequests.length") == 0
        page.evaluate("finishStroke()")
        page.wait_for_function("runRequests.length === 2")
        requests = page.evaluate("runRequests")
        assert [request["options"]["steps"] for request in requests] == [1, 1]
        assert [request["options"]["focus"]["x"] for request in requests] == pytest.approx([0.2, 0.7])
        assert [request["options"]["focus"]["y"] for request in requests] == pytest.approx([0.25, 0.75])
        assert [request["options"]["focus"]["strength"] for request in requests] == [0.75, 0.75]
        assert requests[1]["session_id"] == "paint-session"
        page.evaluate("finishStroke()")
        expect(page.locator("#telemetry-acceptance")).to_have_text("2 accepted / 2 attempts")
        expect(page.locator("#run-button")).to_have_text("Continue")
        assert page.evaluate("focusRequests.length") == 0
        pixels = page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()")
        saved = _save_project(page, tmp_path / "painted.json")
        assert saved["options"]["focus"]["strength"] == 0.9
        assert saved["result"]["preview_data_url"] == pixels
        assert len(saved["result"]["shapes"]) == 2
        assert saved["telemetry"]["batches"][0]["focus"]["x"] == pytest.approx(0.2)

        # An active stroke finishes after Paint is disabled; queued clicks stop.
        _click_fraction(page, 0.4, 0.5)
        _click_fraction(page, 0.6, 0.5)
        page.get_by_role("button", name="Paint", exact=True).click()
        page.evaluate("finishStroke()")
        expect(page.locator("#telemetry-acceptance")).to_have_text("3 accepted / 3 attempts")
        assert page.evaluate("runRequests.length") == 3
        expect(page.locator("#focus-overlay")).to_be_hidden()
        page.locator("#project-input").set_input_files(tmp_path / "painted.json")
        expect(page.locator("#focus-toggle")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#paint-toggle")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#focus-strength-number")).to_have_value("90")
        page.get_by_role("button", name="Sample", exact=True).click()
        expect(page.locator("#focus-toggle")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#focus-radius-number")).to_have_value("20")
        expect(page.locator("#focus-overlay")).to_be_hidden()
        assert errors == []
        browser.close()


def test_paint_pause_discards_queue_without_automatic_resume(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(server_url, wait_until="networkidle")
        _mock_runs(page)
        page.route("**/api/sessions/paint-session/pause", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"run_id":"stroke-1"}),
        ))
        page.get_by_role("button", name="Sample", exact=True).click()
        page.get_by_role("button", name="Paint", exact=True).click()
        _click_fraction(page, 0.2, 0.3)
        _click_fraction(page, 0.6, 0.7)
        page.get_by_role("button", name="Pause", exact=True).click()
        expect(page.locator("#telemetry-state")).to_have_text("Pausing")
        page.evaluate("finishStroke('paused')")
        expect(page.locator("#telemetry-state")).to_have_text("Paused")
        assert page.evaluate("runRequests.length") == 1
        _click_fraction(page, 0.8, 0.6)
        page.wait_for_function("runRequests.length === 2")
        page.evaluate("finishStroke()")
        expect(page.locator("#telemetry-acceptance")).to_have_text("1 accepted / 2 attempts")
        browser.close()


def test_live_focus_requests_and_late_errors_leave_new_source_untouched(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(server_url, wait_until="networkidle")
        _mock_runs(page)
        page.get_by_role("button", name="Sample", exact=True).click()
        page.get_by_role("button", name="Focus area", exact=True).click()
        page.get_by_role("button", name="Run", exact=True).click()
        expect(page.locator("#pause-button")).to_be_enabled()
        request = page.evaluate("runRequests[0]")
        assert request["options"]["focus"] == {"x":0.5,"y":0.5,"radius":0.2,"strength":0.75}
        _click_fraction(page, 0.2, 0.3)
        # Leaving Paint during a normal live batch reattaches focus updates.
        page.get_by_role("button", name="Paint", exact=True).click()
        page.get_by_role("button", name="Focus area", exact=True).click()
        _click_fraction(page, 0.4, 0.6)
        _click_fraction(page, 0.8, 0.7)
        assert page.evaluate("focusRequests.length") == 1
        page.evaluate("focusReplies[0](new Response(JSON.stringify({focus:focusRequests[0].focus,applies:'next_attempt'}), {headers:{'Content-Type':'application/json'}}))")
        page.wait_for_function("focusRequests.length === 2")
        final = page.evaluate("focusRequests[1]")
        assert final["run_id"] == "stroke-1"
        assert final["focus"]["x"] == pytest.approx(0.8)
        assert final["focus"]["y"] == pytest.approx(0.7)
        page.evaluate("finishStroke()")
        expect(page.locator("#run-button")).to_have_text("Continue")
        page.get_by_role("button", name="Sample", exact=True).click()
        page.evaluate("focusReplies[1](new Response(JSON.stringify({error:'Old run expired',code:'stale_run'}), {status:409,headers:{'Content-Type':'application/json'}}))")
        page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
        expect(page.locator("#status")).to_have_text("Ready")
        expect(page.locator("#focus-status")).to_have_text("Off · drag previews to pan")
        expect(page.locator("#focus-overlay")).to_be_hidden()
        browser.close()


def test_project_focus_legacy_validation_and_overlay_free_exports(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 1000})
        page.goto(server_url, wait_until="networkidle")
        contract = page.request.get(f"{server_url}/api/config").json()
        options = {key:value for key,value in contract["defaults"].items() if key != "focus"}
        project = {
            "format":contract["project"]["format"],"version":contract["project"]["version"],
            "source":{"name":"wide.png","data_url":"data:image/png;base64," + base64.b64encode(_image()).decode()},
            "options":options,
            "result":{"width":320,"height":100,"render_width":320,"render_height":100,
                "background":[80,130,170,255],"shapes":[],"preview_data_url":None},
            "telemetry":{"attempts":0,"duration_ms":0,"initial_score":0.9,"batches":[]},
        }
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(project), encoding="utf-8")
        page.locator("#project-input").set_input_files(path)
        expect(page.locator("#status")).to_have_text("Project loaded — Run starts a new render")
        expect(page.locator("#focus-toggle")).to_have_attribute("aria-pressed", "false")
        assert _save_project(page, tmp_path / "legacy-resaved.json")["options"]["focus"] is None
        baseline = page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()")
        page.get_by_role("button", name="Focus area", exact=True).click()
        _click_fraction(page, 0.75, 0.5)
        page.locator("#focus-radius-number").fill("35")
        page.locator("#focus-radius-number").dispatch_event("change")
        assert page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()") == baseline
        saved = _save_project(page, tmp_path / "focused.json")
        assert saved["result"]["preview_data_url"] == baseline
        assert saved["options"]["focus"]["radius"] == 0.35
        page.locator("#project-input").set_input_files(tmp_path / "focused.json")
        expect(page.locator("#focus-radius-number")).to_have_value("35")
        expect(page.locator("#focus-overlay")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        exports: list[dict] = []

        def export(route) -> None:
            exports.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "preview":baseline,"svg":"<svg xmlns='http://www.w3.org/2000/svg'/>","export_width":320,"export_height":100,
            }))

        page.route("**/api/export", export)
        with page.expect_download():
            page.locator("#download-png").click()
        assert "focus" not in exports[0]["result"]
        assert exports[0]["result"]["shapes"] == []
        for field, invalid in [("x",True),("y",1.01),("radius",0),("strength",-0.1)]:
            raw = json.loads(json.dumps(saved))
            raw["options"]["focus"][field] = invalid
            error = page.evaluate("""async ({project, contract}) => {
              const {validateProject} = await import('/static/project.js');
              try {validateProject(project, contract); return null;} catch(error) {return error.message;}
            }""", {"project":raw,"contract":contract})
            assert f"Project options focus {field} must be a number" in error
        validated = page.evaluate("""async ({project, contract}) => {
          const {validateProject} = await import('/static/project.js');
          project.options.focus = {x:0.2,y:0.3};
          const defaults = validateProject(project, contract).options.focus;
          project.options.focus.unexpected = 1;
          let error; try {validateProject(project, contract);} catch(value) {error=value.message;}
          return {defaults,error};
        }""", {"project":saved,"contract":contract})
        assert validated["defaults"] == {"x":0.2,"y":0.3,"radius":0.2,"strength":0.75}
        assert validated["error"] == "Project options focus contains an unknown field"
        page.get_by_role("button", name="Clear focus", exact=True).click()
        expect(page.locator("#focus-overlay")).to_be_hidden()
        expect(page.locator("#focus-radius-number")).to_have_value("20")
        browser.close()


@pytest.mark.parametrize("reason,broken", [("paused",False),("error",False),("target_reached",True)])
def test_paint_interruption_stops_pending_clicks_and_preserves_snapshot(server_url: str, reason: str, broken: bool) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(server_url, wait_until="networkidle")
        _mock_runs(page)
        page.route("**/api/sessions/paint-session/snapshot", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(page.evaluate("lastSnapshot")),
        ))
        page.get_by_role("button", name="Sample", exact=True).click()
        page.get_by_role("button", name="Paint", exact=True).click()
        _click_fraction(page, 0.2, 0.3)
        _click_fraction(page, 0.6, 0.7)
        page.evaluate("([reason,broken]) => finishStroke(reason,broken)", [reason,broken])
        expect(page.locator("#paint-status")).to_contain_text("Paint stopped:")
        expect(page.locator("#run-button")).to_have_text("Continue")
        assert page.evaluate("runRequests.length") == 1
        expected = "1 accepted / 1 attempts" if broken else "0 accepted / 1 attempts"
        expect(page.locator("#telemetry-acceptance")).to_have_text(expected)
        expect(page.locator("#download-png")).to_have_attribute("aria-disabled", "false")
        browser.close()
