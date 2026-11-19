// Fixed substitutes only; no model, SSH, DSH web process or simulation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,existsSync,readFileSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {HumanWorkflowApi,installHumanApi} from '../human-api.mjs';
import {SessionProjects,toolNames,projectForSession} from '../bio-plugin.mjs';
import {loadExecutionGate} from '../execution-gate.mjs';
import {loadTarget,targetHash} from '../../../agent/execution-target.mjs';
import {tenantCandidate,writeTenantCandidate} from '../../../deploy/tenant-profile.mjs';
const root=mkdtempSync('/private/tmp/bio-workflow-unit-');
const target=JSON.parse(readFileSync(new URL('../../../deploy/execution-target.example.json',import.meta.url),'utf8'));
target.tenantId='tenant-a';target.nodeId='n_test';target.projectsRoot='/compute/projects';target.releaseRoot='/compute/release';target.python='/compute/env/bin/python';
const targetFile=join(root,'target.json');writeFileSync(targetFile,JSON.stringify(target),{mode:0o600});
const Compile=()=>({Check:()=>true});

function fixture(){
  const calls=[];let creates=0;
  const record={uncertain:new Set(['workflow']),tools:new Map(toolNames.map(name=>[name,{execute:async(_id,args)=>{
    calls.push({name,args});return {details:{ok:true,result:{state:'READY',items:[],nextOffset:null}}};
  }}])),bridge:{control:async(name,args)=>{
    calls.push({name,args});return {ok:true,result:name==='recover'?{state:'READY'}:{state:'READY'}};
  }}};
  const agent={session:{id:'mine'}};
  agent.ctx={tools:{schemas:scope=>scope===agent?toolNames.map(name=>({name})):[]}};
  const ctx={agents:{get:id=>id==='mine'?agent:undefined}};
  const projects={guard:async()=>{},get:async()=>{creates++;return record;}};
  return {record,calls,ctx,projects,api:new HumanWorkflowApi(ctx,projects,{tenantId:'tenant-a',toolNames}),creates:()=>creates};
}

test('human RPC rejects wrong sessions, forged actor and oversized messages before opening a bridge',async()=>{
  const f=fixture();await assert.rejects(f.api.dispatch('context',{sessionId:'other'}),/ACCESS_DENIED/);
  await assert.rejects(f.api.dispatch('approve',{sessionId:'mine',workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64),actor:'forged'}),/REQUEST_INVALID/);
  await assert.rejects(f.api.dispatch('upload-chunk',{sessionId:'mine',uploadId:'x',offset:0,data:'x'.repeat(140000)}),/REQUEST_INVALID/);
  assert.equal(f.creates(),0);
});

test('human approval uses the host namespace with a server-owned actor, and never submits',async()=>{
  const f=fixture();await f.api.dispatch('approve',{sessionId:'mine',workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64)});
  assert.deepEqual(f.calls,[{name:'approve',args:{workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64),actor:'web:tenant-a'}}]);
  f.api.tenantId='t'.repeat(128);await f.api.dispatch('approve',{sessionId:'mine',workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64)});
  assert.ok(f.calls[1].args.actor.length<=128);
});

test('an aborted human action cannot approve after waiting for the Bridge',async()=>{
  const f=fixture();let announce,release;const waiting=new Promise(resolve=>announce=resolve);
  f.projects.get=()=>{announce();return new Promise(resolve=>release=resolve);};
  const controller=new AbortController();
  const pending=f.api.dispatch('approve',{sessionId:'mine',workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64)},controller.signal);
  await waiting;controller.abort();release(f.record);await assert.rejects(pending,/BIO_REQUEST_ABORTED/);assert.deepEqual(f.calls,[]);
});

test('human recovery clears both uncertainty stores and does not replay submit',async()=>{
  const f=fixture();await f.api.dispatch('recover',{sessionId:'mine',workflowId:'workflow'});
  assert.equal(f.record.uncertain.has('workflow'),false);assert.deepEqual(f.calls.map(c=>c.name),['recover','resume']);
  f.record.bridge.control=async(name,args)=>({ok:false,error:{code:'TOOL_RESPONSE_UNKNOWN'}});
  f.record.uncertain.add('workflow');await assert.rejects(f.api.dispatch('recover',{sessionId:'mine',workflowId:'workflow'}),/TOOL_RESPONSE_UNKNOWN/);
  assert.equal(f.record.uncertain.has('workflow'),true);
});

test('large host records are fetched in bounded chunks and whole-record hash is verified',async()=>{
  const f=fixture();const bytes=Buffer.from(JSON.stringify({ok:true,result:{text:'full review '.repeat(15000)}})),sha=createHash('sha256').update(bytes).digest('hex');
  let chunks=0;
  f.record.bridge.control=async(name,args)=>{
    if(name==='record-chunk'){chunks++;const part=bytes.subarray(args.offset,args.offset+args.limit);return {ok:true,result:{offset:args.offset,nextOffset:args.offset+part.length,data:part.toString('base64')}};}
    return {ok:true,result:{fullResult:{path:'/compute/chat/records/'+sha+'.json',byteSize:bytes.length,sha256:sha}}};
  };
  assert.equal((await f.api.dispatch('review',{sessionId:'mine',workflowId:'workflow',revision:1})).text.length,180000);assert.ok(chunks>1);
});

