import test from 'node:test';
import assert from 'node:assert/strict';
import {HumanWorkflowApi} from '../human-api.mjs';

const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};
const request={sessionId:'progress-session',workflowId:'md',revision:1};
const snapshot={schemaVersion:'bio.compute-progress.v1',workflowId:'md',revision:1,specSha256:'a'.repeat(64),
  state:'RUNNING',currentStageId:'production',resourceWaiting:false,observedAtEpochMs:100000,
  stages:[{id:'production',kind:'production',state:'RUNNING',attemptId:'attempt',attemptState:'RUNNING',
    totalSteps:1000,progress:{stageStep:400,totalSteps:1000,elapsedSeconds:40,publishedAtEpochMs:100000}}]};

function fixture(){
  let names=['bio_get'],allowed=true;
  const agent={session:{id:request.sessionId},ctx:{tools:{schemas:()=>names.map(name=>({name}))}}};
  const ctx={agents:new Map([[request.sessionId,agent]])};
  const hostEntered=deferred(),hostReply=deferred(),getEntered=deferred();let getReply=null;
  const calls=[];
  const record={project:'/engineering/projects/progress-session',bridge:{control:async(action,args,{signal})=>{
    calls.push({action,args,signal});hostEntered.resolve();return hostReply.promise;
  }}};
  const projects={records:new Map([[request.sessionId,record]]),guard:async action=>{
    assert.equal(action,'human:get');
    if(!allowed)throw new Error('BIO_PLATFORM_ACCESS_DENIED');
  },get:async()=>{getEntered.resolve();return getReply?getReply.promise:record;}};
  return {api:new HumanWorkflowApi(ctx,projects,{tenantId:'tenant',toolNames:['bio_get']}),calls,hostEntered,getEntered,
    finish:()=>hostReply.resolve({ok:true,result:snapshot}),
    revoke:kind=>{
      if(kind==='scope')names=[];
      else if(kind==='session')ctx.agents.delete(request.sessionId);
      else if(kind==='replacement')ctx.agents.set(request.sessionId,{...agent});
      else if(kind==='session-id')agent.session.id='other';
      else if(kind==='platform')allowed=false;
      else if(kind==='record')projects.records.set(request.sessionId,{...record});
      else if(kind==='record-removed')projects.records.delete(request.sessionId);
      else if(kind==='registry')delete projects.records;
      else if(kind==='project')record.project='/engineering/projects/replacement';
      else if(kind==='bridge')record.bridge={...record.bridge};
    },
    holdConnection:()=>{getReply=deferred();return ()=>getReply.resolve(record);}};
}

test('progress requires only scoped read permission and transfers bounded host metadata',async()=>{
  const f=fixture(),pending=f.api.dispatch('progress',{...request});
  await f.hostEntered.promise;f.finish();
  assert.deepEqual(await pending,snapshot);
  assert.deepEqual(f.calls.map(({action,args})=>({action,args})),[{action:'progress',args:{workflowId:'md',revision:1}}]);
});

for(const kind of ['scope','session','replacement','session-id','platform']){
  test(`progress discards a host reply after ${kind} revocation`,async()=>{
    const f=fixture(),pending=f.api.dispatch('progress',{...request});
    await f.hostEntered.promise;f.revoke(kind);f.finish();
    await assert.rejects(pending,kind==='platform'?/BIO_PLATFORM_ACCESS_DENIED/:/BIO_SESSION_ACCESS_DENIED/);
  });
}

for(const kind of ['record','record-removed','registry','project','bridge']){
  test(`progress discards an old reply after its ${kind} binding changes`,async()=>{
    const f=fixture(),pending=f.api.dispatch('progress',{...request});
    await f.hostEntered.promise;f.revoke(kind);f.finish();
    await assert.rejects(pending,/BIO_SESSION_ACCESS_DENIED/);
  });
}

test('progress rechecks platform permission after an asynchronous connection before querying the host',async()=>{
  const f=fixture(),finishConnection=f.holdConnection(),pending=f.api.dispatch('progress',{...request});
  await f.getEntered.promise;f.revoke('platform');finishConnection();f.finish();
  await assert.rejects(pending,/BIO_PLATFORM_ACCESS_DENIED/);
  assert.equal(f.calls.length,0);
});

test('progress discards a host reply when its read was aborted without canceling a scientific task',async()=>{
  const f=fixture(),abort=new AbortController(),pending=f.api.dispatch('progress',{...request},abort.signal);
  await f.hostEntered.promise;abort.abort();f.finish();
  await assert.rejects(pending,/BIO_REQUEST_ABORTED/);
  assert.deepEqual(f.calls.map(call=>call.action),['progress']);
});
