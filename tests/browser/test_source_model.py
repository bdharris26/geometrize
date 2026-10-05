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


def test_source_policy_validation_and_independent_experiment_snapshots(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate("""async () => {
          const {validateSource,validateRgb} = await import('/static/source.js');
          const {ReconstructionHistory} = await import('/static/history.js');
          const config = await (await fetch('/api/config')).json();
          const defaults = [undefined,null,{}].map(value => validateSource(value,config.source));
          const invalid = [[],true,{frame:null},{frame:true},{frame:0.5},{frame:-1},{frame:256},
            {frame:Infinity},{extra:1},{matte:[]},{matte:[0,0,256]},{matte:[0,true,0]},
            {matte:[1,2,3,4]},{matte:'#FFF'}];
          const errors = invalid.map(value => {
            try {validateSource(value,config.source);return null;} catch(error) {return error.message;}});
          const badBackgrounds = ['average',false,[1,2],[1,2,3,4],[0,0,0.5],[0,0,-1]];
          const backgrounds = badBackgrounds.map(value => {
            try {validateRgb(value);return null;} catch(error) {return error.message;}});
          const options = {...config.defaults,source:{frame:1,matte:[1,2,3]},background:[4,5,6]};
          const batch = {index:1,source:{frame:1,matte:[7,8,9]},background:[10,11,12]};
          const history = new ReconstructionHistory(config.project);
          const original = history.add({options,result:{width:8,height:8,background:[4,5,6,255],shapes:[]},
            sessionId:'private',telemetry:{attempts:0,batches:[batch]}});
          options.source.matte[0]=100;options.background[0]=101;
          batch.source.matte[0]=102;batch.background[0]=103;
          const isolated = {source:original.options.source,background:original.options.background,
            batch:original.telemetry.batches[0]};
          const serialized = history.serialize(value => value);
          serialized.branches[0].options.source.matte[0]=104;
          serialized.branches[0].options.background[0]=105;
          return {defaults,errors,backgrounds,isolated,serializedHasSession:'sessionId' in serialized.branches[0]};
        }""")
        assert result["defaults"] == [{"frame": 0, "matte": None}] * 3
        assert all(result["errors"]) and all(result["backgrounds"])
        assert result["isolated"] == {
            "source": {"frame": 1, "matte": [1, 2, 3]}, "background": [4, 5, 6],
            "batch": {"index": 1, "source": {"frame": 1, "matte": [7, 8, 9]}, "background": [10, 11, 12]},
        }
        assert not result["serializedHasSession"]
        browser.close()


def test_source_project_target_graph_and_active_options_authority(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate("""async () => {
          const {validateProject} = await import('/static/project.js');
          const config = await (await fetch('/api/config')).json();
          const canvas = document.createElement('canvas');canvas.width=8;canvas.height=8;
          const options = {...config.defaults,source:{frame:1,matte:[1,2,3]},background:[4,5,6]};
          const scene = {width:8,height:8,background:[4,5,6,255],shapes:[],target_digest:'a'.repeat(64)};
          const telemetry = {attempts:0,duration_ms:0,initial_score:null,batches:[]};
          const base = {format:config.project.format,version:3,source:{data_url:canvas.toDataURL()},
            options,result:scene,telemetry,history:{active_branch:'a',view_shape_count:0,branches:[
              {id:'a',name:'Active',parent_id:null,fork_shape_count:0,
                options:{...options,source:{frame:0,matte:null},background:null}},
              {id:'b',name:'Child',parent_id:'a',fork_shape_count:0,options,result:scene,telemetry}]}};
          const normalized=validateProject(base,config);
          const authority={source:structuredClone(normalized.history.branches[0].options.source),
            background:structuredClone(normalized.history.branches[0].options.background)};
          normalized.history.branches[0].options.source.matte[0]=77;
          normalized.history.branches[0].options.background[0]=88;
          const isolated=normalized.options.source.matte[0]===1 && normalized.options.background[0]===4;
          const invalid=[p=>p.version=1,p=>p.version=2,p=>p.version=4,
            p=>p.options.source={frame:null},p=>p.options.background=[0,0,true],
            p=>p.result.target_digest=null,p=>p.result.target_digest='bad',
            p=>p.history.branches[1].options.source={frame:0,matte:[1,2,3]},
            p=>p.history.branches[1].options.source={frame:1,matte:null},
            p=>p.history.branches[1].result.target_digest='b'.repeat(64),
            p=>p.history.branches[1].result.background=[0,0,0,255],
            p=>p.history.branches[1].options.source={frame:256}];
          const errors=invalid.map(mutate=>{const p=JSON.parse(JSON.stringify(base));mutate(p);
            try {validateProject(p,config);return null;} catch(error) {return error.message;}});
          const mixedCase=JSON.parse(JSON.stringify(base));
          mixedCase.history.branches[1].result.target_digest='A'.repeat(64);
          const digest=validateProject(mixedCase,config).history.branches[1].result.target_digest;
          return {authority,isolated,errors,digest};
        }""")
        assert result["authority"] == {"source": {"frame": 1, "matte": [1, 2, 3]}, "background": [4, 5, 6]}
        assert result["isolated"]
        assert all(result["errors"])
        assert result["digest"] == "a" * 64
        browser.close()


def test_preparation_ownership_metadata_bounds_and_original_byte_preservation(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate(r"""async () => {
          const {SourcePreparation,canonicalImageUrl} = await import('/static/source.js');
          const config = await (await fetch('/api/config')).json();
          const pending=[],requests=[];
          const prep=new SourcePreparation(config,(url,body)=>new Promise((resolve,reject)=>{
            requests.push({url,body});pending.push({resolve,reject});}));
          const preview=document.createElement('canvas');preview.width=16;preview.height=8;
          const reply={source:{frame:1,matte:null},width:1600,height:800,mime_type:'image/gif',
            frame_count:2,frames_truncated:false,default_image:false,frame_duration_ms:50,
            preview_data_url:preview.toDataURL(),preview_width:16,preview_height:8};
          const original='data:image/png;base64,R0lGODlh\r\nAQABAIAAAP///w==';
          const first=prep.prepare(original,{frame:1},prep.begin());
          const second=prep.prepare(original,{frame:1},prep.begin());
          pending[0].resolve({...reply,width:Infinity});
          const stale=await first;pending[1].resolve(reply);const current=await second;
          const mutations=[r=>r.source.frame=0,r=>r.width=0,r=>r.width=config.images.max_dimension+1,
            r=>r.frame_count=1,r=>r.frame_count=257,r=>r.frames_truncated=true,
            r=>r.preview_width=1025,r=>r.frame_duration_ms=-1,r=>r.default_image='no',
            r=>r.preview_data_url=original,r=>r.mime_type='image/svg+xml'];
          const errors=[];
          for(const mutate of mutations) {
            const r=structuredClone(reply);mutate(r);
            const promise=prep.prepare(original,{frame:1},prep.begin());pending.at(-1).resolve(r);
            try {await promise;errors.push(null);} catch(error) {errors.push(error.message);}
          }
          return {stale,current,errors,request:requests[0],canonical:canonicalImageUrl(original,'image/apng')};
        }""")
        assert result["stale"] is None
        assert result["current"]["image"] == "data:image/gif;base64,R0lGODlh\r\nAQABAIAAAP///w=="
        assert result["current"]["width"] == 1600 and result["current"]["preview_width"] == 16
        assert result["request"]["url"] == "/api/source/prepare"
        assert result["request"]["body"]["source"] == {"frame": 1, "matte": None}
        assert all(result["errors"])
        assert result["canonical"].startswith("data:image/apng;base64,R0lGODlh\r\n")
        browser.close()


def test_project_discards_cached_previews_and_preserves_bounded_geometry_serialization(server_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(server_url, wait_until="networkidle")
        result = page.evaluate("""async () => {
          const {projectContent,validateProject} = await import('/static/project.js');
          const config=await (await fetch('/api/config')).json(),options=config.defaults;
          const original='data:image/gif;base64,originalBytes';
          const geometry={width:8,height:8,background:[1,2,3,255],shapes:[],target_digest:'a'.repeat(64),
            preview_data_url:'data:image/png;base64,'+'A'.repeat(3000)};
          const draft={width:null,height:null,background:null,shapes:[],preview_data_url:null};
          const telemetry={attempts:0,duration_ms:0,initial_score:null,batches:[]};
          const project={format:config.project.format,version:config.project.version,options,
            source:{data_url:original},result:geometry,telemetry,history:{active_branch:'a',view_shape_count:0,branches:[
            {id:'a',name:'A',parent_id:null,fork_shape_count:0,options},
            {id:'b',name:'B',parent_id:null,fork_shape_count:0,options,result:geometry,telemetry},
            {id:'c',name:'C',parent_id:null,fork_shape_count:0,options,result:draft,telemetry}]}};
          const normalized=validateProject(project,config);
          const saved=JSON.parse(projectContent(normalized,100000));
          let tooLarge;try {projectContent(project,50);} catch(error) {tooLarge=error.message;}
          return {saved,tooLarge,
            untouched:project.result.preview_data_url.length===3022};
        }""")
        assert result["untouched"]
        assert result["saved"]["source"]["data_url"] == "data:image/gif;base64,originalBytes"
        assert result["saved"]["result"]["target_digest"] == "a" * 64
        assert result["saved"]["result"]["preview_data_url"] is None
        assert result["saved"]["history"]["branches"][1]["result"]["preview_data_url"] is None
        assert result["saved"]["history"]["branches"][2]["result"]["preview_data_url"] is None
        assert "exceeds 64 MB" in result["tooLarge"]
        browser.close()
