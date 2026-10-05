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


def _image(animated: bool = False) -> bytes:
    buffer = io.BytesIO()
    first = Image.new("RGBA", (80, 40), (100, 130, 170, 128))
    if animated:
        second = Image.new("RGBA", first.size, (200, 30, 70, 255))
        first.save(buffer, format="GIF", save_all=True, append_images=[second], duration=[50, 100])
    else:
        first.save(buffer, format="PNG")
    return buffer.getvalue()


def _save(page: Page, path: Path) -> dict:
    with page.expect_download() as download:
        page.locator("#save-project-button").click()
    download.value.save_as(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _mock(page: Page) -> None:
    page.add_init_script(r"""{
      const fetch=window.fetch.bind(window),sessions=new Map();
      const json=(data,status=200)=>new Response(JSON.stringify(data),
        {status,headers:{'Content-Type':'application/json'}});
      const encode=event=>new TextEncoder().encode(JSON.stringify(event)+'\n');
      let nextId=0,active;
      window.prepareRequests=[];window.prepareReplies=[];window.runRequests=[];
      window.restoreRequests=[];window.restoreReplies=[];window.paletteRequests=[];window.paletteReplies=[];
      window.targetWidth=1600;window.targetHeight=800;window.targetFrames=2;window.deferPrepare=false;
      const digest=options=>(options.source?.frame ? 'b' : options.source?.matte ? 'a' : 'c').repeat(64);
      const png=source=>{
        const canvas=document.createElement('canvas');canvas.width=80;
        canvas.height=Math.max(1,Math.round(80*targetHeight/targetWidth));
        canvas.getContext('2d').fillStyle=source?.frame ? '#C81E46' : source?.matte ? '#D3DBE3' : '#6482AA';
        canvas.getContext('2d').fillRect(0,0,canvas.width,canvas.height);return canvas.toDataURL();};
      const snapshot=(id,event='snapshot')=>{
        const state=sessions.get(id);
        return {event,session_id:id,width:80,height:40,background:state.background,shapes:[...state.shapes],
          attempts:state.attempts,revision:state.shapes.length,restored_shape_count:state.retained,
          initial_score:0.9,target_digest:state.digest};};
      window.finishSourceRun=()=>{
        const {controller,id,options,startCount}=active,state=sessions.get(id);
        for(let n=0;n<options.steps;n++) state.shapes.push({type:'circle',
          color:{r:40,g:70,b:90,a:options.alpha},data:{x:10+state.shapes.length*10,y:20,r:4},
          score:0.8-state.shapes.length*0.05});
        state.attempts++;state.batches++;
        controller.enqueue(encode({...snapshot(id,'complete'),stop_reason:'target_reached',batch_summary:{
          index:state.batches,target:options.steps,shapeTypes:options.shape_types,candidates:options.shape_count,
          mutations:options.mutations,alpha:options.alpha,seed:options.seed,max_threads:0,effective_threads:1,
          start_shape_count:startCount,start_attempts:state.attempts-1,added:options.steps,attempts:1,
          state:'Target reached',reason:'target_reached',source:options.source,background:options.background}}));
        controller.close();active=null;};
      window.fetch=(url,settings={})=>{
        if(url==='/api/config') return fetch(url,settings).then(async response=>{
          const config=await response.json();config.project.version=3;
          if(window.historyLimit) config.project.max_branches=window.historyLimit;
          return json(config);});
        if(url==='/api/source/prepare') {
          const payload=JSON.parse(settings.body);prepareRequests.push(payload);
          const respond=(error=false)=>json(error ? {code:'invalid_image',error:'Preparation rejected'} : {
            source:payload.source,width:targetWidth,height:targetHeight,
            mime_type:payload.image.split(',')[1].startsWith('R0lGOD') ? 'image/gif' :
              payload.image.startsWith('data:image/apng;') ? 'image/apng' : 'image/png',
            frame_count:targetFrames,frames_truncated:Boolean(window.truncatedFrames),
            default_image:Boolean(window.defaultImage),frame_duration_ms:50,
            preview_data_url:png(payload.source),preview_width:80,
            preview_height:Math.max(1,Math.round(80*targetHeight/targetWidth))},error?400:200);
          if(deferPrepare) return new Promise(resolve=>prepareReplies.push(error=>resolve(respond(error))));
          return Promise.resolve(respond());
        }
        if(url==='/api/run/stream') {
          const payload=JSON.parse(settings.body);runRequests.push(payload);
          const id=payload.session_id || 'source-'+ ++nextId;
          if(!sessions.has(id)) sessions.set(id,{shapes:[],attempts:0,batches:0,retained:0,
            digest:digest(payload.options),background:Object.fromEntries(
              [...(payload.options.background||[100,130,170]),255].map((value,index)=>[['r','g','b','a'][index],value]))});
          const state=sessions.get(id);
          return Promise.resolve(new Response(new ReadableStream({start(controller) {
            active={controller,id,options:payload.options,startCount:state.shapes.length};
            controller.enqueue(encode({...snapshot(id,'start'),run_id:'run-'+runRequests.length,
              continued:Boolean(payload.session_id)}));
          }}),{headers:{'Content-Type':'application/x-ndjson'}}));
        }
        if(url==='/api/restore') {
          const payload=JSON.parse(settings.body),id='source-'+ ++nextId;restoreRequests.push(payload);
          sessions.set(id,{shapes:payload.result.shapes.slice(0,payload.shape_count),attempts:0,batches:0,
            retained:payload.shape_count,digest:payload.result.target_digest||digest(payload.options),
            background:payload.result.background});
          if(window.deferRestore) return new Promise(resolve=>restoreReplies.push(()=>
            resolve(json(snapshot(id,'restored')))));
          return Promise.resolve(json(snapshot(id,'restored')));
        }
        if(url==='/api/palette') {
          const payload=JSON.parse(settings.body);paletteRequests.push(payload);
          const response=()=>json({colors:[[payload.source.frame*100,payload.source.matte ? 255 : 0,0]],
            requested_max_colors:payload.max_colors,sample_width:80,sample_height:40});
          if(window.deferPalette) return new Promise(resolve=>paletteReplies.push(()=>resolve(response())));
          return Promise.resolve(response());
        }
        if(String(url).endsWith('/focus')) return Promise.resolve(json({focus:JSON.parse(settings.body).focus,
          applies:'next_attempt'}));
        return fetch(url,settings);
      };
    }""")


def _prepare(page: Page, url: str, *, animated: bool = False) -> bytes:
    _mock(page)
    page.goto(url, wait_until="networkidle")
    data = _image(animated)
    page.locator("#image-input").set_input_files({"name": "source.unknown", "mimeType": "", "buffer": data})
    expect(page.locator("#status")).to_have_text("Ready")
    page.locator(".source-controls summary").click()
    page.locator("#steps-number").fill("1")
    return data


def _run(page: Page) -> None:
    page.locator("#run-button").click()
    page.wait_for_function("runRequests.length && document.querySelector('#run-button').textContent==='Running'")
    page.evaluate("finishSourceRun()")
    expect(page.locator("#run-button")).to_have_text("Continue")


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 1000}])
def test_source_original_bytes_static_preview_and_applied_creation_policy(
    server_url: str, tmp_path: Path, viewport: dict[str, int]
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=viewport)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        original = _prepare(page, server_url, animated=True)
        expect(page.locator("#source-meta")).to_have_text("1600 x 800")
        assert page.locator("#source-preview").get_attribute("src").startswith("data:image/png;base64,")
        expect(page.locator("#source-matte")).to_have_value("white")
        _run(page)
        first = _save(page, tmp_path / "first.json")
        assert first["source"]["width"] == 1600 and first["source"]["height"] == 800
        assert first["source"]["data_url"] == "data:image/gif;base64," + base64.b64encode(original).decode()
        assert first["options"]["source"] == {"frame": 0, "matte": [255, 255, 255]}
        page.locator("#source-frame").fill("1")
        page.locator("#source-matte").select_option("none")
        page.locator("#canvas-background").select_option("custom")
        page.locator("#canvas-background-color").fill("#123456")
        draft = _save(page, tmp_path / "draft.json")
        assert draft["options"]["source"] == first["options"]["source"]
        assert draft["options"]["background"] is None
        _run(page)
        assert page.evaluate("runRequests[1].options.source") == first["options"]["source"]
        assert page.evaluate("runRequests[1].options.background") is None
        page.locator("#experiment-fork").click()
        expect(page.locator("#status")).to_contain_text("ready to Continue")
        assert page.evaluate("restoreRequests[0].options.source") == first["options"]["source"]
        assert page.evaluate("restoreRequests[0].result.target_digest") == first["result"]["target_digest"]
        # Installing the fork restores applied controls; re-enter creation drafts explicitly.
        page.locator("#source-frame").fill("1")
        page.locator("#source-matte").select_option("none")
        page.locator("#canvas-background").select_option("custom")
        page.locator("#canvas-background-color").fill("#123456")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        _run(page)
        updated = _save(page, tmp_path / "updated.json")
        assert updated["version"] == 3
        assert updated["options"]["source"] == {"frame": 1, "matte": None}
        assert updated["options"]["background"] == [18, 52, 86]
        assert updated["result"]["background"] == {"r": 18, "g": 52, "b": 86, "a": 255}
        assert updated["result"]["target_digest"] == "b" * 64
        assert updated["history"]["branches"][-1]["parent_id"] is None
        assert updated["history"]["branches"][0]["result"]["shapes"] == draft["result"]["shapes"] + [
            updated["history"]["branches"][1]["result"]["shapes"][1],
        ]
        page.locator("#zoom-in").click()
        zoom = page.locator("#zoom-value").inner_text()
        page.locator("#experiment-select").select_option("experiment-1")
        expect(page.locator("#status")).to_contain_text("Original selected")
        expect(page.locator("#source-frame")).to_have_value("0")
        expect(page.locator("#source-matte")).to_have_value("white")
        expect(page.locator("#canvas-background")).to_have_value("average")
        expect(page.locator("#zoom-value")).to_have_text(zoom)
        assert _save(page, tmp_path / "original-head.json")["result"]["shapes"] == (
            updated["history"]["branches"][0]["result"]["shapes"]
        )
        page.locator("#project-input").set_input_files(tmp_path / "updated.json")
        expect(page.locator("#status")).to_contain_text("Restore or fork to continue")
        expect(page.locator("#source-frame")).to_have_value("1")
        expect(page.locator("#canvas-background-color")).to_have_value("#123456")
        expect(page.locator("#run-button")).to_be_disabled()
        page.locator("#experiment-fork").click()
        expect(page.locator("#status")).to_contain_text("ready to Continue")
        assert page.evaluate("restoreRequests.at(-1).result.background") == updated["result"]["background"]
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("late_error", [False, True])
@pytest.mark.parametrize("replacement", ["source", "invalid_image", "invalid_project", "branch"])
def test_preparation_supersession_ignores_stale_success_error_and_finally(
    server_url: str, tmp_path: Path, late_error: bool, replacement: str
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        page.locator("#restart-button").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        _run(page)
        page.evaluate("deferPrepare=true")
        page.locator("#source-frame").fill("1")
        page.locator("#source-apply").click()
        page.wait_for_function("prepareReplies.length===1")
        expect(page.locator("#run-button")).to_have_text("Preparing")
        expect(page.locator("#paint-toggle")).to_be_disabled()
        expect(page.locator("#history-count")).to_be_disabled()
        expect(page.locator("#experiment-fork")).to_be_disabled()
        if replacement == "source":
            page.locator("#image-input").set_input_files({
                "name": "new.gif", "mimeType": "application/octet-stream", "buffer": _image(True),
            })
            page.wait_for_function("prepareReplies.length===2")
        elif replacement == "invalid_image":
            page.locator("#image-input").set_input_files({
                "name": "invalid.svg", "mimeType": "image/svg+xml", "buffer": b"<svg/>",
            })
            expect(page.locator("#status")).to_contain_text("Could not load image")
        elif replacement == "invalid_project":
            page.locator("#project-input").set_input_files({
                "name": "bad.json", "mimeType": "application/json", "buffer": b"{",
            })
            expect(page.locator("#status")).to_contain_text("Could not open project")
        else:
            page.locator("#experiment-select").select_option("experiment-1")
            page.wait_for_function("prepareReplies.length===2")
        feedback = page.locator("#status").inner_text()
        page.evaluate("error=>prepareReplies[0](error)", late_error)
        page.evaluate("() => new Promise(resolve=>setTimeout(resolve,0))")
        expect(page.locator("#status")).to_have_text(feedback)
        if replacement in ("source", "branch"):
            expect(page.locator("#run-button")).to_have_text("Preparing")
            expect(page.locator("#source-apply")).to_be_disabled()
            page.evaluate("prepareReplies[1]()")
        expect(page.locator("#run-button")).to_be_enabled()
        saved = _save(page, tmp_path / "superseded.json")
        if replacement == "source":
            assert saved["source"]["name"] == "new.gif" and len(saved["history"]["branches"]) == 1
        else:
            assert len(saved["history"]["branches"]) == 2
            assert saved["options"]["source"] == {"frame": 0, "matte": [255, 255, 255]}
            assert len(saved["result"]["shapes"]) == 1
            assert saved["history"]["active_branch"] == ("experiment-1" if replacement == "branch" else "experiment-2")
        browser.close()


def test_prepare_failure_capacity_and_cancel_keep_original_experiment_usable(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        page.evaluate("deferPrepare=true")
        page.locator("#source-frame").fill("1")
        page.locator("#source-apply").click()
        page.wait_for_function("prepareReplies.length===1")
        # Programmatic events must obey the same preparing lock as disabled controls.
        page.locator("#history-count").evaluate("el=>{el.value='0';el.dispatchEvent(new Event('change'));}")
        page.locator("#experiment-name").evaluate("el=>{el.value='Unexpected';}")
        page.locator("#experiment-rename").dispatch_event("click")
        page.locator("#experiment-fork").dispatch_event("click")
        assert page.evaluate("restoreRequests.length") == 0
        page.evaluate("prepareReplies[0](true)")
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#status")).to_contain_text("Preparation rejected")
        failed = _save(page, tmp_path / "failed.json")
        assert failed["history"]["view_shape_count"] == 1
        assert failed["history"]["branches"][0]["name"] == "Original"
        assert failed["options"]["source"]["frame"] == 0
        assert len(failed["history"]["branches"]) == 1
        page.evaluate("deferPrepare=false")
        page.locator("#restart-button").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        page.evaluate("deferPrepare=true")
        page.locator("#experiment-select").select_option("experiment-1")
        page.wait_for_function("prepareReplies.length===2")
        page.locator("#experiment-select").select_option("experiment-2")
        expect(page.locator("#run-button")).to_have_text("Run")
        page.evaluate("prepareReplies[1]()")
        expect(page.locator("#experiment-select")).to_have_value("experiment-2")
        expect(page.locator("#source-frame")).to_have_value("1")
        browser.close()


@pytest.mark.parametrize("unsupported_version", [1, 2])
def test_unsupported_versions_preserve_scene_and_inactive_invalid_frames_reject(
    server_url: str, tmp_path: Path, unsupported_version: int
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        project = _save(page, tmp_path / "modern.json")
        project["version"] = unsupported_version
        unsupported_path = tmp_path / "unsupported.json"
        unsupported_path.write_text(json.dumps(project), encoding="utf-8")
        before = page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()")
        page.locator("#project-input").set_input_files(unsupported_path)
        expect(page.locator("#status")).to_contain_text("only version 3")
        assert page.locator("#result-canvas").evaluate("canvas => canvas.toDataURL()") == before
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#source-matte")).to_have_value("white")
        page.locator("#experiment-fork").click()
        expect(page.locator("#status")).to_contain_text("ready to Continue")
        assert page.evaluate("restoreRequests.at(-1).options.source") == {"frame": 0, "matte": [255, 255, 255]}
        roundtrip = _save(page, tmp_path / "current-roundtrip.json")
        assert roundtrip["options"]["source"] == {"frame": 0, "matte": [255, 255, 255]}
        bad = json.loads(json.dumps(roundtrip))
        bad["history"]["branches"][0]["parent_id"] = None
        bad["history"]["branches"][1]["parent_id"] = None
        bad["history"]["branches"][1]["fork_shape_count"] = 0
        bad["history"]["branches"][0]["options"]["source"]["frame"] = 2
        bad_path = tmp_path / "unavailable-frame.json"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        page.locator("#project-input").set_input_files(bad_path)
        expect(page.locator("#status")).to_contain_text("frame outside the available source prefix")
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#source-matte")).to_have_value("white")
        browser.close()


@pytest.mark.parametrize("behavior", ["click", "hold"])
def test_paint_uses_full_prepared_metadata_and_palette_uses_applied_target(
    server_url: str, tmp_path: Path, behavior: str
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        page.evaluate("targetHeight=400")
        page.locator("#source-frame").fill("1")
        page.locator("#source-matte").select_option("black")
        page.locator(".palette-controls summary").click()
        page.locator("#palette-extract").click()
        expect(page.locator("#palette-mode")).to_have_value("exact")
        assert page.evaluate("paletteRequests[0].source") == {"frame": 0, "matte": [255, 255, 255]}
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        page.locator("#palette-extract").click()
        expect(page.locator("#palette-colors")).to_have_value("#64FF00")
        assert page.evaluate("paletteRequests[1].source") == {"frame": 1, "matte": [0, 0, 0]}
        page.locator("#paint-toggle").click()
        page.locator("#paint-behavior").select_option(behavior)
        canvas = page.locator("#result-canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        assert box and box["width"] / box["height"] == pytest.approx(4, rel=0.01)
        page.mouse.move(box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.5)
        page.mouse.down()
        if behavior == "click":
            page.mouse.up()
        page.wait_for_function("runRequests.length===1")
        assert page.evaluate("runRequests[0].options.source") == {"frame": 1, "matte": [0, 0, 0]}
        assert page.evaluate("runRequests[0].options.steps") == 1
        page.locator("#source-apply").dispatch_event("click")
        assert page.evaluate("prepareRequests.length") == 2
        page.evaluate("finishSourceRun()")
        if behavior == "hold":
            page.wait_for_function("runRequests.length===2")
            page.mouse.up()
            assert page.evaluate("runRequests[1].options.source") == {"frame": 1, "matte": [0, 0, 0]}
            page.evaluate("finishSourceRun()")
        expect(page.locator("#run-button")).to_be_enabled()
        saved = _save(page, tmp_path / "paint-source.json")
        assert all(batch["source"] == saved["options"]["source"] for batch in saved["telemetry"]["batches"])
        browser.close()


def test_apng_default_image_and_truncated_frame_prefix_are_explicit(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        page.evaluate("targetFrames=256;truncatedFrames=true;defaultImage=true")
        page.locator("#restart-button").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        expect(page.locator("#source-options-meta")).to_contain_text("first 256 frames")
        expect(page.locator("#source-options-meta")).to_contain_text("default image is index 0")
        expect(page.locator("#source-frame-label")).to_have_text("Frame index · default image")
        expect(page.locator("#source-frame")).to_have_attribute("max", "255")
        page.locator("#source-frame").fill("256")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_contain_text("integer from 0 to 255")
        assert page.evaluate("prepareRequests.length") == 2
        page.locator("#source-frame").fill("255")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        assert page.evaluate("prepareRequests[2].source.frame") == 255
        browser.close()


def test_failed_branch_preparation_retains_applied_target_and_session(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        page.locator("#source-frame").fill("1")
        page.locator("#source-matte").select_option("black")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        _run(page)
        before = _save(page, tmp_path / "before-switch.json")
        page.evaluate("deferPrepare=true")
        page.locator("#experiment-select").select_option("experiment-1")
        page.wait_for_function("prepareReplies.length===1")
        page.evaluate("prepareReplies[0](true)")
        expect(page.locator("#status")).to_contain_text("Could not select experiment")
        expect(page.locator("#experiment-select")).to_have_value("experiment-2")
        expect(page.locator("#source-frame")).to_have_value("1")
        expect(page.locator("#source-matte")).to_have_value("black")
        expect(page.locator("#run-button")).to_have_text("Continue")
        after = _save(page, tmp_path / "after-switch.json")
        assert after["result"] == before["result"]
        _run(page)
        assert page.evaluate("runRequests.at(-1).session_id") == "source-2"
        assert page.evaluate("runRequests.at(-1).options.source") == {"frame": 1, "matte": [0, 0, 0]}
        browser.close()


def test_creation_capacity_is_checked_before_preparation(server_url: str, tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.add_init_script("window.historyLimit=2")
        _prepare(page, server_url)
        _run(page)
        page.locator("#restart-button").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        _run(page)
        page.locator("#source-frame").fill("1")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_contain_text("at most 2 experiments")
        assert page.evaluate("prepareRequests.length") == 2
        expect(page.locator("#run-button")).to_have_text("Continue")
        saved = _save(page, tmp_path / "capacity.json")
        assert len(saved["history"]["branches"]) == 2 and saved["options"]["source"]["frame"] == 0
        browser.close()


def test_invalid_creation_drafts_do_not_block_continue_or_enter_committed_options(
    server_url: str, tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        page.locator("#source-frame").fill("256")
        page.locator("#canvas-background").select_option("custom")
        page.locator("#canvas-background-color").fill("invalid")
        _run(page)
        assert page.evaluate("runRequests.at(-1).options.source") == {"frame": 0, "matte": [255, 255, 255]}
        assert page.evaluate("runRequests.at(-1).options.background") is None
        assert page.locator("#source-frame").evaluate("el=>el.form") is None
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_contain_text("integer from 0 to 255")
        page.locator("#source-frame").fill("2")
        page.locator("#canvas-background").select_option("average")
        page.locator("#source-apply").click()
        expect(page.locator("#status")).to_contain_text("frame index from 0 to 1")
        assert page.evaluate("prepareRequests.length") == 1
        committed = _save(page, tmp_path / "invalid-creation-drafts.json")
        assert committed["options"]["source"]["frame"] == 0 and committed["options"]["background"] is None
        browser.close()


@pytest.mark.parametrize("replacement", ["invalid_image", "invalid_project"])
def test_invalid_replacement_supersedes_restore_without_stuck_controls(
    server_url: str, tmp_path: Path, replacement: str
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _prepare(page, server_url)
        _run(page)
        page.evaluate("deferRestore=true")
        page.locator("#experiment-fork").click()
        page.wait_for_function("restoreReplies.length===1")
        if replacement == "invalid_image":
            page.locator("#image-input").set_input_files({
                "name": "bad.svg", "mimeType": "image/svg+xml", "buffer": b"<svg/>",
            })
        else:
            page.locator("#project-input").set_input_files({
                "name": "bad.json", "mimeType": "application/json", "buffer": b"{",
            })
        expect(page.locator("#status")).to_contain_text("Could not")
        status = page.locator("#status").inner_text()
        page.evaluate("restoreReplies[0]()")
        expect(page.locator("#status")).to_have_text(status)
        expect(page.locator("#run-button")).to_have_text("Continue")
        expect(page.locator("#source-apply")).to_be_enabled()
        retained = _save(page, tmp_path / "retained-parent.json")
        assert len(retained["history"]["branches"]) == 1 and len(retained["result"]["shapes"]) == 1
        browser.close()
