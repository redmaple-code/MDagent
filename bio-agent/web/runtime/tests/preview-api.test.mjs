import test from 'node:test';import assert from 'node:assert/strict';
import {PreviewApi} from '../visualization/preview-api.mjs';
const data={sessionId:'session',viewportId:'main',previewId:'preview-abc',fileId:'segment.xtc',offset:0,limit:65536,sourceSha256:'a'.repeat(64)};
const description={sessionId:'session',viewportId:'main',source:{kind:'input',sourceId:'protein',sourceSha256:'a'.repeat(64),workflowId:null,revision:null,stageId:null,attemptId:null}};
function fixture(){
  let scoped=true;const agent={session:{id:'session'},ctx:{tools:{schemas:arg=>{assert.equal(arg,agent);return scoped?[{name:'bio_inputs'}]:[];}}}};
  let finish;const result=new Promise(resolve=>finish=resolve);
  const ctx={agents:{get:()=>agent}};const projects={guard:async()=>{},get:async()=>({bridge:{control:async()=>result}})};
  const api=new PreviewApi(ctx,projects,{toolNames:['bio_inputs']});
  return {api,finish,revoke:()=>scoped=false};
}
test('tenant source and scoped revocation after Bridge rejects late metadata',async()=>{
  const f=fixture();const pending=f.api.dispatch('describe',description);await new Promise(r=>setImmediate(r));
  f.revoke();f.finish({ok:true,result:{sourceSha256:data.sourceSha256,data:'eA=='}});
  await assert.rejects(pending,/BIO_SESSION_ACCESS_DENIED/);
});
test('original Bio scope remains valid without display tools',async()=>{
  const f=fixture();const pending=f.api.dispatch('describe',description);f.finish({ok:true,result:{sourceSha256:data.sourceSha256}});
  assert.equal((await pending).sourceSha256,data.sourceSha256);
});
test('abort rejects before metadata delivery',async()=>{
  const f=fixture(),abort=new AbortController();const pending=f.api.dispatch('describe',description,abort.signal);abort.abort();f.finish({ok:true,result:{sourceSha256:data.sourceSha256}});await assert.rejects(pending,/BIO_REQUEST_ABORTED/);
});
test('legacy_read_permission_cannot_emit_coordinates',async()=>{
  const f=fixture();let calls=0;f.api.projects.get=async()=>{calls++;return {bridge:{control:async()=>({ok:true,result:{sourceSha256:data.sourceSha256,data:'eA=='}})}};};
  await assert.rejects(f.api.dispatch('read',data),/PREVIEW_RELAY_DISABLED/);assert.equal(calls,0);
});
test('owned preview operations are validated and forwarded without losing the cancellation ID',async()=>{
  const f=fixture(),calls=[];f.api.projects.get=async()=>({bridge:{control:async(action,args)=>{calls.push({action,args});return {ok:true,result:{state:'CANCELED'}};}}});
  const operationId='147fd91d-3440-4932-8536-c395960421cb';
  await f.api.dispatch('cancel',{sessionId:'session',viewportId:'main',operationId});
  assert.deepEqual(calls,[{action:'preview-cancel',args:{operationId}}]);
  await assert.rejects(f.api.dispatch('cancel',{sessionId:'session',viewportId:'main',operationId:'../unsafe'}),/INVALID_PREVIEW_OPERATION/);
  await assert.rejects(f.api.dispatch('cancel',{sessionId:'session',viewportId:'main',operationId,jobId:'job-other'}),/INVALID_PREVIEW_REQUEST/);
});
