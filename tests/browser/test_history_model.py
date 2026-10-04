from __future__ import annotations

import base64
import io
import json
import threading
from collections.abc import Iterator

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

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


def _source() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), (130, 170, 210)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_history_model_preserves_heads_snapshots_and_point_capacity(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate("""async () => {
          const {ReconstructionHistory,validateHistory} = await import('/static/history.js');
          const limits = {max_branches:3,max_shapes:10,max_history_shapes:12,max_history_points:8};
          const options = {shape_types:['polyline'],focus:null}, batches = [{index:1}];
          const shapes = [{type:'polyline',data:{points:[[0,0],[1,1],[2,2],[3,3]]}}];
          const history = new ReconstructionHistory(limits);
          const snapshot = {result:{width:8,height:8,background:[1,2,3,255],shapes},
            options,sessionId:'ephemeral',telemetry:{attempts:2,batches}};
          const parent = history.add(snapshot); history.inspect(0);
          const sameShapes = parent.result.shapes === shapes, count = parent.result.shapes.length;
          batches.push({index:2}); options.shape_types.push('circle');
          const parentBatches = parent.telemetry.batches.length, parentTypes = parent.options.shape_types.length;
          history.add(snapshot,{name:'Child',parent_id:parent.id,fork_shape_count:1});
          const capacity = history.fitCapacity(['polyline']), circleCapacity = history.fitCapacity(['circle']);
          let deletion; try {history.remove(parent.id);} catch(error) {deletion=error.message;}
          const serialized = history.serialize(result => result);
          const raw = {...serialized,branches:history.branches.map(branch => ({...branch,name:' '+branch.name+' '}))};
          const validated = validateHistory(raw,limits);
          history.select(parent.id); history.remove(serialized.active_branch);
          return {sameShapes,count,parentBatches,parentTypes,capacity,circleCapacity,deletion,serialized,
            rawName:raw.branches[0].name,normalizedName:validated.branches[0].name,remaining:history.branches.length};
        }""")
        assert result["sameShapes"] and result["count"] == 1
        assert result["parentBatches"] == result["parentTypes"] == 1
        assert result["capacity"] == 0 and result["circleCapacity"] == 9
        assert "Remove “Child” first" in result["deletion"]
        assert result["rawName"] == " Original " and result["normalizedName"] == "Original"
        assert result["remaining"] == 1
        assert "sessionId" not in json.dumps(result["serialized"])
        assert "result" not in result["serialized"]["branches"][1]
        assert result["serialized"]["branches"][0]["result"]["shapes"]
        browser.close()


def test_project_history_strict_validation_and_legacy_loading(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        results = page.evaluate("""async dataUrl => {
          const {validateProject,openProjectFile} = await import('/static/project.js');
          const contract = await (await fetch('/api/config')).json(), options = contract.defaults;
          const color = {r:10,g:20,b:30,a:255}, shape = {type:'circle',color,data:{x:2,y:2,r:1}};
          const scene = {width:8,height:8,background:color,shapes:[shape],preview_data_url:null};
          const telemetry = {attempts:1,duration_ms:0,initial_score:0.9,batches:[]};
          const project = {format:'geometrize-project',version:2,source:{name:'Source',data_url:dataUrl},
            options,result:scene,telemetry,history:{active_branch:'child',view_shape_count:0,branches:[
              {id:'parent',name:'Parent',parent_id:null,fork_shape_count:0,options,result:structuredClone(scene),telemetry},
              {id:'child',name:' Child ',parent_id:'parent',fork_shape_count:1,options}]}};
          const cases = [
            ['duplicate IDs', p => p.history.branches.push(structuredClone(p.history.branches[0])), 'IDs'],
            ['bad ID', p => p.history.branches[0].id='../bad', 'IDs'],
            ['duplicate names', p => p.history.branches[0].name='child', 'names must be unique'],
            ['empty name', p => p.history.branches[0].name=' ', 'name must contain'],
            ['long name', p => p.history.branches[0].name='x'.repeat(81), 'name must contain'],
            ['missing parent', p => p.history.branches[1].parent_id='missing', 'parent does not exist'],
            ['cycle', p => p.history.branches[0].parent_id='child', 'cycle'],
            ['child prefix', p => p.result.shapes=[], 'fewer shapes'],
            ['parent prefix', p => p.history.branches[0].result.shapes=[], 'exceeds its parent'],
            ['dimensions', p => p.history.branches[0].result.width=9, 'dimensions and background'],
            ['background', p => p.history.branches[0].result.background.r=11, 'dimensions and background'],
            ['cursor', p => p.history.view_shape_count=2, 'inspected shape count'],
            ['cursor boolean', p => p.history.view_shape_count=true, 'inspected shape count'],
            ['retained', p => p.result.restored_shape_count=2, 'retained shape count'],
            ['unknown metadata', p => p.history.extra=1, 'unknown field'],
            ['saved session', p => p.history.branches[1].sessionId='live', 'unknown field'],
            ['duplicate active scene', p => p.history.branches[1].result=p.result, 'top-level'],
          ];
          const errors = cases.map(([name,mutate,want]) => {
            const raw = structuredClone(project); mutate(raw);
            try {validateProject(raw,contract);return {name,want,error:null};}
            catch(error) {return {name,want,error:error.message};}
          });
          const narrow = structuredClone(contract); narrow.project.max_history_shapes=1;
          try {validateProject(project,narrow);}
          catch(error) {errors.push({name:'shape cap',want:'total shapes',error:error.message});}
          const points = structuredClone(project);
          points.result.shapes=[{type:'polyline',color,data:{points:[[0,0],[1,1],[2,2],[3,3]]}}];
          points.history.branches[0].result.shapes=points.result.shapes;
          narrow.project.max_history_shapes=10; narrow.project.max_history_points=7;
          try {validateProject(points,narrow);}
          catch(error) {errors.push({name:'point cap',want:'total polyline points',error:error.message});}
          let decoded = 0; const RealImage = window.Image;
          window.Image = class {constructor() {decoded++;}};
          try {await openProjectFile(new File([JSON.stringify(points)],'large.json'),narrow);}
          catch(error) {errors.push({name:'before decode',want:'total polyline points',error:error.message});}
          finally {window.Image=RealImage;}
          const accepted = validateProject(project,contract), legacy = structuredClone(project);
          legacy.version=1; delete legacy.history; delete legacy.result.restored_shape_count;
          const old = validateProject(legacy,contract);
          return {errors,decoded,name:accepted.history.branches[1].name,rawName:project.history.branches[1].name,
            legacy:{name:old.history.branches[0].name,cursor:old.history.view_shape_count,retained:old.result.restored_shape_count}};
        }""", "data:image/png;base64," + base64.b64encode(_source()).decode())
        for result in results["errors"]:
            assert result["error"] and result["want"] in result["error"], result
        assert results["decoded"] == 0
        assert results["name"] == "Child" and results["rawName"] == " Child "
        assert results["legacy"] == {"name":"Original", "cursor":1, "retained":0}
        browser.close()
