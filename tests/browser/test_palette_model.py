from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
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


def test_palette_validation_defaults_and_nested_snapshot_isolation(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate("""async () => {
          const {validatePalette,parseHexColors,copyPalette} = await import('/static/palette.js');
          const {ReconstructionHistory} = await import('/static/history.js');
          const {validateProject} = await import('/static/project.js');
          const contract=await (await fetch('/api/config')).json();
          const palette=validatePalette({colors:[[1,2,3],[1,2,3],[4,5,6]]});
          const malformed=[{colors:[]},{colors:Array(33).fill([0,0,0])},{colors:[[0,0,256]]},
            {colors:[[0,0,true]]},{colors:[[0,0,1.5]]},{colors:[[1,2,3,4]]},{colors:['#123']},
            {colors:[[1,2,3]],strength:null},{colors:[[1,2,3]],strength:true},
            {colors:[[1,2,3]],strength:-0.1},{colors:[[1,2,3]],strength:1.01},
            {colors:[[1,2,3]],strength:NaN},{colors:[[1,2,3]],strength:Infinity},{colors:[[1,2,3]],extra:1}];
          const errors=malformed.map(value => {
            try {validatePalette(value);return null;} catch(error) {return error.message;}});
          const source=document.createElement('canvas'); source.width=8;source.height=8;
          const options={...contract.defaults,palette};
          const telemetry={attempts:0,duration_ms:0,initial_score:null,batches:[]};
          const scene={width:8,height:8,background:[0,0,0,255],shapes:[],preview_data_url:null};
          const project={format:'geometrize-project',version:2,source:{name:'Source',data_url:source.toDataURL()},
            options,result:scene,telemetry,history:{active_branch:'a',view_shape_count:0,branches:[
              {id:'a',name:'A',parent_id:null,fork_shape_count:0,options:{...options,palette:{colors:[[90,80,70]],strength:0}}},
              {id:'b',name:'B',parent_id:null,fork_shape_count:0,options:{...options,palette:{colors:[[90,80,70]],strength:0}},
                result:scene,telemetry}]}};
          const normalized=validateProject(project,contract);
          const authority=copyPalette(normalized.history.branches[0].options.palette);
          normalized.history.branches[0].options.palette.colors[0][0]=99;
          const independentlyCopied=normalized.options.palette.colors[0][0]===1;
          const history=new ReconstructionHistory(contract.project);
          const batch={index:1,palette:copyPalette(palette)};
          const parent=history.add({result:scene,options,telemetry:{...telemetry,batches:[batch]},sessionId:'live'});
          options.palette.colors[0][0]=100;batch.palette.colors[0][0]=200;
          const isolated={option:parent.options.palette.colors[0][0],
            batch:parent.telemetry.batches[0].palette.colors[0][0]};
          const inactive=normalized.history.branches[1].options.palette;
          const legacy=structuredClone(project);legacy.version=1;delete legacy.history;delete legacy.options.palette;
          const old=validateProject(legacy,contract);
          const oldV2=structuredClone(project);delete oldV2.options.palette;
          oldV2.history.branches.forEach(branch => delete branch.options.palette);
          const normalizedV2=validateProject(oldV2,contract);
          const invalidProjectCases=[p => p.history.branches[1].options.palette={colors:[[0,0,true]]},
            p => p.history.branches[0].options.palette={colors:[[1,2,3]],strength:null},
            p => p.telemetry.batches=[{index:1,target:1,shapeTypes:['circle'],candidates:1,mutations:1,alpha:1,
              added:0,attempts:0,palette:{colors:[[1,2,3]],strength:null}}]];
          const projectErrors=invalidProjectCases.map(mutate => {
            const p=structuredClone(project);mutate(p);
            try {validateProject(p,contract);return null;} catch(error) {return error.message;}});
          return {palette:authority,errors,parsed:parseHexColors('#abc, AABBCC #012345'),independentlyCopied,isolated,
            inactive,legacy:old.options.palette,
            legacyV2:normalizedV2.history.branches.map(branch => branch.options.palette),
            projectErrors,softZero:validatePalette({colors:[[1,2,3]],strength:0})};
        }""")
        assert result["palette"] == {"colors":[[1,2,3],[4,5,6]], "strength":1}
        assert all(result["errors"])
        assert result["parsed"] == [[170,187,204],[1,35,69]]
        assert result["independentlyCopied"] and result["isolated"] == {"option":1,"batch":1}
        assert result["inactive"] == {"colors":[[90,80,70]],"strength":0}
        assert result["legacy"] is None
        assert result["legacyV2"] == [None,None]
        assert all(result["projectErrors"])
        assert result["softZero"] == {"colors":[[1,2,3]],"strength":0}
        browser.close()
