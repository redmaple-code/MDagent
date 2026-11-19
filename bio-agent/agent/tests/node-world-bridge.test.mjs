import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,existsSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {nodeConnection,nodeBridgeChild} from '../node-world-bridge.mjs';
import {BridgeClient} from '../bridge-client.mjs';

const config={nodeId:'n_offline_test',computeProjectsRoot:'/compute/projects',computeReleaseRoot:'/compute/release',computePython:'/compute/env/bin/python'};
class RemoteSubprocessRuntime{
  constructor(){this.world={config:{url:'ws://127.0.0.1:8090/v1/world/n_offline_test'}};this.specs=[];}
  spawn(spec){
    this.specs.push(spec);
    const relay="let input='';process.stdin.on('data',chunk=>{input+=chunk;let i;while((i=input.indexOf('\\n'))!==-1){const request=JSON.parse(input.slice(0,i));input=input.slice(i+1);process.stdout.write(JSON.stringify({schemaVersion:'bio.tool-result.v1',requestId:request.requestId,tool:request.tool,ok:true,result:{source:'offline-node-relay'}})+'\\n');}});process.stdin.on('end',()=>process.exit(0));";
    const child=spawn(process.execPath,['-e',relay],{stdio:['pipe','pipe','pipe'],env:{PATH:'/usr/bin:/bin'}});
    return {stdin:child.stdin,stdout:child.stdout,stderr:child.stderr,
      done:new Promise((resolve,reject)=>{child.once('exit',(exitCode,signal)=>resolve({exitCode,signal}));child.once('error',reject);}),terminate:()=>child.kill('SIGTERM')};
  }
}

test('platform node transport rejects a local provider or a different node',()=>{
  assert.throws(()=>nodeConnection({spawn(){}},config),/BIO_REMOTE_NODE_REQUIRED/);
  const runtime=new RemoteSubprocessRuntime();
  assert.throws(()=>nodeConnection(runtime,{...config,nodeId:'n_other'}),/BIO_NODE_BINDING_INVALID/);
});

test('existing remote-world streams carry fixed Bio RPC without launching control-host Python',async()=>{
  const runtime=new RemoteSubprocessRuntime(),connection=nodeConnection(runtime,config);
  const project=mkdtempSync('/private/tmp/bio-world-transport-test-');
  const client=new BridgeClient({project,python:'/missing/local/python',remoteProject:'/compute/projects/'+'b'.repeat(64),
    nodeWorld:{runtime,connection},spawnChild:()=>{throw new Error('LOCAL_SPAWN_FORBIDDEN');}});
  try{await client.ready;const response=await client.call('bio_inputs',{offset:0,limit:100});assert.equal(response.result.source,'offline-node-relay');}
  finally{await client.close();}
  assert.equal(runtime.specs.length,1);assert.equal(runtime.specs[0].argv[0],config.computePython);
  assert.equal(runtime.specs[0].argv[3],'/compute/release/run_biobridge.py');
  assert.deepEqual(runtime.specs[0].env,{PATH:'/compute/env/bin:/usr/bin:/bin'});
  assert.equal(existsSync(join(project,'workflow.sqlite3')),false);
});

test('node URL rebinding is refused before opening a remote process',()=>{
  const runtime=new RemoteSubprocessRuntime(),connection=nodeConnection(runtime,config);
  runtime.world.config.url='ws://127.0.0.1:8090/v1/world/n_other';
  assert.throws(()=>nodeBridgeChild(runtime,connection,'/compute/projects/'+'c'.repeat(64)),/BIO_NODE_BINDING_INVALID/);
  assert.equal(runtime.specs.length,0);
});

test('node SSH mode opens only a fixed ingress relay and sends the final target descriptor',async()=>{
  const runtime=new RemoteSubprocessRuntime();
  const connection=nodeConnection(runtime,{...config,mode:'node-ssh',gatewayPython:'/ingress/python',
    gatewayReleaseRoot:'/ingress/release',gatewayConnectionFile:'/ingress/operator/connection.json'});
  const target={...JSON.parse(readFileSync(new URL('../../deploy/execution-target.example.json',import.meta.url),'utf8')),
    nodeId:config.nodeId,targetId:'final-compute',python:config.computePython,projectsRoot:config.computeProjectsRoot,releaseRoot:config.computeReleaseRoot};
  const child=nodeBridgeChild(runtime,connection,'/compute/projects/'+'d'.repeat(64),target);
  child.stderr.resume();child.stdout.resume();const ended=new Promise(resolve=>child.once('exit',resolve));child.stdin.end();await ended;
  const spec=runtime.specs[0];assert.equal(spec.argv[0],'/ingress/python');assert.equal(spec.argv[3],'/ingress/release/run_biogateway.py');
  assert.equal(spec.argv[spec.argv.indexOf('--execution-target')+1],JSON.stringify(target));assert.equal(spec.cwd,'/ingress/release');
  assert.deepEqual(spec.env,{PATH:'/ingress:/usr/bin:/bin'});assert.equal(spec.argv.includes('/compute/release/run_biobridge.py'),false);
});
