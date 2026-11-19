import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {runInNewContext} from 'node:vm';
import {defaultAppearance,validateAppearance} from '../visualization/appearance.mjs';
import {styleTemplates,resolveStyleTemplate,matchStyleTemplate} from '../visualization/style-templates.mjs';
const catalog=await import('../../visualization/src/source-catalog.mjs').catch(()=>({loadPreviewChoices:async()=>({choices:[],nextOffset:null})}));
const source={kind:'input',sourceId:'pdb',sourceSha256:'a'.repeat(64),workflowId:null,revision:null,stageId:null,attemptId:null};
function fixture(boxOnly=false,humanApi=async()=>({items:[]}),paired=true){
  const nodes=new Map(),requests=[],restored=[],progressSources=[];let notify,pauses=0,progressClosed=false,metadata={ready:true,sourceSha256:source.sourceSha256,boxOnly,representation:'cartoon',selection:[],camera:null};
  const node=name=>{if(!nodes.has(name))nodes.set(name,{value:name==='[data-atoms]'?'overview':name==='[data-start]'?'2':name==='[data-end]'?'3':'',classList:{toggle(){}},replaceChildren(...children){this.children=children;},append(...children){this.children=[...(this.children||[]),...children];},setAttribute(){},insertBefore(){},after(){},prepend(){},focus(){},scrollIntoView(){}});return nodes.get(name);};
  const element={classList:{toggle(){}},setAttribute(){},querySelector:selector=>node(selector),remove(){},append(){},insertBefore(){}};
  // Use the real dock behavior with a finite renderer and transport boundary; no WebGL emulation.
  const transport={api:async(action,args)=>{requests.push({action,args});if(action==='describe')return {source,sourceFrameCount:20,overviewReason:boxOnly?'WATER_BOX_ONLY':null,boxVectorsAngstrom:[[30,0,0],[0,30,0],[0,0,30]]};return {jobId:'job',state:'READY',manifest:{previewId:'preview-'+(action==='window'?'b':'c').repeat(64),source,sourceFrameCount:20,frames:[{timePs:0},{timePs:19}]}};},setProvider(){},dispose(){}};
  const renderer={observe:()=>metadata,focus(){},dispose(){},setRepresentation:async(representation,appearance)=>{if(boxOnly)throw new Error('PREVIEW_BOX_ONLY');metadata={...metadata,representation,appearance};},select(){if(boxOnly)throw new Error('PREVIEW_BOX_ONLY');},restoreCamera:camera=>restored.push(camera)};
  const player={manifest:null,state:{},pause(){pauses++;},dispose(){},open:async m=>{player.manifest=m;metadata.sourceSha256=m.source.sourceSha256;metadata.boxOnly=false;},openBox:async()=>{player.manifest=null;metadata.boxOnly=true;},seekTime:async t=>{metadata.timePs=t;}};
  class Handler{constructor({openSource}){this.openSource=openSource;}execute(command){return this.openSource(command.payload.source);}observe(){return {ready:true,binding:{sourceSha256:metadata.sourceSha256},timePs:metadata.timePs||0,sourceFrameIndex:0,representation:'cartoon',selection:[],camera:null};}}
  const text=readFileSync(new URL('../../visualization/src/dock.mjs',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace('export async function','async function')+'\nresult=createDock;';
  const context={...catalog,styleTemplates,resolveStyleTemplate,matchStyleTemplate,defaultAppearance,validateAppearance,result:null,AbortController,AbortSignal,document:{createElement:tag=>tag==='optgroup'?{children:[],append(child){this.children.push(child);}}:element,body:{append(){}}},createMolstarRenderer:async()=>renderer,createPreviewPlayer:({onState})=>{notify=onState;return player;},createPreviewTransport:()=>transport,createLocalConnectorClient:()=>({pairingId:'test-pair',pair:async()=>{},dispose(){}}),createDirectProvider:()=>({mode:'direct'}),bindFloatingPanel:()=>()=>{},ViewCommandHandler:Handler,crypto,Option:function(text,value){this.text=text;this.value=value;},setTimeout};
  context.createComputeProgressPanel=()=>({element:{},setSource:value=>{progressSources.push(value);return Promise.resolve();},dispose:()=>{progressClosed=true;}});
  runInNewContext(text,context);return {transport,create:async()=>{const dock=await context.result({sessionId:'s',humanApi});if(paired)await node('[data-connect]').onclick();return dock;},node:name=>node('[data-'+name+']'),requests,restored,progressSources,progressClosed:()=>progressClosed,pauses:()=>pauses,emit:state=>notify(state)};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
test('progress follows only the displayed source and is disposed with the dock',async()=>{
  const f=fixture(),dock=await f.create();
  await dock.openSource(source);assert.equal(f.progressSources.at(-1)?.sourceId,'pdb');
  const accepted={...source,kind:'accepted',sourceId:'result',workflowId:'md',revision:1,stageId:'prod',attemptId:'attempt'};
  await dock.openSource(accepted);assert.equal(f.progressSources.at(-1)?.workflowId,'md');
  f.transport.api=async()=>{throw new Error('SSH_NETWORK_UNAVAILABLE');};
  await assert.rejects(dock.openSource({...source,sourceId:'failed'}),/SSH_NETWORK_UNAVAILABLE/);
  assert.equal(f.progressSources.at(-1)?.sourceId,'result');assert.equal(dock.metadata().source.sourceId,'result');
  dock.close();assert.equal(f.progressClosed(),true);
});
test('saved v2 appearance restores color, material and picker controls',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);const appearance={colorScheme:'uniform',uniformColor:0xaabbcc,material:'metal'};
    await dock.restore({...dock.metadata(),appearance,representation:'spheres'});
    assert.equal(f.node('color-scheme').value,'uniform');assert.equal(f.node('material').value,'metal');assert.equal(f.node('uniform-color').value,'#aabbcc');assert.equal(f.node('uniform-color').hidden,false);
  }finally{dock.close();}
});
test('restored template properties select the matching template without separate saved IDs',async()=>{
  const f=fixture(),dock=await f.create();try{
    await dock.openSource(source);const template=resolveStyleTemplate('materials-metal');await dock.restore({...dock.metadata(),...template});
    assert.equal(f.node('style-template').value,'materials-metal');assert.equal(f.node('representation').value,'spheres');assert.equal(f.node('uniform-color').value,'#b0becf');
    const old={...dock.metadata(),representation:'cartoon'};delete old.appearance;await dock.restore(old);assert.equal(f.node('style-template').value,'protein-classic');
  }finally{dock.close();}
});
const nextSource={...source,sourceId:'next',sourceSha256:'b'.repeat(64)};
const jobFor=(source,profile='overview')=>({jobId:'job',state:'READY',manifest:{previewId:profile+source.sourceId,source,sourceFrameCount:20,frames:[{timePs:0},{timePs:19}]}});
test('a late detail window cannot replace a newly opened source or request',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();
  f.transport.api=async(action,args)=>{if(action==='describe')return {source:args.source,sourceFrameCount:20};if(action==='window'){entered.resolve();return late.promise;}return jobFor(args.request.source);};
  await dock.openSource(source);const opening=f.node('detail').onclick();await entered.promise;
  await dock.openSource(nextSource);late.resolve(jobFor(source,'detail'));await opening;
  assert.equal(dock.player.manifest.source.sourceId,'next');assert.equal(dock.metadata().source.sourceId,'next');assert.equal(dock.metadata().previewRequest.source.sourceId,'next');assert.equal(dock.metadata().previewRequest.profile,'overview');dock.close();
});
test('a late restore cannot replace a newly opened source',async()=>{
  const f=fixture(),dock=await f.create(),entered=deferred(),late=deferred();
  await dock.openSource(source);const state=dock.metadata();
  f.transport.api=async(action,args)=>{if(action==='describe')return {source:args.source,sourceFrameCount:20};if(args.request.source.sourceId===source.sourceId){entered.resolve();return late.promise;}return jobFor(args.request.source);};
  const restoring=dock.restore({...state,timePs:19});await entered.promise;await dock.openSource(nextSource);late.resolve(jobFor(source));await restoring;
  assert.equal(dock.player.manifest.source.sourceId,'next');assert.equal(dock.metadata().source.sourceId,'next');assert.equal(dock.metadata().previewRequest.source.sourceId,'next');assert.doesNotMatch(f.node('note').textContent,/已恢复|BIO_REQUEST_ABORTED/);dock.close();
});
test('changing phase rejects an old artifact result even when the human RPC ignores cancellation',async()=>{
  const entered=deferred(),late=deferred();let hold=false;
  const workflow={workflowId:'md',revision:1,state:'COMPLETED',stages:['a','b'].map(id=>({id,state:'COMPLETED'})),attempts:['a','b'].map(id=>({stageId:id,attemptId:id,state:'COMPLETED'}))};
  const artifact=id=>({attemptId:id,sourceRef:{kind:'accepted',sourceId:'result-'+id,sourceSha256:(id==='a'?'a':'b').repeat(64),workflowId:'md',revision:1,stageId:id,attemptId:id},items:[{role:'topology',path:id+'.pdb'}],nextOffset:null});
  const humanApi=async(action,args)=>{if(action==='inputs')return {items:[]};if(action==='list')return {items:[workflow],nextOffset:null};if(action==='get')return workflow;if(hold&&args.stageId==='a'){entered.resolve();return late.promise;}return artifact(args.stageId);};
  const f=fixture(false,humanApi),dock=await f.create();f.node('source').value='workflow:md:1';await f.node('source').onchange();
  f.node('stage').value='a';hold=true;const opening=f.node('open').onclick();await entered.promise;f.node('stage').value='b';f.node('stage').onchange();late.resolve(artifact('a'));await opening;
  assert.equal(f.requests.filter(r=>['describe','prepare'].includes(r.action)).length,0);assert.equal(dock.player.manifest,null);assert.doesNotMatch(f.node('note').textContent||'',/BIO_REQUEST_ABORTED/);dock.close();
});
for(const failOpen of [false,true])test('the current source caption survives '+(failOpen?'a failed replacement':'a pending selection'),async()=>{
  const inputs=[{id:'pdb',role:'structure',path:'a.pdb',sha256:source.sourceSha256},{id:'next',role:'structure',path:'b.pdb',sha256:nextSource.sourceSha256}];
  const f=fixture(false,async action=>({items:action==='inputs'?inputs:[],nextOffset:null})),dock=await f.create();
  f.node('source').value=`input:pdb:${source.sourceSha256}:a.pdb`;await f.node('source').onchange();await f.node('open').onclick();const caption=f.node('source-info').textContent;
  f.node('source').value=`input:next:${nextSource.sourceSha256}:b.pdb`;await f.node('source').onchange();
  if(failOpen){f.transport.api=async()=>{throw new Error('SSH_NETWORK_UNAVAILABLE');};await f.node('open').onclick();}
  assert.equal(f.node('source-info').textContent,caption);assert.equal(dock.metadata().source.sourceSha256,source.sourceSha256);assert.equal(dock.player.manifest.source.sourceSha256,source.sourceSha256);dock.close();
});
test('late_open_cannot_replace_selected_source',async()=>{
  const f=fixture(),dock=await f.create();let release;const first=new Promise(r=>release=r);const next={...source,sourceId:'next',sourceSha256:'b'.repeat(64)};
  f.transport.api=async(action,args)=>action==='describe'?(args.source===source?first:{source:next,sourceFrameCount:1}):{jobId:'job',state:'READY',manifest:{previewId:'preview',source:args.request.source,sourceFrameCount:1,frames:[{timePs:0}]}};
  const late=dock.openSource(source);const rejected=assert.rejects(late,/BIO_REQUEST_ABORTED/);await dock.openSource(next);release({source,sourceFrameCount:1});await rejected;
  assert.equal(dock.metadata().source.sourceId,'next');assert.equal(dock.player.manifest.source.sourceId,'next');dock.close();
});
test('overview after a detail window persists and restores the overview request and true time',async()=>{
  const f=fixture(),dock=await f.create();await dock.openSource(source);await f.node('detail').onclick();assert.equal(dock.metadata().previewRequest.profile,'detail');await f.node('overview').onclick();
  const state=dock.metadata();assert.equal(state.previewRequest.profile,'overview');assert.equal(state.previewRequest.endFrame,19);
  await dock.restore({...state,timePs:19});assert.equal(f.requests.at(-1).args.request.profile,'overview');dock.close();
});
test('periodic water box restore skips atom representation and restores the camera',async()=>{
  const f=fixture(true),dock=await f.create(),camera={position:[1,2,3]};
  await dock.restore({source,previewRequest:null,representation:'cartoon',selection:[],camera,timePs:0});
  assert.equal(f.restored.length,1);assert.deepEqual(f.restored[0],camera);assert.doesNotMatch(f.node('note').textContent,/未完成/);dock.close();
});
test('restored representation and atom-set controls describe the actual restored view',async()=>{
  const f=fixture(),dock=await f.create();await dock.openSource(source);const state=dock.metadata();
  await dock.restore({...state,representation:'sticks',previewRequest:{...state.previewRequest,atomSet:'solute-heavy'}});
  assert.equal(f.node('representation').value,'sticks');assert.equal(f.node('atoms').value,'solute-heavy');dock.close();
});
test('water box exposes camera controls but cannot launch an atom representation or trajectory window',async()=>{
  const f=fixture(true),dock=await f.create();await dock.openSource(source);f.emit({ready:true,frameIndex:0,sourceFrameNumber:1,timePs:1.25,buffering:false});
  assert.equal(f.node('detail').disabled,true);assert.equal(f.node('representation').disabled,true);assert.equal(f.node('style-template').disabled,true);assert.equal(f.node('time').disabled,true);dock.close();
});
const inputA={id:'pdb',role:'structure',path:'a.pdb',sha256:source.sourceSha256},inputB={id:'next',role:'structure',path:'b.pdb',sha256:nextSource.sourceSha256};
const choiceValues=f=>(f.node('source').children||[]).flatMap(c=>c.children||[c]).map(c=>c.value);
test('refresh adds registered structures without pausing or replacing the displayed view',async()=>{
  let inputs=[inputA];const f=fixture(false,async action=>({items:action==='inputs'?inputs:[],nextOffset:null})),dock=await f.create();
  try{
    f.node('source').value=`input:pdb:${source.sourceSha256}:a.pdb`;await f.node('source').onchange();await f.node('open').onclick();await dock.player.seekTime(19);dock.player.state.playing=true;
    const before=dock.metadata(),caption=f.node('source-info').textContent,manifest=dock.player.manifest,pauses=f.pauses(),reads=f.requests.length;
    inputs=[inputA,inputB];assert.equal(typeof f.node('refresh').onclick,'function','a refresh action must be available');await f.node('refresh').onclick();
    assert.ok(choiceValues(f).includes(`input:next:${nextSource.sourceSha256}:b.pdb`));assert.deepEqual(dock.metadata(),before);assert.equal(dock.player.manifest,manifest);assert.equal(f.node('source-info').textContent,caption);assert.equal(f.pauses(),pauses);assert.equal(f.requests.length,reads);assert.equal(dock.player.state.playing,true);assert.equal(f.node('open').disabled,false);
    f.node('source').value=`input:next:${nextSource.sourceSha256}:b.pdb`;await f.node('source').onchange();await f.node('open').onclick();
    assert.equal(f.requests.at(-1).args.request.source.sourceSha256,nextSource.sourceSha256);
  }finally{dock.close();}
});
test('refresh clears a retired selection but keeps its already displayed structure',async()=>{
  let inputs=[inputA];const f=fixture(false,async action=>({items:action==='inputs'?inputs:[],nextOffset:null})),dock=await f.create();
  try{
    f.node('source').value=`input:pdb:${source.sourceSha256}:a.pdb`;await f.node('source').onchange();await f.node('open').onclick();const caption=f.node('source-info').textContent;
    inputs=[inputB];assert.equal(typeof f.node('refresh').onclick,'function');await f.node('refresh').onclick();
    assert.equal(f.node('source').value,'');assert.equal(f.node('open').disabled,true);assert.equal(f.node('source-info').textContent,caption);assert.equal(dock.player.manifest.source.sourceSha256,source.sourceSha256);
    await f.node('open').onclick();assert.match(f.node('note').textContent,/PREVIEW_SOURCE_REQUIRED/);
  }finally{dock.close();}
});
test('refresh revalidates completed stages while retaining a still eligible stage',async()=>{
  let ids=['a','b'];const workflow=()=>({workflowId:'md',revision:1,state:'COMPLETED',stages:ids.map(id=>({id,state:'COMPLETED'})),attempts:ids.map(id=>({stageId:id,attemptId:id,state:'COMPLETED'}))});
  const humanApi=async(action,args)=>action==='inputs'?{items:[]}:action==='list'?{items:[workflow()],nextOffset:null}:action==='get'?workflow():{attemptId:args.stageId,sourceRef:{kind:'accepted',sourceId:'result-'+args.stageId,sourceSha256:'a'.repeat(64),workflowId:'md',revision:1,stageId:args.stageId,attemptId:args.stageId},items:[{role:'topology',path:args.stageId+'.pdb'}],nextOffset:null};
  const f=fixture(false,humanApi),dock=await f.create();try{
    f.node('source').value='workflow:md:1';await f.node('source').onchange();f.node('stage').value='b';f.node('stage').onchange();
    assert.equal(typeof f.node('refresh').onclick,'function');await f.node('refresh').onclick();assert.equal(f.node('stage').value,'b');assert.equal(f.node('open').disabled,false);
    ids=['a'];await f.node('refresh').onclick();assert.equal(f.node('stage').value,'a');assert.equal(f.node('open').disabled,false);
    ids=[];await f.node('refresh').onclick();assert.equal(f.node('open').disabled,true);
  }finally{dock.close();}
});
test('a late next page cannot restore entries discarded by a catalog refresh',async()=>{
  const entered=deferred(),late=deferred();let renewed=false;
  const humanApi=async(action,args)=>{if(action==='inputs')return {items:[renewed?inputB:inputA]};if(args.offset){entered.resolve();return late.promise;}return {items:[],nextOffset:renewed?null:1};};
  const f=fixture(false,humanApi),dock=await f.create();try{
    const oldPage=f.node('more').onclick();await entered.promise;renewed=true;assert.equal(typeof f.node('refresh').onclick,'function');await f.node('refresh').onclick();
    late.resolve({items:[{workflowId:'retired',revision:1,state:'COMPLETED'}],nextOffset:null});await oldPage;
    assert.ok(choiceValues(f).includes(`input:next:${nextSource.sourceSha256}:b.pdb`));assert.ok(!choiceValues(f).includes('workflow:retired:1'));assert.equal(f.node('more').hidden,true);assert.equal(f.node('refresh').disabled,false);
  }finally{late.resolve({items:[],nextOffset:null});dock.close();}
});
test('a failed refresh leaves the current source and retry action usable',async()=>{
  let failed=false;const f=fixture(false,async action=>{if(failed)throw new Error('SSH_NETWORK_UNAVAILABLE');return {items:action==='inputs'?[inputA]:[],nextOffset:null};}),dock=await f.create();
  try{
    f.node('source').value=`input:pdb:${source.sourceSha256}:a.pdb`;await f.node('source').onchange();await f.node('open').onclick();const before=dock.metadata();failed=true;
    assert.equal(typeof f.node('refresh').onclick,'function');await f.node('refresh').onclick();assert.deepEqual(dock.metadata(),before);assert.equal(f.node('refresh').disabled,false);assert.equal(f.node('source').disabled,false);assert.match(f.node('note').textContent,/超算连接已断开/);
    failed=false;await f.node('refresh').onclick();assert.equal(f.node('open').disabled,false);
  }finally{dock.close();}
});
test('unpaired Open explains the required connection before describing or deriving a preview',async()=>{
  const f=fixture(false,async action=>({items:action==='inputs'?[inputA]:[],nextOffset:null}),false),dock=await f.create();try{
    f.node('source').value=`input:pdb:${source.sourceSha256}:a.pdb`;await f.node('source').onchange();await f.node('open').onclick();
    assert.equal(f.requests.length,0);assert.equal(dock.player.manifest,null);assert.match(f.node('note').textContent,/连接本地/);
  }finally{dock.close();}
});
