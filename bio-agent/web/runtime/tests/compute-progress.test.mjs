import test from 'node:test';
import assert from 'node:assert/strict';
import {HumanWorkflowApi} from '../human-api.mjs';
const progress=await import('../../visualization/src/compute-progress.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const snapshot=(step=400,extra={})=>({schemaVersion:'bio.compute-progress.v1',workflowId:'md',revision:1,specSha256:'a'.repeat(64),state:'RUNNING',resourceWaiting:false,observedAtEpochMs:100000,
  stages:[{id:'prepare',kind:'prepare',state:'COMPLETED',attemptId:'a1',attemptState:'COMPLETED',totalSteps:null,progress:null},
    {id:'production',kind:'production',state:'RUNNING',attemptId:'a2',attemptState:'RUNNING',totalSteps:1000,progress:{stageStep:step,totalSteps:1000,elapsedSeconds:40,publishedAtEpochMs:100000}}],...extra});
const binding={workflowId:'md',revision:1,viewStageId:'prepare'};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function clock(){let time=100000,id=0;const timers=new Map();return {now:()=>time,schedule:(fn,delay)=>{const key=++id;timers.set(key,{fn,delay});return key;},cancel:key=>timers.delete(key),timers,advance:ms=>time+=ms,async fire(){const [key,item]=timers.entries().next().value||[];if(item){timers.delete(key);await item.fn();}}};}

test('progress human facade preserves session scope and existing read permission',async()=>{
  const calls=[],agent={session:{id:'s'},ctx:{tools:{schemas:()=>[{name:'bio_get'}]}}};
  const ctx={agents:new Map([['s',agent]])};
  const record={project:'/scientific/project',bridge:{control:async(action,args)=>({ok:true,result:{action,args}})}};
  const projects={records:new Map([['s',record]]),guard:async(...args)=>calls.push(args.slice(0,3)),get:async()=>record};
  const api=new HumanWorkflowApi(ctx,projects,{tenantId:'t',toolNames:['bio_get']});
  const value=await api.dispatch('progress',{sessionId:'s',workflowId:'md',revision:1});
  assert.deepEqual(value,{action:'progress',args:{workflowId:'md',revision:1}});
  assert.deepEqual(calls,Array.from({length:3},()=>['human:get','s',{workflowId:'md',revision:1}]));
  await assert.rejects(api.dispatch('progress',{sessionId:'other',workflowId:'md',revision:1}),/BIO_SESSION_ACCESS_DENIED/);
  await assert.rejects(api.dispatch('progress',{sessionId:'s',workflowId:'md',revision:1,path:'/log'}),/BIO_HUMAN_REQUEST_INVALID/);
  projects.guard=async()=>{ctx.agents.delete('s');};
  await assert.rejects(api.dispatch('progress',{sessionId:'s',workflowId:'md',revision:1}),/BIO_SESSION_ACCESS_DENIED/);
});

