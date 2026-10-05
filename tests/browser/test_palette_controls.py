from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page, expect


def _save(page: Page, path: Path) -> dict:
    with page.expect_download() as download:
        page.locator("#save-project-button").click()
    download.value.save_as(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _mock(page: Page) -> None:
    page.evaluate("""() => {
      const fetch = window.fetch.bind(window), sessions = new Map(), background = {r:100,g:130,b:170,a:255};
      const json = (data,status=200) => new Response(JSON.stringify(data),
        {status,headers:{'Content-Type':'application/json'}});
      const encode = event => new TextEncoder().encode(JSON.stringify(event)+'\\n');
      let nextId=0, active;
      window.runRequests=[]; window.paletteRequests=[]; window.paletteReplies=[]; window.restoreReplies=[];
      const snapshot = (id,event='snapshot') => {
        const state=sessions.get(id);
        return {event,session_id:id,width:80,height:40,background,shapes:[...state.shapes],attempts:state.attempts,
          revision:state.shapes.length,restored_shape_count:state.retained,initial_score:0.9};
      };
      window.finishPaletteRun = () => {
        const {controller,id,options,startCount} = active, state=sessions.get(id);
        const rgb=options.palette?.strength ? options.palette.colors[0] : [40,70,90];
        for(let n=0;n<options.steps;n++) state.shapes.push({type:'circle',
          color:{r:rgb[0],g:rgb[1],b:rgb[2],a:options.alpha},
          data:{x:10+state.shapes.length*10,y:20,r:4},score:0.8-state.shapes.length*0.05});
        state.attempts++; state.batches++;
        controller.enqueue(encode({...snapshot(id,'complete'),stop_reason:'target_reached',batch_summary:{
          index:state.batches,target:options.steps,shapeTypes:options.shape_types,candidates:options.shape_count,
          mutations:options.mutations,alpha:options.alpha,seed:options.seed,max_threads:0,effective_threads:1,
          start_shape_count:startCount,start_attempts:state.attempts-1,added:options.steps,attempts:1,
          state:'Target reached',reason:'target_reached',palette:options.palette}}));
        controller.close(); active=null;
      };
      window.fetch=(url,settings={}) => {
        if(url==='/api/run/stream') {
          const payload=JSON.parse(settings.body); runRequests.push(payload);
          const id=payload.session_id || 'palette-'+ ++nextId;
          if(!sessions.has(id)) sessions.set(id,{shapes:[],attempts:0,retained:0,batches:0});
          const state=sessions.get(id);
          return Promise.resolve(new Response(new ReadableStream({start(controller) {
            active={controller,id,options:payload.options,startCount:state.shapes.length};
            controller.enqueue(encode({...snapshot(id,'start'),run_id:'run-'+runRequests.length,
              continued:Boolean(payload.session_id)}));
          }}),{headers:{'Content-Type':'application/x-ndjson'}}));
        }
        if(url==='/api/palette') {
          const payload=JSON.parse(settings.body); paletteRequests.push(payload);
          return new Promise(resolve => paletteReplies.push((colors,error=false) => resolve(error ?
            json({code:'invalid_image',error:'Late extraction error'},400) : json({colors,
              requested_max_colors:payload.max_colors,sample_width:80,sample_height:40}))));
        }
        if(url==='/api/restore') {
          const payload=JSON.parse(settings.body), id='palette-'+ ++nextId;
          sessions.set(id,{shapes:payload.result.shapes.slice(0,payload.shape_count),retained:payload.shape_count,
            attempts:0,batches:0});
          if(window.deferPaletteRestore) return new Promise(resolve => restoreReplies.push(() =>
            resolve(json(snapshot(id,'restored')))));
          return Promise.resolve(json(snapshot(id,'restored')));
        }
        if(String(url).endsWith('/focus')) return Promise.resolve(json({focus:JSON.parse(settings.body).focus,
          applies:'next_attempt'}));
        return fetch(url,settings);
      };
    }""")


def _prepare(page: Page, url: str) -> None:
    page.goto(url, wait_until="networkidle")
    image = io.BytesIO()
    Image.new("RGB", (80,40), (100,130,170)).save(image, format="PNG")
    page.locator("#image-input").set_input_files({
        "name":"source.png", "mimeType":"image/png", "buffer":image.getvalue(),
    })
    expect(page.locator("#status")).to_have_text("Ready")
    _mock(page)
    page.locator(".palette-controls summary").click()
    page.locator("#steps-number").fill("1")


def _colors(page: Page, value: str) -> None:
    page.locator("#palette-colors").fill(value)
    page.locator("#palette-apply").click()


def _run(page: Page) -> None:
    page.locator("#run-button").click()
    page.evaluate("finishPaletteRun()")
    expect(page.locator("#run-button")).to_have_text("Continue")


def _click_result(page: Page, x: float = 0.5) -> None:
    page.locator("#result-canvas").scroll_into_view_if_needed()
    box = page.locator("#result-canvas").bounding_box()
    assert box
    page.mouse.click(box["x"]+box["width"]*x, box["y"]+box["height"]*0.5)


def test_native_exact_palette_keeps_retained_shapes_and_background(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    page.goto(server_url, wait_until="networkidle")
    expect(page.locator("#native-state")).to_have_text("Core ready")
    page.locator("#sample-button").click()
    page.locator("#max-size-number").fill("64")
    page.locator("#steps-number").fill("2")
    page.locator(".advanced-controls summary").click()
    page.locator("#shape-count").fill("16")
    page.locator("#mutations").fill("24")
    page.locator("#max-threads").fill("1")
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    original = _save(page, tmp_path / "native-off.json")
    page.locator(".palette-controls summary").click()
    _colors(page,"#F5DC82,#468246,#8C4628")
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    constrained = _save(page, tmp_path / "native-exact.json")
    assert constrained["result"]["shapes"][:2] == original["result"]["shapes"]
    assert constrained["result"]["background"] == original["result"]["background"]
    added = constrained["result"]["shapes"][2:]
    assert added
    colors = constrained["options"]["palette"]["colors"]
    for shape in added:
        assert [shape["color"][channel] for channel in ("r","g","b")] in colors
        assert shape["color"]["a"] == 128
    assert constrained["telemetry"]["batches"][0]["palette"] is None
    assert constrained["telemetry"]["batches"][1]["palette"] == constrained["options"]["palette"]
    page.locator("#experiment-fork").click()
    expect(page.locator("#status")).to_contain_text("ready to Continue")
    restored = _save(page, tmp_path / "native-exact-restored.json")
    assert [shape["color"] for shape in restored["result"]["shapes"]] == [
        shape["color"] for shape in constrained["result"]["shapes"]
    ]


def test_native_soft_zero_matches_off_and_source_extraction_is_bounded(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    page.goto(server_url, wait_until="networkidle")
    page.locator("#sample-button").click()
    page.locator("#max-size-number").fill("64")
    page.locator("#steps-number").fill("2")
    page.locator(".advanced-controls summary").click()
    page.locator("#shape-count").fill("16")
    page.locator("#mutations").fill("24")
    page.locator("#max-threads").fill("1")
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    off = _save(page, tmp_path / "off-native.json")
    page.locator(".palette-controls summary").click()
    page.locator("#palette-extract-count").fill("32")
    page.locator("#palette-extract").click()
    expect(page.locator("#palette-mode")).to_have_value("exact", timeout=30_000)
    count = page.locator("#palette-swatches li").count()
    assert 1 <= count <= 32
    page.locator("#palette-mode").select_option("soft")
    page.locator("#palette-strength-number").fill("0")
    page.locator("#palette-strength-number").dispatch_event("change")
    page.locator("#restart-button").click()
    page.locator("#run-button").click()
    expect(page.locator("#run-button")).to_have_text("Continue", timeout=120_000)
    zero = _save(page, tmp_path / "zero-native.json")
    assert zero["result"]["shapes"] == off["result"]["shapes"]
    assert zero["result"]["background"] == off["result"]["background"]
    assert zero["options"]["palette"]["strength"] == 0
    assert zero["telemetry"]["batches"][0]["palette"]["strength"] == 0


@pytest.mark.parametrize("viewport", [{"width":1440,"height":1000},{"width":390,"height":1000}])
def test_palette_committed_edits_modes_projects_and_branch_settings(
    server_url: str, tmp_path: Path, viewport: dict[str, int],
    browser: Browser,
) -> None:
    page = browser.new_page(viewport=viewport)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _prepare(page, server_url)
    _colors(page, "#123, #ABC, #112233")
    expect(page.locator("#palette-mode")).to_have_value("exact")
    expect(page.locator("#palette-swatches li")).to_have_count(2)
    _run(page)
    first = _save(page, tmp_path / "palette-first.json")
    assert first["options"]["palette"] == {"colors":[[17,34,51],[170,187,204]],"strength":1}
    page.locator("#palette-mode").select_option("soft")
    page.locator("#palette-strength-number").fill("0")
    page.locator("#palette-strength-number").dispatch_event("change")
    assert _save(page, tmp_path / "soft-zero.json")["options"]["palette"]["strength"] == 0
    page.locator("#palette-strength-number").fill("100")
    page.locator("#palette-strength-number").dispatch_event("change")
    expect(page.locator("#palette-mode")).to_have_value("exact")
    page.locator("#palette-colors").fill("not a hex color")
    page.locator("#palette-apply").click()
    expect(page.locator("#palette-colors")).to_have_attribute("aria-invalid","true")
    _run(page)
    assert page.evaluate("runRequests[1].options.palette") == first["options"]["palette"]
    saved = _save(page, tmp_path / "invalid-draft.json")
    assert saved["options"]["palette"] == first["options"]["palette"]
    assert saved["telemetry"]["batches"][0]["palette"] == first["options"]["palette"]
    _colors(page, "#F00")
    page.locator("#experiment-fork").click()
    expect(page.locator("#status")).to_contain_text("ready to Continue")
    branched = _save(page, tmp_path / "branched.json")
    assert branched["options"]["palette"] == {"colors":[[255,0,0]],"strength":1}
    assert branched["history"]["branches"][0]["result"]["shapes"] == saved["result"]["shapes"]
    page.locator("#experiment-select").select_option("experiment-1")
    expect(page.locator("#palette-colors")).to_have_value("#FF0000")
    _colors(page, "#00F")
    page.locator("#experiment-select").select_option("experiment-2")
    expect(page.locator("#palette-colors")).to_have_value("#FF0000")
    page.locator("#project-input").set_input_files(tmp_path / "branched.json")
    expect(page.locator("#status")).to_have_text("Project loaded — Restore or fork to continue")
    assert _save(page, tmp_path / "palette-round-trip.json")["history"] == branched["history"]
    page.locator("#palette-mode").select_option("off")
    expect(page.locator("#palette-colors")).to_have_value("#FF0000")
    assert _save(page, tmp_path / "off.json")["options"]["palette"] is None
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


@pytest.mark.parametrize("replacement", ["edit","source","branch","failed_import","restore"])
@pytest.mark.parametrize("error", [False,True])
def test_palette_extraction_ignores_changed_editor_source_branch_or_restore(
    server_url: str, replacement: str, error: bool,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    _colors(page, "#123")
    _run(page)
    if replacement == "branch":
        page.locator("#restart-button").click()
        expect(page.locator("#status")).to_have_text("New render ready")
        page.locator("#experiment-select").select_option("experiment-1")
        expect(page.locator("#status")).to_contain_text("Original selected")
    page.locator("#palette-extract").click()
    assert page.evaluate("paletteRequests[0].max_colors") == 8
    if replacement == "edit":
        _colors(page,"#F00")
    elif replacement == "source":
        page.locator("#sample-button").click()
        expect(page.locator("#status")).to_have_text("Ready")
    elif replacement == "branch":
        page.locator("#experiment-select").select_option("experiment-2")
        expect(page.locator("#experiment-select")).to_have_value("experiment-2")
    elif replacement == "failed_import":
        page.locator("#project-input").set_input_files({"name":"bad.json","mimeType":"application/json","buffer":b"{"})
        expect(page.locator("#status")).to_contain_text("Could not open project")
    else:
        page.evaluate("deferPaletteRestore=true")
        page.locator("#experiment-fork").click()
        expect(page.locator("#palette-mode")).to_be_disabled()
    mode = page.locator("#palette-mode").input_value()
    text = page.locator("#palette-colors").input_value()
    feedback = page.locator("#palette-status").inner_text()
    page.evaluate("error => paletteReplies[0]([[0,255,0]], error)", error)
    page.evaluate("() => new Promise(resolve => setTimeout(resolve,0))")
    expect(page.locator("#palette-mode")).to_have_value(mode)
    expect(page.locator("#palette-colors")).to_have_value(text)
    expect(page.locator("#palette-status")).to_have_text(feedback)
    if replacement == "restore":
        page.evaluate("restoreReplies[0]()")
        expect(page.locator("#palette-mode")).to_be_enabled()
        expect(page.locator("#palette-colors")).to_have_value("#112233")
    else:
        expect(page.locator("#palette-extract")).to_be_enabled()


@pytest.mark.parametrize("behavior", ["click","hold"])
def test_palette_requests_snapshot_each_stroke_and_extraction_survives_same_source_fitting(
    server_url: str, behavior: str,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    _colors(page,"#123")
    page.locator("#palette-extract-count").fill("32")
    page.locator("#palette-extract").click()
    page.locator("#paint-toggle").click()
    page.locator("#paint-behavior").select_option(behavior)
    if behavior == "click":
        _click_result(page, 0.3)
        _click_result(page, 0.7)
    else:
        page.locator("#result-canvas").scroll_into_view_if_needed()
        box = page.locator("#result-canvas").bounding_box()
        assert box
        page.mouse.move(box["x"]+box["width"]*0.5, box["y"]+box["height"]*0.5)
        page.mouse.down()
    page.evaluate("paletteReplies[0]([[0,255,0]])")
    expect(page.locator("#palette-colors")).to_have_value("#00FF00")
    assert page.evaluate("runRequests[0].options.palette.colors") == [[17,34,51]]
    page.evaluate("finishPaletteRun()")
    page.wait_for_function("runRequests.length===2")
    assert page.evaluate("runRequests[1].options.palette.colors") == [[0,255,0]]
    if behavior == "hold":
        page.mouse.up()
    page.evaluate("finishPaletteRun()")
    expect(page.locator("#run-button")).to_be_enabled()
    assert page.evaluate("runRequests.length") == 2
    assert page.evaluate("paletteRequests.length") == 1


def test_palette_extraction_counts_and_new_render_source_lifecycle(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page, server_url)
    for index,count in enumerate((1,32)):
        page.locator("#palette-extract-count").fill(str(count))
        page.locator("#palette-extract").click()
        assert page.evaluate(f"paletteRequests[{index}].max_colors") == count
        page.evaluate(f"paletteReplies[{index}]([[10,20,30]])")
        expect(page.locator("#palette-swatches li")).to_have_count(1)
    page.locator("#palette-mode").select_option("soft")
    page.locator("#palette-strength-number").fill("25")
    page.locator("#palette-strength-number").dispatch_event("change")
    _run(page)
    page.locator("#restart-button").click()
    same_source = _save(page, tmp_path / "same-source.json")
    assert same_source["options"]["palette"] == {"colors":[[10,20,30]],"strength":0.25}
    page.locator("#sample-button").click()
    expect(page.locator("#palette-mode")).to_have_value("off")
    expect(page.locator("#palette-swatches li")).to_have_count(0)
    assert _save(page, tmp_path / "new-source.json")["options"]["palette"] is None


def test_palette_edits_during_normal_batch_apply_to_next_batch(
    server_url: str, tmp_path: Path,
    browser: Browser,
) -> None:
    page = browser.new_page()
    _prepare(page,server_url)
    _colors(page,"#123")
    page.locator("#run-button").click()
    _colors(page,"#F00")
    assert page.evaluate("runRequests[0].options.palette.colors") == [[17,34,51]]
    page.evaluate("finishPaletteRun()")
    expect(page.locator("#run-button")).to_have_text("Continue")
    first = _save(page,tmp_path / "edited-mid-batch.json")
    assert first["options"]["palette"]["colors"] == [[255,0,0]]
    assert first["telemetry"]["batches"][0]["palette"]["colors"] == [[17,34,51]]
    _run(page)
    second = _save(page,tmp_path / "next-batch.json")
    assert [batch["palette"]["colors"] for batch in second["telemetry"]["batches"]] == [
        [[17,34,51]],[[255,0,0]],
    ]


@pytest.mark.parametrize("old_error", [False,True])
def test_palette_older_reply_keeps_newer_extraction_pending_and_errors_local(
    server_url: str, old_error: bool,
    browser: Browser,
) -> None:
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _prepare(page,server_url)
    _colors(page,"#123")
    page.locator("#palette-extract").click()
    _colors(page,"#F00")
    page.locator("#palette-extract-count").fill("")
    page.locator("#palette-extract").click()
    assert page.evaluate("paletteRequests[1].max_colors") == 8
    page.evaluate("error => paletteReplies[0]([[0,255,0]],error)",old_error)
    page.evaluate("() => new Promise(resolve => setTimeout(resolve,0))")
    expect(page.locator("#palette-extract")).to_be_disabled()
    expect(page.locator("#palette-status")).to_have_text("Extracting source colors…")
    expect(page.locator("#palette-colors")).to_have_value("#FF0000")
    page.evaluate("paletteReplies[1]([],true)")
    expect(page.locator("#palette-extract")).to_be_enabled()
    expect(page.locator("#palette-status")).to_contain_text("Could not extract palette")
    expect(page.locator("#palette-colors")).to_have_value("#FF0000")
    expect(page.locator("#status")).to_have_text("Ready")
    assert errors == []
