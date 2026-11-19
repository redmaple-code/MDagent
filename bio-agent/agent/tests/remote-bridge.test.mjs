import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,writeFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {BridgeClient} from '../bridge-client.mjs';
import {remoteConnection,remoteBridgeCommand} from '../remote-bridge.mjs';

function setup(){
  const root=mkdtempSync('/private/tmp/bio-ssh-transport-test-');
  const configFile=join(root,'config'),knownHosts=join(root,'known_hosts');
  writeFileSync(configFile,'Host bio-test\n  HostName example.invalid\n  User scientific-user\n',{mode:0o600});
  // An offline fixture, never offered to an actual SSH process or host.
  writeFileSync(knownHosts,'example.invalid ssh-ed25519 offline-fixture\n',{mode:0o600});
  const remote=remoteConnection({schemaVersion:'bio.ssh-connection.v1',hostAlias:'bio-test',configFile,knownHosts,
    projectsRoot:'/compute/projects',releaseRoot:'/compute/release',python:'/compute/env/bin/python'});
  return {root,remote,project:'/compute/projects/'+'a'.repeat(64)};
}

test('SSH launch is pinned, batch-only and targets the compute Python and project',()=>{
  const {remote,project}=setup(),command=remoteBridgeCommand(remote,project);
  assert.equal(command.executable,'/usr/bin/ssh');
  assert.equal(command.args.includes('BatchMode=yes'),true);assert.equal(command.args.includes('StrictHostKeyChecking=yes'),true);
  assert.equal(command.args.includes('ForwardAgent=no'),true);assert.equal(command.args.includes('ClearAllForwardings=yes'),true);
  assert.match(command.args.at(-1),/'\/compute\/env\/bin\/python' '-I' '-B'/);
  assert.match(command.args.at(-1),/'\/compute\/release\/run_biobridge.py'/);
  assert.equal(command.args.at(-1).includes(project),true);
  assert.throws(()=>remoteBridgeCommand(remote,'/etc'),/BIO_REMOTE_PROJECT_INVALID/);
  const {identity,...raw}=remote;
  assert.throws(()=>remoteConnection({...raw,hostAlias:'-oProxyCommand=anything'}),/BIO_REMOTE_ALIAS_INVALID/);
});

test('changed pinned files prevent opening a new SSH bridge',()=>{
  const {remote,project}=setup();writeFileSync(remote.knownHosts,'different offline pin\n');
  assert.throws(()=>remoteBridgeCommand(remote,project),/BIO_REMOTE_BINDING_CHANGED/);
});

test('remote RPC uses only the SSH child and carries no API credentials',async()=>{
  const {root,remote,project}=setup();let launched=0;
  const relay="let input='';process.stdin.on('data',chunk=>{input+=chunk;let i;while((i=input.indexOf('\\n'))!==-1){const request=JSON.parse(input.slice(0,i));input=input.slice(i+1);process.stdout.write(JSON.stringify({schemaVersion:'bio.tool-result.v1',requestId:request.requestId,tool:request.tool,ok:true,result:{source:'offline-remote-relay'}})+'\\n');}});process.stdin.on('end',()=>process.exit(0));";
  const client=new BridgeClient({project:join(root,'receipts'),python:'/missing/local/python',remote,remoteProject:project,
    spawnChild:(executable,args,options)=>{
      launched++;assert.equal(executable,'/usr/bin/ssh');assert.equal(args.at(-2),'bio-test');assert.equal(options.shell,false);
      assert.equal(Object.keys(options.env).some(name=>/KEY|TOKEN|SECRET|PASSWORD/.test(name)),false);
      return spawn(process.execPath,['-e',relay],options);
    }});
  try{await client.ready;const response=await client.call('bio_inputs',{offset:0,limit:100});assert.equal(response.result.source,'offline-remote-relay');}
  finally{await client.close();}
  assert.equal(launched,1);assert.equal(existsSync(join(root,'receipts/workflow.sqlite3')),false);
});

test('a disconnected SSH transport cannot fall back to local scientific Python',async()=>{
  const {root,remote,project}=setup();const launches=[];
  const client=new BridgeClient({project:join(root,'receipts'),python:'/missing/local/python',remote,remoteProject:project,
    spawnChild:(executable,_args,options)=>{launches.push(executable);return spawn(process.execPath,['-e','process.exit(1)'],options);}});
  await assert.rejects(client.ready,/BRIDGE_CLOSED/);await client.close();
  assert.deepEqual(launches,['/usr/bin/ssh']);
});