test('stage progress is independent of accepted-stage count and viewed source stage',()=>{
  assert.equal(typeof progress.describeProgress,'function','stage progress must be available');
  const view=progress.describeProgress(snapshot(),binding);
  assert.equal(view.percent,40);assert.equal(view.completedStages,1);assert.equal(view.totalStages,2);
  assert.equal(view.stageId,'production');assert.equal(view.stageIndex,2);assert.equal(view.viewStageId,'prepare');
  assert.equal(view.status,'running');assert.equal(view.step,400);assert.equal(view.total,1000);
});
test('full MD steps wait for acceptance while failed steps keep the actual percentage',()=>{
  assert.equal(typeof progress.describeProgress,'function');
  assert.equal(progress.describeProgress(snapshot(1000),binding).status,'validating');
  const failed=snapshot(400,{state:'FAILED'});failed.stages[1].state=failed.stages[1].attemptState='FAILED';
  assert.equal(progress.describeProgress(failed,binding).status,'failed');assert.equal(progress.describeProgress(failed,binding).percent,40);
  const complete=snapshot(1000,{state:'COMPLETED'});complete.stages[1].state=complete.stages[1].attemptState='COMPLETED';
  assert.equal(progress.describeProgress(complete,binding).status,'completed');
});
test('unknown totals and resource queues never fabricate a percent or ETA',()=>{
  assert.equal(typeof progress.describeProgress,'function');
  const value=snapshot();value.stages[1]={id:'min',kind:'minimize',state:'RUNNING',attemptId:'min1',attemptState:'RUNNING',totalSteps:null,progress:null};
  assert.equal(progress.describeProgress(value,binding).percent,null);
  value.state='WAITING';value.resourceWaiting=true;value.stages[1].state='PENDING';value.stages[1].attemptId=null;
  assert.equal(progress.describeProgress(value,binding).status,'queued');assert.equal(progress.describeProgress(value,binding).percent,null);
});
test('recoverable workflow pause takes precedence over its failed worker attempt',()=>{
  const value=snapshot(400,{state:'PAUSED'});value.stages[1].state=value.stages[1].attemptState='FAILED';
  const view=progress.describeProgress(value,binding);assert.equal(view.status,'paused');assert.equal(view.percent,40);
});
test('the authoritative runnable stage takes precedence over protocol display order',()=>{
  const value=snapshot(400,{state:'RUNNING',resourceWaiting:true,currentStageId:'min'});
  value.stages=[{...value.stages[1],state:'PENDING',attemptState:null,attemptId:null,progress:null},value.stages[0],{id:'min',kind:'minimize',state:'PENDING',attemptState:null,attemptId:null,totalSteps:null,progress:null}];
  const view=progress.describeProgress(value,binding);assert.equal(view.stageId,'min');assert.equal(view.status,'queued');assert.equal(view.percent,null);
});
test('snapshot scope, bounds and revision are verified before rendering',()=>{
  assert.equal(typeof progress.describeProgress,'function');
  for(const value of [snapshot(1001),snapshot(-1),snapshot(2,{revision:2}),snapshot(2,{workflowId:'other'})])assert.throws(()=>progress.describeProgress(value,binding),/PROGRESS_(INVALID|SOURCE_CHANGED)/);
});
test('polling coalesces refresh, retains last valid progress on loss and stops on close',async()=>{
  assert.equal(typeof progress.createComputeProgress,'function');
  const timer=clock(),late=deferred(),changes=[];let calls=0;
  const monitor=progress.createComputeProgress({...timer,onChange:value=>changes.push(value),humanApi:async(action,args,signal)=>{assert.equal(action,'progress');assert.deepEqual(args,{workflowId:'md',revision:1});calls++;if(calls===2)throw new Error('SSH_NETWORK_UNAVAILABLE');if(calls===3)return late.promise;return snapshot();}});
  await monitor.bind(binding);assert.equal(monitor.state.view.percent,40);assert.equal(timer.timers.size,1);
  await timer.fire();assert.equal(monitor.state.view.percent,40);assert.equal(monitor.state.error,'SSH_NETWORK_UNAVAILABLE');
  const pending=monitor.refresh(),same=monitor.refresh();assert.equal(calls,3);monitor.dispose();const count=changes.length;
  late.resolve(snapshot(500));await Promise.all([pending,same]);assert.equal(changes.length,count);assert.equal(timer.timers.size,0);
});
test('late old task responses and previous-attempt observations cannot contaminate a replacement',async()=>{
  assert.equal(typeof progress.createComputeProgress,'function');
  const timer=clock(),late=deferred();let calls=0;
  const monitor=progress.createComputeProgress({...timer,humanApi:async(_action,args)=>{calls++;return args.workflowId==='md'?late.promise:snapshot(200,{workflowId:'other'});}});
  const pending=monitor.bind(binding);await monitor.bind({workflowId:'other',revision:1});late.resolve(snapshot());await pending;
  assert.equal(monitor.state.binding.workflowId,'other');assert.equal(monitor.state.view.percent,20);
  monitor.dispose();assert.equal(timer.timers.size,0);
});
test('ETA needs stable advancing worker observations and resets when an attempt changes',async()=>{
  assert.equal(typeof progress.createComputeProgress,'function');
  const timer=clock();let index=0;
  const monitor=progress.createComputeProgress({...timer,humanApi:async()=>{const value=snapshot(400+index*100);value.observedAtEpochMs+=index*10000;value.stages[1].progress.publishedAtEpochMs+=index*10000;value.stages[1].progress.elapsedSeconds+=index*10;if(index===4)value.stages[1].attemptId='retry';index++;return value;}});
  await monitor.bind(binding);assert.equal(monitor.state.etaSeconds,null);
  await monitor.refresh();await monitor.refresh();assert.equal(monitor.state.etaSeconds,null);
  await monitor.refresh();assert.equal(monitor.state.etaSeconds,30);
  await monitor.refresh();assert.equal(monitor.state.etaSeconds,null);monitor.dispose();
});
test('successful metadata polls do not keep an ETA based on a stale worker observation',async()=>{
  const timer=clock();let index=0;
  const monitor=progress.createComputeProgress({...timer,humanApi:async()=>{const n=Math.min(index++,3),value=snapshot(400+n*100);value.observedAtEpochMs=timer.now();value.stages[1].progress.publishedAtEpochMs+=n*10000;value.stages[1].progress.elapsedSeconds+=n*10;return value;}});
  try{
    await monitor.bind(binding);for(let i=0;i<3;i++){timer.advance(10000);await monitor.refresh();}
    assert.equal(monitor.state.etaSeconds,30);timer.advance(600000);await monitor.refresh();assert.equal(monitor.state.etaSeconds,null);
  }finally{monitor.dispose();}
});
test('ETA expires while the next progress read remains pending',async()=>{
  const timer=clock(),late=deferred(),changes=[];let index=0;
  const monitor=progress.createComputeProgress({...timer,onChange:state=>changes.push(state),humanApi:async()=>{
    if(index===4)return late.promise;
    const n=index++,value=snapshot(400+n*100);value.observedAtEpochMs=timer.now();
    value.stages[1].progress.publishedAtEpochMs+=n*10000;value.stages[1].progress.elapsedSeconds+=n*10;return value;
  }});
  let waiting;
  try{
    await monitor.bind(binding);for(let i=0;i<3;i++){timer.advance(10000);await monitor.refresh();}
    assert.equal(monitor.state.etaSeconds,30);
    waiting=monitor.refresh();timer.advance(30001);await timer.fire();
    assert.equal(monitor.state.etaSeconds,null,'an unfinished read must not keep an expired ETA visible');
    assert.equal(changes.at(-1).etaSeconds,null);
    assert.equal(monitor.state.view.percent,70,'the last actual step count remains available');
  }finally{monitor.dispose();late.resolve(snapshot(800));await waiting;}
});
test('source changes and external cancellation clear ETA timers and ignore pending old replies',async()=>{
  for(const action of ['replace','unbind','abort','dispose']){
    const timer=clock(),signal=new AbortController(),late=deferred(),changes=[];let index=0,oldSignal;
    const monitor=progress.createComputeProgress({...timer,signal:signal.signal,onChange:state=>changes.push(state),humanApi:async(_action,args,requestSignal)=>{
      if(args.workflowId==='other')return snapshot(200,{workflowId:'other'});
      if(index===4){oldSignal=requestSignal;return late.promise;}
      const n=index++,value=snapshot(400+n*100);value.observedAtEpochMs=timer.now();
      value.stages[1].progress.publishedAtEpochMs+=n*10000;value.stages[1].progress.elapsedSeconds+=n*10;return value;
    }});
    let waiting;
    try{
      await monitor.bind(binding);for(let i=0;i<3;i++){timer.advance(10000);await monitor.refresh();}
      assert.equal(monitor.state.etaSeconds,30);waiting=monitor.refresh();
      if(action==='replace')await monitor.bind({workflowId:'other',revision:1});
      else if(action==='unbind')await monitor.bind(null);
      else if(action==='abort')signal.abort();
      else monitor.dispose();
      assert.equal(oldSignal.aborted,true);assert.equal(timer.timers.size,action==='replace'?1:0);
      const expected=monitor.state,count=changes.length;
      late.resolve(snapshot(900));await waiting;
      assert.equal(monitor.state,expected);assert.equal(changes.length,count);
      if(action==='replace')assert.equal(monitor.state.view.percent,20);
    }finally{monitor.dispose();late.resolve(snapshot(900));await waiting;assert.equal(timer.timers.size,0);}
  }
});
test('accepted terminal completion cancels an established ETA and all polling',async()=>{
  const timer=clock();let index=0;
  const monitor=progress.createComputeProgress({...timer,humanApi:async()=>{
    const n=index++,value=snapshot(n===4?1000:400+n*100);value.observedAtEpochMs=timer.now();
    value.stages[1].progress.publishedAtEpochMs+=n*10000;value.stages[1].progress.elapsedSeconds+=n*10;
    if(n===4){value.state='COMPLETED';value.stages[1].state=value.stages[1].attemptState='COMPLETED';}
    return value;
  }});
  try{
    await monitor.bind(binding);for(let i=0;i<3;i++){timer.advance(10000);await monitor.refresh();}
    assert.equal(monitor.state.etaSeconds,30);timer.advance(10000);await monitor.refresh();
    assert.equal(monitor.state.view.status,'completed');assert.equal(monitor.state.etaSeconds,null);assert.equal(timer.timers.size,0);
  }finally{monitor.dispose();}
});
test('input structures require explicit association; accepted sources bind their exact workflow',async()=>{
  assert.equal(typeof progress.createComputeProgress,'function');
  const timer=clock();let calls=0;
  const monitor=progress.createComputeProgress({...timer,humanApi:async()=>{calls++;return snapshot();}});
  await monitor.setSource({kind:'input',sourceId:'md.pdb'});assert.equal(calls,0);assert.equal(monitor.state.binding,null);
  await monitor.bind(binding);assert.equal(calls,1);
  await monitor.setSource({kind:'accepted',workflowId:'md',revision:1,stageId:'prepare'});assert.equal(monitor.state.binding.viewStageId,'prepare');
  await monitor.setSource({kind:'input',sourceId:'other'});assert.equal(monitor.state.binding,null);assert.equal(timer.timers.size,0);monitor.dispose();
});
