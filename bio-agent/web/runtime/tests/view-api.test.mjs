import test from 'node:test';import assert from 'node:assert/strict';
import {ViewCommandHandler} from '../visualization/view-api.mjs';
const source='a'.repeat(64);const state={sourceSha256:source,sourceRevision:1,visualRevision:1,frameRevision:1,ready:true,playing:true};
function fixture(){let camera=2;const player={state:{...state},pause(){this.state.playing=false;this.state.frameRevision++;},seekTime:async t=>{player.state.frameRevision++;return {actualTimePs:t};}};
  const renderer={observe:()=>({ready:true,sourceSha256:source,sourceFrameIndex:4,timePs:5,cameraRevision:camera,selection:[],representation:'cartoon',camera:null}),focus(){},setRepresentation:async()=>{},select(){}};
  return {handler:new ViewCommandHandler({sessionId:'s',viewportId:'main',player,renderer}),rotate:()=>camera++,player};}
function command(handler,kind,payload={}){return {operationId:crypto.randomUUID(),expected:handler.observe().binding,kind,payload};}
test('stale source or frame command is rejected',async()=>{
  const f=fixture();const c=command(f.handler,'pause');c.expected.sourceSha256='b'.repeat(64);await assert.rejects(f.handler.execute(c),/VIEW_BINDING_CHANGED/);
  const d=command(f.handler,'pause');d.expected.frameRevision=0;await assert.rejects(f.handler.execute(d),/VIEW_BINDING_CHANGED/);
});
test('automatic playback frame does not block pause',async()=>{
  const f=fixture();const c=command(f.handler,'pause');assert.equal((await f.handler.execute(c)).observation.playing,false);
});
test('seek preserves newer camera but focus needs the camera revision',async()=>{
  const f=fixture();const seek=command(f.handler,'seekTime',{timePs:5}),focus=command(f.handler,'focus');f.rotate();
  assert.equal((await f.handler.execute(seek)).observation.binding.cameraRevision,3);
  await assert.rejects(f.handler.execute(focus),/VIEW_BINDING_CHANGED/);
});
test('manual camera motion is detected from the actual snapshot even without a renderer event',async()=>{
  const f=fixture();const camera={position:[1,2,3],up:[0,1,0],target:[0,0,0]};const observed=f.handler.renderer.observe;
  f.handler.renderer.observe=()=>({...observed(),camera:structuredClone(camera)});
  const focus=command(f.handler,'focus');camera.position[0]=7;
  assert.ok(f.handler.observe().binding.cameraRevision>focus.expected.cameraRevision);
  await assert.rejects(f.handler.execute(focus),/VIEW_BINDING_CHANGED/);
});
