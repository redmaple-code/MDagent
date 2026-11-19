import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {validateViewCommand} from '../visualization/contracts.mjs';import {ViewStateStore} from '../visualization/view-state.mjs';import {validateObservation} from '../visualization/view-runtime.mjs';import {ViewCommandHandler} from '../visualization/view-api.mjs';
const style=await import('../visualization/appearance.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const drawing=await import('../../visualization/src/appearance.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const appearance={colorScheme:'uniform',uniformColor:0x3388cc,material:'glossy'};
const binding={sessionId:'s',viewportId:'main',sourceSha256:'a'.repeat(64),sourceRevision:1,visualRevision:1,frameRevision:1,cameraRevision:1};
const command=(payload)=>({operationId:crypto.randomUUID(),expected:binding,kind:'setRepresentation',payload});
test('appearance accepts bounded color/material choices and rejects malformed settings',()=>{
 assert.equal(typeof style.validateAppearance,'function');
 for(const colorScheme of ['auto','element','chain','residue','uniform'])for(const material of ['matte','glossy','metal'])assert.deepEqual(style.validateAppearance({...appearance,colorScheme,material}),{...appearance,colorScheme,material});
 for(const value of [{...appearance,uniformColor:-1},{...appearance,uniformColor:0x1000000},{...appearance,uniformColor:1.5},{...appearance,colorScheme:'url'},{...appearance,material:'custom'},{...appearance,coordinates:[1,2,3]}])assert.throws(()=>style.validateAppearance(value),/INVALID_VIEW_APPEARANCE/);
 assert.notEqual(style.validateAppearance(appearance),appearance);
});
test('representation commands accept appearance while legacy commands remain valid',()=>{
 assert.equal(validateViewCommand(command({representation:'sticks',appearance})).payload.appearance,appearance);
 assert.doesNotThrow(()=>validateViewCommand(command({representation:'cartoon'})));
 assert.throws(()=>validateViewCommand(command({representation:'sticks',appearance:{...appearance,uniformColor:-1}})),/INVALID_VIEW_COMMAND/);
});
test('saved appearance is metadata-only and older view states remain readable',()=>{
 const store=new ViewStateStore(mkdtempSync(join(tmpdir(),'bio-appearance-state-')));
 const old={schemaVersion:'bio.view-state.v1',sessionId:'s',viewportId:'main',sourceSha256:binding.sourceSha256,source:null,previewRequest:null,previewId:null,timePs:5,sourceFrameIndex:4,representation:'cartoon',selection:[],camera:null};
 store.save('s',old,0);assert.equal(store.load('s').schemaVersion,'bio.view-state.v1');
 const next={...old,schemaVersion:'bio.view-state.v2',appearance};store.save('s',next,1);assert.deepEqual(store.load('s').appearance,appearance);
 assert.throws(()=>store.save('s',{...next,appearance:{...appearance,uniformColor:NaN}},2),/INVALID_VIEW_STATE/);
 assert.throws(()=>store.save('s',{...next,coordinates:[1,2,3]},2),/INVALID_VIEW_STATE/);
});
test('appearance observations are valid without invalidating older heartbeats',()=>{
 const o={binding,ready:true,playing:false,buffering:false,sourceFrameIndex:4,timePs:5,selection:[],representation:'sticks',camera:null,previewFrameIndex:4,previewFrameCount:5,sourceFrameCount:5};
 assert.doesNotThrow(()=>validateObservation(o));assert.doesNotThrow(()=>validateObservation({...o,appearance}));
 assert.throws(()=>validateObservation({...o,appearance:{...appearance,colorScheme:'custom'}}),/INVALID_VIEW_OBSERVATION/);
});
test('style command receipts retain actual source/frame/camera and applied appearance',async()=>{
 const camera={position:[1,2,3],up:[0,1,0],target:[0,0,0]};let applied,kind='cartoon';
 const player={state:{...binding,ready:true,sourceSha256:binding.sourceSha256}};
 const renderer={observe:()=>({ready:true,sourceSha256:binding.sourceSha256,sourceFrameIndex:4,timePs:5,cameraRevision:1,camera,selection:[],representation:kind,appearance:applied}),setRepresentation:async(rep,value)=>{kind=rep;applied=value;}};
 const handler=new ViewCommandHandler({sessionId:'s',viewportId:'main',player,renderer}),before=handler.observe();
 const result=await handler.execute({...command({representation:'sticks',appearance}),expected:before.binding});
 assert.deepEqual(result.observation.appearance,appearance);assert.deepEqual(result.observation.camera,camera);assert.equal(result.observation.sourceFrameIndex,4);assert.equal(result.observation.timePs,5);assert.equal(result.observation.binding.sourceSha256,binding.sourceSha256);
});
function representationFixture(fail=false){
 const cells=['cartoon','ball-and-stick'].map((name,i)=>({ref:'rep'+i,params:{values:{type:{name,params:{quality:'auto',sizeFactor:0.2,material:{metalness:0,roughness:1,bumpiness:0}}},colorTheme:{name:i?'element-symbol':'chain-id',params:{}},sizeTheme:{name:'uniform',params:{value:1}}}}}));
 const entries=cells.map((cell,i)=>({selector:{ref:cell.ref,cell},component:{obj:{data:{elementCount:i?8:100}}},automaticColor:i?'element-symbol':'chain-id'})),pending=[];let options;
 const plugin={state:{data:{build:()=>({to:selector=>({update:params=>{pending.push([selector.cell,params]);}}),commit:async value=>{options=value;if(fail)throw new Error('GPU_UPDATE_FAILED');for(const [cell,params]of pending)cell.params.values=params;}})}},coordinateBytes:new Uint8Array([1,2,3]),camera:{position:[1,2,3]}};
 const theme=(_ctx,_data,_type,name,params)=>({name,params:params||{}});
 return {cells,entries,plugin,theme,options:()=>options};
}
test('multi-component style update changes themes/materials together without geometry or coordinates',async()=>{
 assert.equal(typeof drawing.applyRepresentationAppearance,'function');const f=representationFixture(),before=f.cells.map(c=>structuredClone(c.params.values));
 await drawing.applyRepresentationAppearance(f.plugin,f.entries,appearance,f.theme);
 assert.deepEqual(f.options(),{revertOnError:true});
 for(let i=0;i<f.cells.length;i++){const next=f.cells[i].params.values;assert.equal(next.colorTheme.name,'uniform');assert.equal(next.colorTheme.params.value,appearance.uniformColor);assert.equal(next.type.params.material.roughness,0.2);assert.equal(next.type.name,before[i].type.name);assert.equal(next.type.params.sizeFactor,before[i].type.params.sizeFactor);assert.deepEqual(next.sizeTheme,before[i].sizeTheme);}
 assert.deepEqual([...f.plugin.coordinateBytes],[1,2,3]);assert.deepEqual(f.plugin.camera,{position:[1,2,3]});
});
test('failed style update rejects with rollback enabled and retains the prior representation',async()=>{
 assert.equal(typeof drawing.applyRepresentationAppearance,'function');const f=representationFixture(true),before=f.cells.map(c=>structuredClone(c.params.values));
 await assert.rejects(drawing.applyRepresentationAppearance(f.plugin,f.entries,appearance,f.theme),/GPU_UPDATE_FAILED/);
 assert.deepEqual(f.options(),{revertOnError:true});assert.deepEqual(f.cells.map(c=>c.params.values),before);
});
test('explicit element coloring uses element carbon rather than the old carbon-chain override',()=>{
 assert.equal(typeof style.representationAppearance,'function');const value=style.representationAppearance({...appearance,colorScheme:'element'},'chain-id');
 assert.equal(value.color,'element-symbol');assert.deepEqual(value.colorParams.carbonColor,{name:'element-symbol',params:{}});
 assert.equal(style.representationAppearance({...appearance,colorScheme:'auto'},'chain-id').color,'chain-id');
});
