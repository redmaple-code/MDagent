import test from 'node:test';import assert from 'node:assert/strict';
import {ViewRuntime} from '../visualization/view-runtime.mjs';
const binding={sessionId:'s',viewportId:'main',sourceSha256:'a'.repeat(64),sourceRevision:1,visualRevision:1,frameRevision:1,cameraRevision:1};
const observation={binding,ready:true,playing:false,buffering:false,sourceFrameIndex:0,timePs:1,selection:[],representation:'cartoon',camera:null,previewFrameIndex:0,previewFrameCount:1,sourceFrameCount:1};
function fixture(){let current;let names=['bio_inputs','bio_view','bio_observe'];const agent={session:{id:'s'},ctx:{tools:{schemas:()=>names.map(name=>({name}))}}};current=agent;const projects={guard:async()=>{},get:async()=>({receiptProject:'/unused'})};
  return {runtime:new ViewRuntime({agents:{get:()=>current}},projects,{toolNames:['bio_inputs'],persist:false,verifySource:false}),replace:()=>current={...agent},revoke:name=>names=names.filter(n=>n!==name),agent};}
test('offline or ambiguous view cannot succeed',async()=>{
  const f=fixture();await assert.rejects(f.runtime.observe('s','main'),/VIEWPORT_OFFLINE/);
  f.runtime.heartbeat('s','main','client-a',observation);f.runtime.heartbeat('s','main','client-b',observation);
  await assert.rejects(f.runtime.observe('s','main'),/VIEWPORT_AMBIGUOUS/);
});
test('source_verification_uses_metadata_only',async()=>{
  const f=fixture();f.runtime.config.verifySource=true;const calls=[];
  const source={kind:'input',inputId:'protein',sourceSha256:'a'.repeat(64)};
  const view={sessionId:'s',metadata:{source,sourceSha256:source.sourceSha256,previewId:'preview-test'},observation};
  f.runtime.projects.get=async()=>({bridge:{control:async(action,args)=>{calls.push({action,args});return {ok:true,result:{sourceSha256:source.sourceSha256}};}}});
  await f.runtime.checkSource(view);assert.deepEqual(calls,[{action:'preview-describe',args:{source}}]);
  f.runtime.projects.get=async()=>({bridge:{control:async()=>({ok:true,result:{sourceSha256:'b'.repeat(64)}})}});
  await assert.rejects(f.runtime.checkSource(view),/PREVIEW_SOURCE_CHANGED/);
});
test('replacement during asynchronous permission check rejects observation',async()=>{
  const f=fixture();f.runtime.heartbeat('s','main','client',observation);
  f.runtime.projects.guard=async()=>f.replace();await assert.rejects(f.runtime.observe('s','main'),/BIO_SESSION_ACCESS_DENIED/);
});
test('client IDs are isolated by session and cannot replace a foreign view',()=>{
  const f=fixture();f.runtime.heartbeat('s','main','shared-id',observation);
  f.runtime.heartbeat('other','main','shared-id',{...observation,binding:{...binding,sessionId:'other'}});
  assert.equal(f.runtime.view('s','main').sessionId,'s');assert.equal(f.runtime.view('other','main').sessionId,'other');
});
test('observe waits for a fresh browser receipt instead of returning a heartbeat',async()=>{
  const f=fixture();f.runtime.heartbeat('s','main','client',observation);let resolved=false;
  const pending=f.runtime.observe('s','main').then(value=>{resolved=true;return value;});await new Promise(r=>setImmediate(r));assert.equal(resolved,false);
  const poll=await f.runtime.dispatch('poll',{sessionId:'s',viewportId:'main',clientId:'client',observation,state:null});
  assert.equal(poll.command.kind,'observe');
  const latest={...observation,timePs:2};await f.runtime.dispatch('ack',{sessionId:'s',viewportId:'main',clientId:'client',receipt:{operationId:poll.command.operationId,observation:latest},state:null});
  assert.equal((await pending).timePs,2);
});
test('revoking display capability denies new and in-flight commands while manual dispatch remains available',async()=>{
  for(const capability of ['bio_view','bio_observe']){
    const f=fixture();f.runtime.heartbeat('s','main','client',observation);
    const pending=capability==='bio_observe'?f.runtime.observe('s','main'):f.runtime.command('s','main',{operationId:crypto.randomUUID(),expected:binding,kind:'pause',payload:{}});
    const rejected=assert.rejects(pending,/BIO_SESSION_ACCESS_DENIED/);await new Promise(r=>setImmediate(r));
    const poll=await f.runtime.dispatch('poll',{sessionId:'s',viewportId:'main',clientId:'client',observation,state:null});f.revoke(capability);
    await f.runtime.dispatch('ack',{sessionId:'s',viewportId:'main',clientId:'client',receipt:{operationId:poll.command.operationId,observation},state:null});await rejected;
    await assert.rejects(f.runtime.guard('s',capability),/BIO_SESSION_ACCESS_DENIED/);
    await f.runtime.dispatch('poll',{sessionId:'s',viewportId:'main',clientId:'client',observation,state:null});
  }
});
