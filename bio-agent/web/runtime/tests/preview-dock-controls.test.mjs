import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {runInNewContext} from 'node:vm';
import {createPreviewPlayer} from '../../visualization/src/player.mjs';import {ViewCommandHandler} from '../visualization/view-api.mjs';
import {defaultAppearance,validateAppearance} from '../visualization/appearance.mjs';
const presets=await import('../visualization/style-templates.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const source={kind:'input',sourceId:'pdb',sourceSha256:'a'.repeat(64),workflowId:null,revision:null,stageId:null,attemptId:null};
const manifest={previewId:'preview-a',source,atomCount:2,topologyFile:'top',atomMapFile:'map',sourceFrameCount:4,
  frames:[0,1,2,3].map(i=>({sourceFrameIndex:i,segmentId:'seg',segmentFrameIndex:i,timePs:i*10})),
  files:[{fileId:'top',kind:'topology'},{fileId:'map',kind:'atom-map'},{fileId:'seg',kind:'xtc',previewFrameCount:4}]};
function fixture(){
  const nodes=new Map(),node=name=>{if(!nodes.has(name))nodes.set(name,{value:name==='atoms'?'overview':'',attributes:{},classList:{toggle(){}},replaceChildren(){},append(...children){this.children=[...(this.children||[]),...children];},setAttribute(k,v){this.attributes[k]=v;},insertBefore(){},prepend(){},focus(){},scrollIntoView(){}});return nodes.get(name);};
  const element={classList:{toggle(){}},setAttribute(){},querySelector:selector=>node(selector.startsWith('[data-')?selector.slice(6,-1):selector),append(){},insertBefore(){},remove(){}};
  const drawn=[],styles=[];let actual={ready:false,appearance:defaultAppearance(),representation:'cartoon',camera:null,selection:[]},read=async()=>new Uint8Array(48),applyStyle=async()=>{},intercept;
  const renderer={observe:()=>actual,open:async m=>{actual={...actual,ready:true,sourceSha256:m.source.sourceSha256,selection:[]};},setFrame:async(_bytes,index,frame)=>{drawn.push(index);actual={...actual,sourceFrameIndex:frame.sourceFrameIndex,timePs:frame.timePs};},setRepresentation:async(rep,appearance)=>{await applyStyle(rep,appearance);styles.push(appearance);actual={...actual,representation:rep,appearance};},select:async selection=>{actual={...actual,selection};},focus(){},dispose(){}};
  const requests=[],cancellations=[];
  const transport={api:async(action,args)=>{requests.push({action,args});if(intercept){const value=await intercept(action,args);if(value)return value;}return action==='describe'?{sourceFrameCount:4}:{jobId:'job',state:'READY',manifest:{...manifest,source:args?.request?.source??source,...(args?.request?.pbc==='unwrap'?{previewId:'preview-unwrap',processing:{mode:'unwrap'}}:{})}};},getFile:(...args)=>read(...args),setProvider(){},dispose(){}};
  transport.api.cancelOperation=async args=>{cancellations.push(args);};
  const text=readFileSync(new URL('../../visualization/src/dock.mjs',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace('export async function','async function')+'\nresult=createDock;';
  const context={...presets,defaultAppearance,validateAppearance,result:null,AbortController,AbortSignal,crypto,Option:function(){},setTimeout,document:{createElement:()=>element,body:{append(){}}},createMolstarRenderer:async()=>renderer,createPreviewTransport:()=>transport,createPreviewPlayer,createLocalConnectorClient:()=>({pairingId:'test-pair',pair:async()=>{},dispose(){}}),createDirectProvider:()=>({mode:'direct'}),bindFloatingPanel:()=>()=>{},ViewCommandHandler,loadPreviewChoices:async()=>({choices:[],nextOffset:null})};
  context.createComputeProgressPanel=()=>({element:{},setSource:async()=>{},dispose(){}});
  runInNewContext(text,context);return {create:async()=>{const dock=await context.result({sessionId:'s'});await node('connect').onclick();return dock;},node,drawn,styles,requests,cancellations,setApi:fn=>{intercept=fn;},setActual:patch=>{actual={...actual,...patch};},setStyle:fn=>{applyStyle=fn;},setRead:fn=>{read=fn;}};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
test('unwrap and raw buttons preserve the displayed frame, style, selection and camera',async()=>{
  const f=fixture(),dock=await f.create();try{
    assert.equal(f.node('pbc-unwrap').disabled,true);
    await dock.openSource(source);await dock.player.seekTime(20);
    const camera={position:[2,3,4],target:[0,0,0],up:[0,1,0]};f.setActual({camera,selection:[1]});
    const before=dock.handler.observe();
    assert.equal(typeof f.node('pbc-unwrap').onclick,'function');await f.node('pbc-unwrap').onclick();
    assert.equal(dock.player.manifest.processing?.mode,'unwrap');assert.equal(dock.metadata().previewRequest.pbc,'unwrap');
    assert.equal(dock.player.state.timePs,20);assert.deepEqual(dock.handler.observe().camera,camera);assert.deepEqual(dock.handler.observe().selection,[1]);assert.deepEqual(dock.handler.observe().appearance,before.appearance);
    assert.equal(f.node('pbc-unwrap').attributes['aria-pressed'],'true');
    await f.node('pbc-raw').onclick();assert.equal(dock.player.manifest.processing,undefined);assert.equal(dock.player.state.timePs,20);assert.equal(f.node('pbc-raw').attributes['aria-pressed'],'true');
  }finally{dock.close();}
});
test('a failed unwrap keeps the original scene and restores the actual button state',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);await dock.player.seekTime(20);const previous=dock.player.manifest;
    f.setApi(async(action,args)=>{if(action==='prepare'&&args.request.pbc==='unwrap')throw new Error('PBC_PYMOL_UNAVAILABLE');});
    assert.equal(typeof f.node('pbc-unwrap').onclick,'function');await f.node('pbc-unwrap').onclick();
    assert.equal(dock.player.manifest,previous);assert.equal(dock.player.state.timePs,20);assert.equal(f.node('pbc-raw').attributes['aria-pressed'],'true');assert.equal(f.node('pbc-unwrap').disabled,false);assert.match(f.node('note').textContent,/PyMOL/);
  }finally{dock.close();}
});
test('a late unwrap cannot overwrite a newly opened source even if the RPC ignores abort',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();try{
    await dock.openSource(source);f.setApi(async(action,args)=>{if(action==='prepare'&&args.request.pbc==='unwrap'){entered.resolve();return late.promise;}});
    assert.equal(typeof f.node('pbc-unwrap').onclick,'function');const opening=f.node('pbc-unwrap').onclick();await entered.promise;
    const next={...source,sourceId:'next',sourceSha256:'b'.repeat(64)};await dock.openSource(next);
    const operation=f.requests.find(r=>r.action==='prepare'&&r.args.request.pbc==='unwrap').args.operationId;
    assert.match(operation,/^[0-9a-f-]{36}$/);assert.equal(f.cancellations.length,1);assert.equal(f.cancellations[0].operationId,operation);
    late.resolve({jobId:'old',state:'READY',manifest:{...manifest,processing:{mode:'unwrap'}}});await opening;
    assert.equal(dock.player.manifest.source.sourceId,'next');assert.equal(dock.metadata().source.sourceId,'next');assert.equal(dock.player.manifest.processing,undefined);
  }finally{late.resolve();dock.close();}
});
test('closing a panel cancels an unwrap even before prepare returns a job ID',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();
  await dock.openSource(source);f.setApi(async(action,args)=>{if(action==='prepare'&&args.request.pbc==='unwrap'){entered.resolve();return late.promise;}});
  const previous=dock.player.manifest,frames=f.drawn.slice(),opening=f.node('pbc-unwrap').onclick();await entered.promise;dock.close();
  assert.equal(f.cancellations.length,1);assert.ok(f.cancellations[0].operationId);
  late.resolve({jobId:'late',state:'READY',manifest});await opening;assert.equal(dock.player.manifest,previous);assert.deepEqual(f.drawn,frames);
});
test('a failed topology keeps the old scene and time instead of reporting Unwrap',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);await dock.player.seekTime(20);const previous=dock.player.manifest;
    f.setApi(async(action,args)=>action==='prepare'&&args.request.pbc==='unwrap'?{jobId:'bad',state:'FAILED',errorCode:'PBC_TOPOLOGY_REQUIRED'}:null);
    await f.node('pbc-unwrap').onclick();assert.equal(dock.player.manifest,previous);assert.equal(dock.player.state.timePs,20);assert.equal(f.node('pbc-raw').attributes['aria-pressed'],'true');
  }finally{dock.close();}
});
test('one template choice applies geometry, colors and material without rereading or seeking',async()=>{
  const f=fixture(),dock=await f.create();try{
    assert.equal(f.node('style-template').disabled,true);await dock.openSource(source);assert.equal(f.node('style-template').disabled,false);
    const frames=f.drawn.slice();for(const id of ['protein-rainbow','materials-metal','materials-wireframe']){f.node('style-template').value=id;assert.equal(typeof f.node('style-template').onchange,'function');await f.node('style-template').onchange();const wanted=presets.resolveStyleTemplate(id),actual=dock.handler.observe();assert.equal(actual.representation,wanted.representation);assert.deepEqual(actual.appearance,wanted.appearance);assert.equal(f.node('style-template').value,id);assert.equal(actual.binding.sourceSha256,source.sourceSha256);assert.equal(actual.timePs,0);assert.deepEqual(f.drawn,frames);}
  }finally{dock.close();}
});
test('manual refinements select custom and failed template changes restore actual controls',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);f.node('color-scheme').value='residue';await f.node('color-scheme').onchange();assert.equal(f.node('style-template').value,'custom');
    const before=dock.handler.observe();f.setStyle(async()=>{throw new Error('GPU_UPDATE_FAILED');});f.node('style-template').value='materials-metal';await f.node('style-template').onchange();
    assert.equal(f.node('style-template').value,'custom');assert.equal(f.node('representation').value,before.representation);assert.equal(f.node('color-scheme').value,before.appearance.colorScheme);assert.deepEqual(dock.handler.observe().appearance,before.appearance);
  }finally{dock.close();}
});
test('rapid template selection applies the latest requested complete style',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();try{
    await dock.openSource(source);let first=true;f.setStyle(async()=>{if(first){first=false;entered.resolve();await late.promise;}});
    f.node('style-template').value='protein-rainbow';const a=f.node('style-template').onchange();await entered.promise;
    f.node('style-template').value='materials-metal';const b=f.node('style-template').onchange();f.node('style-template').value='materials-wireframe';const c=f.node('style-template').onchange();late.resolve();await Promise.all([a,b,c]);
    assert.equal(f.styles.length,2);assert.equal(dock.handler.observe().representation,'lines');assert.equal(f.node('style-template').value,'materials-wireframe');assert.equal(f.node('material').disabled,true);
  }finally{late.resolve();dock.close();}
});
test('appearance controls become available only after a structure is ready',async()=>{
  const f=fixture(),dock=await f.create();try{
    assert.equal(f.node('color-scheme').disabled,true);assert.equal(f.node('material').disabled,true);
    await dock.openSource(source);assert.equal(f.node('color-scheme').disabled,false);assert.equal(f.node('material').disabled,false);
  }finally{dock.close();}
});
test('fast uniform-color input reaches the latest color without reading another trajectory frame',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();try{
    await dock.openSource(source);const frames=f.drawn.slice();let first=true;f.setStyle(async()=>{if(first){first=false;entered.resolve();await late.promise;}});
    f.node('color-scheme').value='uniform';const a=f.node('color-scheme').onchange();await entered.promise;
    f.node('uniform-color').value='#ff3311';const b=f.node('uniform-color').oninput();f.node('uniform-color').value='#3344cc';const c=f.node('uniform-color').oninput();late.resolve();await Promise.all([a,b,c]);
    assert.equal(dock.handler.observe().appearance.uniformColor,0x3344cc);assert.equal(f.styles.length,2);assert.equal(f.node('uniform-color').value,'#3344cc');assert.deepEqual(f.drawn,frames);assert.equal(dock.player.state.timePs,0);
  }finally{late.resolve();dock.close();}
});
test('failed color update restores its controls and retains the displayed source/frame',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);const frames=f.drawn.slice();f.setStyle(async()=>{throw new Error('GPU_UPDATE_FAILED');});f.node('color-scheme').value='chain';await f.node('color-scheme').onchange();
    assert.equal(f.node('color-scheme').value,'auto');assert.equal(dock.player.state.sourceSha256,source.sourceSha256);assert.equal(dock.player.state.frameIndex,0);assert.deepEqual(f.drawn,frames);assert.match(f.node('note').textContent,/GPU_UPDATE_FAILED/);
  }finally{dock.close();}
});
test('fast time-slider input reaches the latest requested time without a stale frame binding',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();try{
    await dock.openSource(source);f.drawn.length=0;let first=true;
    f.setRead(async()=>{if(first){first=false;entered.resolve();await late.promise;}return new Uint8Array(48);});
    f.node('time').value='1';const a=f.node('time').oninput();await entered.promise;
    f.node('time').value='2';const b=f.node('time').oninput();f.node('time').value='3';const c=f.node('time').oninput();late.resolve();await Promise.all([a,b,c]);
    assert.equal(dock.player.state.timePs,30);assert.equal(f.node('time').value,'3');assert.doesNotMatch(f.node('note').textContent||'',/VIEW_BINDING_CHANGED/);assert.deepEqual(f.drawn,[1,3]);
  }finally{late.resolve();dock.close();}
});
test('a manual command queued before a source replacement cannot seek the replacement source',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();try{
    await dock.openSource(source);let first=true;f.setRead(async()=>{if(first){first=false;entered.resolve();await late.promise;}return new Uint8Array(48);});
    f.node('time').value='1';const a=f.node('time').oninput();await entered.promise;f.node('time').value='3';const b=f.node('time').oninput();
    const replacement=dock.player.open({...manifest,source:{...source,sourceSha256:'b'.repeat(64)}});late.resolve();await Promise.all([a,b,replacement]);
    assert.equal(dock.player.state.sourceSha256,'b'.repeat(64));assert.equal(dock.player.state.frameIndex,0);
  }finally{late.resolve();dock.close();}
});
