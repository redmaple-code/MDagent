import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {RemoteSubprocessRuntime} from '../../../../dsh-plugins/dsh-remote-world/lib/subprocess.js';
import {makeBioGate} from '../platform-gate.mjs';
import {targetHash} from '../../../agent/execution-target.mjs';

const target={...JSON.parse(readFileSync(new URL('../../../deploy/execution-target.example.json',import.meta.url))),tenantId:'alice',nodeId:'n_test'};
const access={schemaVersion:'bio.platform-access.v1',tenantId:'alice',nodeId:'n_test',executionTargetSha256:targetHash(target),sessionId:'my-session',action:'bio_submit',workflowId:'my-workflow',revision:1};
function fixture(fetch){
  const runtime=Object.create(RemoteSubprocessRuntime.prototype);
  runtime.world={config:{url:'ws://127.0.0.1:8090/v1/world/n_test',token:'mdw_'+'synthetic'.repeat(4)}};
  return {runtime,gate:makeBioGate({runtime,executionTarget:target,fetch})};
}

test('gate uses the enrolled world provider and preserves every binding field',async()=>{
  let request;
  const {gate}=fixture(async(url,options)=>{
    request={url,options};return Response.json({...JSON.parse(options.body),allowed:true});
  });
  assert.deepEqual(await gate.authorize(access),{...access,allowed:true});
  assert.equal(request.url,'http://127.0.0.1:8090/v1/bio/access');
  assert.equal(request.options.redirect,'error');
  assert.ok(request.options.headers['x-world-token'].startsWith('mdw_'));
  assert.deepEqual(JSON.parse(request.options.body),access);
});

test('an invalid decision, changed target, node or action never authorizes',async()=>{
  for(const item of [{allowed:false},{nodeId:'n_other'},{action:'connect'},{executionTargetSha256:'0'.repeat(64)}]){
    const {gate}=fixture(async()=>Response.json({...access,allowed:true,...item}));
    await assert.rejects(gate.authorize(access),/BIO_PLATFORM_ACCESS_DENIED/);
  }
  let called=false;const {gate}=fixture(async()=>{called=true;return Response.json({});});
  await assert.rejects(gate.authorize({...access,tenantId:'bob'}),/ACCESS_DENIED/);assert.equal(called,false);
});

test('changing a world binding rejects before forwarding the short token',async()=>{
  let called=false;const {runtime,gate}=fixture(async()=>{called=true;return Response.json({});});
  runtime.world.config.url='ws://other.invalid/v1/world/n_test';
  await assert.rejects(gate.authorize(access),/BIO_NODE_BINDING_CHANGED/);assert.equal(called,false);
  runtime.world.config.url='ws://127.0.0.1:8090/v1/world/n_other';
  await assert.rejects(gate.authorize(access),/BIO_NODE_BINDING_INVALID/);assert.equal(called,false);
});

test('short token rotation is allowed without changing the registered node',async()=>{
  let token;const {runtime,gate}=fixture(async(_url,options)=>{
    token=options.headers['x-world-token'];return Response.json({...access,allowed:true});
  });
  runtime.world.config.token='mdw_'+'rotated'.repeat(4);
  await gate.authorize(access);assert.equal(token,runtime.world.config.token);
});

test('HTTP errors, oversized replies and network errors produce a bounded public error',async()=>{
  for(const fetch of [async()=>new Response('denied',{status:403}),async()=>new Response('x'.repeat(32769)),
    async()=>{throw new Error('private transport diagnostics');}]){
    const {gate}=fixture(fetch);await assert.rejects(gate.authorize(access),/^Error: BIO_PLATFORM_ACCESS_DENIED$/);
  }
});

test('abort rejects before any access or scientific action',async()=>{
  let called=false;const {gate}=fixture(async()=>{called=true;return Response.json({});});
  const controller=new AbortController();controller.abort();
  await assert.rejects(gate.authorize(access,controller.signal),/BIO_REQUEST_ABORTED/);assert.equal(called,false);
});
