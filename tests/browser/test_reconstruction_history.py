from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, expect


def _source() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), (130, 170, 210)).save(buffer, format="PNG")
    return buffer.getvalue()


def _save(page: Page, path: Path) -> dict:
    with page.expect_download() as download:
        page.get_by_role("button", name="Save project", exact=True).click()
    download.value.save_as(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _pixels(page: Page) -> str:
    return page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()")


def _inspect(page: Page, count: int) -> None:
    page.locator("#history-count").fill(str(count))
    page.locator("#history-count").dispatch_event("change")
    expect(page.locator("#history-position")).to_have_text(f"{count} / 3")
    page.evaluate("() => new Promise(requestAnimationFrame)")


def _protocol(page: Page) -> None:
    page.evaluate("""() => {
      const realFetch = window.fetch.bind(window), sessions = new Map();
      const bg = {r:255,g:255,b:255,a:255}, encode = event =>
        new TextEncoder().encode(JSON.stringify(event) + '\\n');
      const response = data => new Response(JSON.stringify(data), {headers:{'Content-Type':'application/json'}});
      window.runRequests = []; window.restoreRequests = []; window.restoreReplies = [];
      let active, nextId = 0;
      const snapshot = (id, event='snapshot') => {
        const state = sessions.get(id);
        return {event,session_id:id,width:80,height:40,background:bg,shapes:[...state.shapes],
          attempts:state.attempts,revision:state.shapes.length,restored_shape_count:state.retained,
          initial_score:0.9,stop_reason:'target_reached',batch_summary:state.batch};
      };
      window.finishRun = (count=null, broken=false, reason='target_reached') => {
        const {id,controller,options,startCount,startAttempts} = active, state = sessions.get(id);
        count ??= options.steps;
        for (let index=0; index<count; index++) {
          const number = state.shapes.length;
          state.shapes.push({type:'circle',color:{r:20+number*20,g:40,b:90,a:255},
            data:{x:12+number*18,y:20,r:6},score:0.8-number*0.1});
        }
        state.attempts++; state.batches++;
        state.batch = {index:state.batches,target:options.steps,shapeTypes:options.shape_types,
          candidates:options.shape_count,mutations:options.mutations,alpha:options.alpha,seed:options.seed,
          max_threads:0,effective_threads:1,start_shape_count:startCount,start_attempts:startAttempts,
          added:count,attempts:1,state:'Target reached',reason};
        const terminal = {...snapshot(id,'complete'),stop_reason:reason};
        if (broken) controller.enqueue(encode({event:'step',shapes:state.shapes.slice(startCount),
          attempts:state.attempts,revision:state.shapes.length,batch_shape_count:count,batch_goal:options.steps}));
        else controller.enqueue(encode(terminal));
        controller.close(); active = null;
      };
      window.fetch = (url, settings={}) => {
        if (url === '/api/run/stream') {
          const payload = JSON.parse(settings.body); runRequests.push(payload);
          if (window.expiredSession) return Promise.resolve(new Response(JSON.stringify({
            code:'unknown_session',error:'Unknown session'}), {status:404}));
          const id = payload.session_id || 'mock-' + ++nextId;
          if (!sessions.has(id)) sessions.set(id,{shapes:[],retained:0,attempts:0,batches:0});
          const state = sessions.get(id);
          return Promise.resolve(new Response(new ReadableStream({start(controller) {
            active = {id,controller,options:payload.options,
              startCount:state.shapes.length,startAttempts:state.attempts};
            controller.enqueue(encode({...snapshot(id,'start'),run_id:'run-'+runRequests.length,
              continued:Boolean(payload.session_id)}));
          }}), {headers:{'Content-Type':'application/x-ndjson'}}));
        }
        if (url === '/api/restore') {
          const payload = JSON.parse(settings.body); restoreRequests.push(payload);
          const id = 'mock-' + ++nextId, shapes = payload.result.shapes.slice(0,payload.shape_count);
          sessions.set(id,{shapes,retained:shapes.length,attempts:0,batches:0});
          const result = snapshot(id,'restored');
          if (window.deferRestore) return new Promise(resolve =>
            restoreReplies.push(data => resolve(response(data ?? result))));
          return Promise.resolve(response(result));
        }
        if (String(url).endsWith('/snapshot')) {
          if (window.snapshotFailure) return Promise.resolve(new Response(JSON.stringify({
            code:'snapshot_unavailable',error:'Snapshot unavailable'}), {status:503}));
          return Promise.resolve(response(snapshot(String(url).split('/')[3])));
        }
        if (String(url).endsWith('/pause')) return Promise.resolve(response({}));
        if (String(url).endsWith('/focus')) return Promise.resolve(response({
          focus:JSON.parse(settings.body).focus,applies:'next_attempt'}));
        return realFetch(url,settings);
      };
    }""")


def _prepare(page: Page, url: str, count: int = 3) -> None:
    page.goto(url, wait_until="networkidle")
    page.locator("#image-input").set_input_files({"name":"source.png", "mimeType":"image/png", "buffer":_source()})
    expect(page.locator("#status")).to_have_text("Ready")
    _protocol(page)
    page.locator("#steps-number").fill(str(count))
    page.get_by_role("button", name="Run", exact=True).click()
    page.evaluate("finishRun()")
    expect(page.locator("#run-button")).to_have_text("Continue")


def test_timeline_prefix_export_fork_and_full_project_round_trip(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page(viewport={"width":1440,"height":1000})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _prepare(page, server_url)
    head = _pixels(page)
    page.locator("#result-canvas").evaluate("""canvas => {
          canvas.toDataURL=() => {throw new Error('Save must use geometry without PNG encoding');};
        }""")
    original = _save(page, tmp_path / "original.json")
    page.locator("#result-canvas").evaluate("canvas => {delete canvas.toDataURL;}")
    _inspect(page, 1)
    assert _pixels(page) != head
    expect(page.locator("#run-button")).to_be_disabled()
    expect(page.locator("#paint-toggle")).to_be_disabled()
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 / 3 shapes · read-only")
    page.locator("#run-form").dispatch_event("submit")
    assert page.evaluate("runRequests.length") == 1
    with page.expect_download() as download:
        page.locator("#download-json").click()
    prefix_path = tmp_path / "prefix.json"
    download.value.save_as(prefix_path)
    assert len(json.loads(prefix_path.read_text(encoding="utf-8"))) == 1
    export_payloads: list[dict] = []
    page.on("request", lambda request: export_payloads.append(request.post_data_json)
            if request.url.endswith("/api/export") else None)
    with page.expect_download() as download:
        page.locator("#download-png").click()
    png_path = tmp_path / "prefix.png"
    download.value.save_as(png_path)
    assert len(export_payloads[0]["result"]["shapes"]) == 1
    with Image.open(png_path) as image:
        assert image.getpixel((round(48 * image.width / 80), image.height // 2)) == (255,255,255,255)
    saved = _save(page, tmp_path / "inspected.json")
    assert len(saved["result"]["shapes"]) == 3 and saved["history"]["view_shape_count"] == 1
    assert saved["result"]["preview_data_url"] is None
    page.locator(".experiment-management summary").click()
    page.locator("#experiment-name").fill("Detail study")
    page.locator(".advanced-controls summary").click()
    page.locator("#seed").fill("234")
    page.locator("#history-restore").click()
    expect(page.locator("#run-button")).to_be_enabled()
    assert page.evaluate("restoreRequests[0].shape_count") == 1
    assert page.evaluate("restoreRequests[0].result.shapes.length") == 3
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 retained · 0 new accepted / 0 attempts")
    expect(page.locator("#max-size-number")).to_have_value(str(original["options"]["max_size"]))
    assert page.evaluate("restoreRequests[0].options.max_size") == original["options"]["max_size"]
    assert page.evaluate("restoreRequests[0].result.width") == original["result"]["render_width"]
    page.locator("#steps-number").fill("1")
    page.locator("#run-button").click()
    page.evaluate("finishRun()")
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 retained · 1 new accepted / 1 attempts")
    child = _save(page, tmp_path / "branched.json")
    assert len(child["history"]["branches"]) == 2
    assert child["history"]["branches"][0]["result"]["shapes"] == original["result"]["shapes"]
    assert child["history"]["branches"][0]["telemetry"] == original["telemetry"]
    assert child["history"]["branches"][1]["options"]["seed"] == 234
    assert child["result"]["restored_shape_count"] == 1
    assert "sessionId" not in json.dumps(child)
    page.locator("#experiment-select").select_option("experiment-1")
    assert _pixels(page) == head
    expect(page.locator("#run-button")).to_be_enabled()
    expect(page.locator("#telemetry-acceptance")).to_have_text("3 accepted / 1 attempts")
    page.locator("#experiment-remove-choice").select_option("experiment-2")
    page.locator("#experiment-remove").click()
    expect(page.locator("#experiment-select option")).to_have_count(1)
    page.locator("#project-input").set_input_files(tmp_path / "branched.json")
    expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    expect(page.locator("#run-button")).to_be_disabled()
    expect(page.locator("#history-restore")).to_be_enabled()
    round_trip = _save(page, tmp_path / "resaved.json")
    assert round_trip["history"] == child["history"]
    assert round_trip["result"]["shapes"] == child["result"]["shapes"]
    assert round_trip["result"]["restored_shape_count"] == 1
    assert errors == []


def test_project_commit_failure_reports_error_and_allows_reopening(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _prepare(page, server_url)
    head = _pixels(page)
    path = tmp_path / "confirmed.json"
    _save(page, path)
    page.evaluate("""() => {
          window.failResultDraw=true;
          const original=CanvasRenderingContext2D.prototype.fillRect;
          CanvasRenderingContext2D.prototype.fillRect=function(...args) {
            if (failResultDraw && this.canvas.id==='result-canvas') {
              failResultDraw=false;throw new Error('Injected result redraw failure');
            }
            return original.apply(this,args);
          };
        }""")
    page.locator("#project-input").set_input_files(path)
    expect(page.locator("#status")).to_have_text("Could not open project: Injected result redraw failure")
    expect(page.locator("#load-project-button")).to_be_enabled()
    expect(page.locator("#save-project-button")).to_be_enabled()
    expect(page.locator("#pause-button")).to_be_disabled()
    page.locator("#project-input").set_input_files(path)
    expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    assert _pixels(page) == head
    assert errors == []


def test_switching_blank_and_fitted_experiments_redraws_the_head(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    head = _pixels(page)
    page.locator("#restart-button").click()
    expect(page.locator("#result-canvas")).to_be_hidden()
    expect(page.locator("#experiment-select option")).to_have_count(2)
    page.locator("#experiment-select").select_option("experiment-1")
    expect(page.locator("#result-canvas")).to_be_visible()
    assert _pixels(page) == head
    _inspect(page, 1)
    prefix = _pixels(page)
    page.locator("#experiment-select").select_option("experiment-2")
    expect(page.locator("#result-canvas")).to_be_hidden()
    page.locator("#experiment-select").select_option("experiment-1")
    assert _pixels(page) == head
    _inspect(page, 1)
    assert _pixels(page) == prefix
    path = tmp_path / "switches.json"
    saved = _save(page, path)
    assert saved["history"]["branches"][1]["result"]["background"] is None
    page.locator("#project-input").set_input_files(path)
    expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    expect(page.locator("#history-count")).to_have_value("1")
    assert _pixels(page) == prefix
    page.locator("#experiment-select").select_option("experiment-2")
    expect(page.locator("#result-canvas")).to_be_hidden()
    page.locator("#experiment-select").select_option("experiment-1")
    assert _pixels(page) == head
    _inspect(page, 1)
    assert _pixels(page) == prefix


@pytest.mark.parametrize("replacement", ["source", "project", "branch"])
@pytest.mark.parametrize("late_error", [False, True])
def test_restore_replies_cannot_replace_changed_content(
    server_url: str, tmp_path: Path, replacement: str, late_error: bool,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    path = tmp_path / "original.json"
    _save(page, path)
    if replacement == "branch":
        page.locator("#restart-button").click()
        page.locator("#experiment-select").select_option("experiment-1")
    _inspect(page, 1)
    page.evaluate("deferRestore = true")
    page.locator("#history-restore").click()
    page.wait_for_function("restoreReplies.length === 1")
    expect(page.locator("#history-slider")).to_be_disabled()
    expect(page.locator("#run-button")).to_be_disabled()
    if replacement == "source":
        page.locator("#sample-button").click()
        expect(page.locator("#status")).to_have_text("Ready")
    elif replacement == "project":
        page.locator("#project-input").set_input_files(path)
        expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    else:
        page.locator("#experiment-select").select_option("experiment-2")
        expect(page.locator("#status")).to_have_text("Render selected")
    expected_status = page.locator("#status").inner_text()
    page.evaluate(
        "restoreReplies[0]({error:'late failure',code:'invalid_result'})" if late_error else "restoreReplies[0]()"
    )
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
    expect(page.locator("#status")).to_have_text(expected_status)
    assert page.locator("#experiment-select option").count() == (2 if replacement == "branch" else 1)


def test_prefix_export_replies_are_invalidated_by_scrubbing(server_url: str, browser: Browser) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    page.evaluate("""() => {
          const realFetch=window.fetch;
          window.fetch=(url,settings) => url==='/api/export' ?
            new Promise(resolve => {window.exportReply=resolve;}) : realFetch(url,settings);
        }""")
    _inspect(page, 1)
    page.locator("#download-svg").click()
    page.wait_for_function("typeof exportReply === 'function'")
    _inspect(page, 2)
    page.evaluate("exportReply(new Response('{}',{headers:{'Content-Type':'application/json'}}))")
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
    expect(page.locator("#status")).to_have_text("Inspecting 2 of 3 shapes")


@pytest.mark.parametrize("invalid_import", ["image", "project"])
def test_failed_import_supersedes_restore_without_leaving_controls_locked(
    server_url: str, invalid_import: str,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    head = _pixels(page)
    page.evaluate("deferRestore = true")
    page.locator("#experiment-fork").click()
    page.wait_for_function("restoreReplies.length === 1")
    if invalid_import == "image":
        page.locator("#image-input").set_input_files({
            "name":"wrong.svg", "mimeType":"image/svg+xml", "buffer":b"<svg/>",
        })
        expect(page.locator("#status")).to_contain_text("Could not load image")
    else:
        page.locator("#project-input").set_input_files({
            "name":"broken.json", "mimeType":"application/json", "buffer":b"{",
        })
        expect(page.locator("#status")).to_contain_text("Could not open project")
    status = page.locator("#status").inner_text()
    page.evaluate("restoreReplies[0]()")
    expect(page.locator("#save-project-button")).to_be_enabled()
    expect(page.locator("#run-button")).to_be_enabled()
    expect(page.locator("#experiment-fork")).to_be_enabled()
    expect(page.locator("#status")).to_have_text(status)
    expect(page.locator("#experiment-select option")).to_have_count(1)
    assert _pixels(page) == head


def test_old_recovery_error_cannot_expire_or_change_a_new_render(server_url: str, browser: Browser) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    page.evaluate("snapshotFailure = true")
    page.locator("#run-button").click()
    page.evaluate("finishRun(1,true)")
    expect(page.locator("#run-button")).to_have_text("Recover result")
    page.evaluate("""() => {
          const realFetch=window.fetch;
          window.fetch=(url,settings) => String(url).endsWith('/snapshot') ?
            new Promise(resolve => {window.recoveryReply=resolve;}) : realFetch(url,settings);
        }""")
    page.locator("#run-button").click()
    page.wait_for_function("typeof recoveryReply === 'function'")
    page.locator("#restart-button").click()
    expect(page.locator("#status")).to_have_text("New render ready")
    page.evaluate("""() => recoveryReply(new Response(JSON.stringify({
          error:'expired',code:'unknown_session'}),{status:404}))""")
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
    expect(page.locator("#status")).to_have_text("New render ready")
    expect(page.locator("#run-button")).to_have_text("Run")
    expect(page.locator("#run-button")).to_be_enabled()


def test_retained_rollback_and_recovery_remain_available_at_point_capacity(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    contract = page.request.get(f"{server_url}/api/config").json()
    contract["project"]["max_history_points"] = 8
    page.route("**/api/config", lambda route: route.fulfill(json=contract))
    _prepare(page, server_url, count=1)
    project = _save(page, tmp_path / "one.json")
    project["result"]["shapes"] = [{
        "type":"polyline", "color":{"r":20,"g":40,"b":90,"a":255},
        "data":{"points":[[10,10],[20,10],[30,20],[40,20]]}, "score":0.8,
    }]
    path = tmp_path / "polyline.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    page.locator("#project-input").set_input_files(path)
    expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    page.locator("#history-restore").click()
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 retained · 0 new accepted / 0 attempts")
    confirmed = _pixels(page)
    page.locator("input[name='shape']").evaluate_all("""inputs =>
          inputs.forEach(input => {input.checked=input.value==='circle';})""")
    page.locator("input[name='shape'][value='circle']").dispatch_event("change")
    page.locator("#steps-number").fill("1")
    page.locator("#run-button").click()
    page.locator("input[name='shape']").evaluate_all("""inputs =>
          inputs.forEach(input => {input.checked=input.value==='polyline';})""")
    page.locator("input[name='shape'][value='polyline']").dispatch_event("change")
    page.evaluate("snapshotFailure = true; finishRun(1,true)")
    expect(page.locator("#run-button")).to_have_text("Recover result")
    expect(page.locator("#run-button")).to_be_enabled()
    expect(page.locator("#history-slider")).to_be_disabled()
    expect(page.locator("#experiment-select")).to_be_disabled()
    expect(page.locator("#history-restore")).to_be_disabled()
    assert _pixels(page) == confirmed
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 retained · 0 new accepted / 0 attempts")
    saved = _save(page, tmp_path / "rollback.json")
    assert saved["result"]["restored_shape_count"] == 1
    assert len(saved["result"]["shapes"]) == 1
    page.evaluate("snapshotFailure = false")
    page.locator("#run-button").click()
    expect(page.locator("#status")).to_have_text("Stream interrupted; recovered final result")
    expect(page.locator("#telemetry-acceptance")).to_have_text("1 retained · 1 new accepted / 1 attempts")
    expect(page.locator("#run-button")).to_be_disabled()
    expect(page.locator("#history-slider")).to_be_enabled()
    assert page.evaluate("runRequests.length") == 2


def test_expired_session_requires_an_explicit_child_and_paint_locks_timeline(server_url: str, browser: Browser) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    head = _pixels(page)
    page.evaluate("expiredSession = true")
    page.locator("#run-button").click()
    expect(page.locator("#status")).to_have_text(
        "Session expired. Restore or fork the retained experiment to continue."
    )
    expect(page.locator("#run-button")).to_be_disabled()
    expect(page.locator("#history-restore")).to_be_enabled()
    assert _pixels(page) == head
    page.evaluate("expiredSession = false")
    page.locator("#history-restore").click()
    expect(page.locator("#run-button")).to_be_enabled()
    page.locator("#paint-toggle").click()
    page.locator("#paint-behavior").select_option("hold")
    box = page.locator("#result-canvas").bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    expect(page.locator("#history-slider")).to_be_disabled()
    expect(page.locator("#experiment-select")).to_be_disabled()
    expect(page.locator("#experiment-fork")).to_be_disabled()
    page.mouse.up()
    page.evaluate("finishRun(0,false,'shape_limit')")
    expect(page.locator("#paint-status")).to_have_text("Paint stopped: Shape limit reached")
    expect(page.locator("#history-slider")).to_be_enabled()
    expect(page.locator("#experiment-select")).to_be_enabled()


@pytest.mark.parametrize("prefix", [0, 2])
def test_native_prefix_restore_continue_and_parent_preservation(
    server_url: str, tmp_path: Path, prefix: int,
    browser: Browser,
) -> None:
    page = browser.new_page()
    page.goto(server_url, wait_until="networkidle")
    expect(page.locator("#native-state")).to_have_text("Core ready")
    page.locator("#sample-button").click()
    page.locator("#max-size-number").fill("64")
    page.locator("#steps-number").fill("3")
    page.locator(".advanced-controls summary").click()
    page.locator("#shape-count").fill("16")
    page.locator("#mutations").fill("24")
    page.locator("#max-threads").fill("1")
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    parent = _save(page, tmp_path / "native-parent.json")
    _inspect(page, prefix)
    prefix_pixels = _pixels(page)
    page.locator("#history-restore").click()
    expect(page.locator("#run-button")).to_be_enabled(timeout=30_000)
    assert _pixels(page) == prefix_pixels
    restored = _save(page, tmp_path / "native-restored.json")
    assert restored["result"]["restored_shape_count"] == prefix
    assert restored["telemetry"]["attempts"] == 0
    assert restored["telemetry"]["batches"] == []
    page.locator("#steps-number").fill("1")
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    continued = _save(page, tmp_path / "native-continued.json")
    assert len(continued["result"]["shapes"]) == prefix + 1
    assert continued["telemetry"]["batches"][0]["start_shape_count"] == prefix
    assert continued["history"]["branches"][0]["result"]["shapes"] == parent["result"]["shapes"]
    page.locator("#experiment-select").select_option("experiment-1")
    expect(page.locator("#run-button")).to_be_enabled()
    assert _save(page, tmp_path / "native-original-again.json")["result"]["shapes"] == parent["result"]["shapes"]