test('Connection RPC installs a trusted-host fence and returns the native response shape',async()=>{
  const f=fixture();let registration;
  f.ctx.effect=fn=>fn();f.ctx.connection={rpc:{handle:(path,handler,options)=>{registration={path,handler,options};return ()=>{};}}};
  installHumanApi(f.ctx,f.projects,{tenantId:'tenant-a',toolNames});
  assert.equal(registration.path,'/bio-workflow');assert.equal(registration.options.authority,'trusted-host');
  const reply=await registration.handler('context',{sessionId:'other'},new AbortController().signal);
  assert.equal(reply.ok,false);assert.equal(reply.error.code,'bad-request');assert.deepEqual(reply.error.details,{issues:[]});
});

test('a closed Bridge is recreated on the same owned project while reconnect never calls submit',async()=>{
  const children=[];const pool=new SessionProjects({projectsRoot:join(root,'reconnect'),python:'/fixed/python',Compile,
    bridgeFactory:options=>{const child={ready:Promise.resolve(),closed:false,calls:[],options,
      control:async(name,args)=>{child.calls.push(name);return {ok:true,result:{advancing:false}};},close:async()=>{child.closed=true;}};children.push(child);return child;}});
  const first=await pool.get('mine');first.bridge.closed=true;await pool.tick();
  assert.equal(children.length,2);assert.equal(children[1].options.project,children[0].options.project);
  assert.deepEqual(children[1].calls,['reconcile']);
  assert.equal(existsSync(join(projectForSession(pool.root,'mine'),'workflow.sqlite3')),false);await pool.close();
});

test('reconnect failures use bounded backoff instead of restarting every tick',async()=>{
  let attempts=0;const pool=new SessionProjects({projectsRoot:join(root,'backoff'),python:'/fixed/python',Compile,
    bridgeFactory:()=>{attempts++;return {ready:attempts===1?Promise.resolve():Promise.reject(new Error('offline')),closed:attempts>1,close:async()=>{},control:async()=>{}};}});
  const first=await pool.get('mine');first.bridge.closed=true;await pool.tick();await pool.tick();assert.equal(attempts,2);await pool.close();
});

test('platform gate is mandatory by default and a decision must match tenant/node/target/session/action',async()=>{
  await assert.rejects(loadExecutionGate({}, {executionTarget:target}),/PLATFORM_GATE_REQUIRED/);
  const module=join(root,'gate.mjs');writeFileSync(module,'export function createBioGate(){return {authorize:async request=>({...request,allowed:true})};}',{mode:0o600});
  const gate=await loadExecutionGate({}, {executionTarget:target,platformGateModule:module});await gate('bio_submit','mine',{workflowId:'workflow',revision:1});
  const denied=join(root,'deny.mjs');writeFileSync(denied,'export function createBioGate(){return {authorize:async request=>({...request,allowed:true,nodeId:"n_other"})};}',{mode:0o600});
  await assert.rejects((await loadExecutionGate({}, {executionTarget:target,platformGateModule:denied}))('bio_submit','mine',{}),/ACCESS_DENIED/);
});

test('target canonical hash matches Python with unicode paths; unsafe config permissions are rejected',()=>{
  const unicode={...target,projectsRoot:'/计算/projects'};
  const expected=execFileSync('python3',['-B','-c','import sys,json,hashlib; print(hashlib.sha256(json.dumps(json.load(sys.stdin),sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest())'],{input:JSON.stringify(unicode),encoding:'utf8'}).trim();
  assert.equal(targetHash(unicode),expected);assert.equal(loadTarget(targetFile).tenantId,'tenant-a');
  const unsafe=join(root,'unsafe.json');writeFileSync(unsafe,JSON.stringify(target));chmodSync(unsafe,0o666);assert.throws(()=>loadTarget(unsafe),/FILE_INVALID/);
});

test('tenant candidate preserves the original agent roster and never writes the original profile',()=>{
  const roster={default:'md-sim-assistant',roots:[{path:'/original/presets',trust:'system'}],includeUserRoot:false};
  const config={tenantId:'tenant-a',output:join(root,'candidate'),mode:'node-world',executionTargetFile:targetFile,compilerRoot:'/existing/typebox',
    projectsRoot:'/tenant/bio-receipts',platformGateModule:'/tenant/operator/bio-gate.mjs',rosterConfig:roster};
  const value=tenantCandidate(config);assert.equal(roster.roots.length,1);assert.equal(value.files['profile.patch.json'][0].config.default,'md-sim-assistant');
  assert.equal(value.files['profile.patch.json'][0].config.roots.length,2);assert.equal(value.files['candidate.json'].originalProvidersChanged,false);
  const written=writeTenantCandidate(config);assert.equal(written.status,'GENERATED_NOT_APPLIED');assert.throws(()=>writeTenantCandidate(config),/OUTPUT_CONFLICT/);
  assert.throws(()=>tenantCandidate({...config,tenantId:'other'}),/CONFIG_INVALID/);
  const packaged=tenantCandidate({...config,agentPlugin:'dsh-bio-agent'});
  assert.equal(packaged.files['presets/bio/agent.cordis.yml'][0].name,'dsh-bio-agent');
  assert.throws(()=>tenantCandidate({...config,agentPlugin:'untrusted-module'}),/CONFIG_INVALID/);
});

console.log('Engineering fixtures retained at',root);
