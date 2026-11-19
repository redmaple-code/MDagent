import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {mkdtempSync,readFileSync} from 'node:fs';
import {WebSocketServer} from 'ws';
import {Context} from '@deepseek-ai/cordis';
import {RemoteSubprocessRuntime} from '../lib/subprocess.js';
import {RemoteFileSystem} from '../lib/fs.js';
import {RemoteSandboxProvider} from '../lib/sandbox.js';
import {BridgeClient} from '../../../bio-agent/agent/bridge-client.mjs';
import {nodeConnection} from '../../../bio-agent/agent/node-world-bridge.mjs';
import {targetHash} from '../../../bio-agent/agent/execution-target.mjs';

function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
async function bounded(promise){
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('FIXTURE_TIMEOUT')),1500);})]);}
  finally{clearTimeout(timer);}
}
async function fixture(t,handler){
  const server=new WebSocketServer({host:'127.0.0.1',port:0});
  await once(server,'listening');
  const connected=deferred(),frames=[];
  let peer,runtime;
  const send=value=>peer.send(JSON.stringify(value));
  server.on('connection',socket=>{
    peer=socket;
    peer.on('message',data=>{const frame=JSON.parse(data);frames.push(frame);handler?.(frame,send);});
    connected.resolve();
  });
  const ctx=new Context();
  const plugin=ctx.plugin({name:'remote-transport-fixture',apply:context=>{
    runtime=new RemoteSubprocessRuntime(context,{url:`ws://127.0.0.1:${server.address().port}/v1/world/n_fixture`,token:''});
  }});
  await plugin.await();
  t.after(async()=>{
    await plugin.dispose();
    for(const client of server.clients)client.terminate();
    await new Promise(resolve=>server.close(resolve));
  });
  await bounded(connected.promise);
  return {runtime,frames,send,plugin,peer,ctx,
    hello:()=>send({t:'ntf',m:'hello',p:{protocol:1}}),
    listeners:()=>[...runtime.world.listeners.values()].reduce((n,set)=>n+set.size,0)};
}
const pipe={argv:['fixture-python'],stdio:{stdin:'pipe',stdout:'pipe',stderr:'pipe'}};

test('cold subprocess queues stdin and EOF until spawn acknowledgement',async t=>{
  const spawning=deferred(),ended=deferred();
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='proc.spawn')spawning.resolve(frame);
    if(frame.m==='proc.stdinEnd'){ended.resolve();send({t:'ntf',m:'proc.exit',p:{hid:frame.p.hid,exitCode:0}});}
  });
  const handle=f.runtime.spawn(pipe);handle.stdout.resume();handle.stderr.resume();
  assert.equal(handle.pid,-1);
  handle.stdin.write('first');handle.stdin.end('second');
  f.hello();const spawn=await bounded(spawning.promise);
  assert.deepEqual(f.frames.map(x=>x.m),['proc.spawn']);
  f.send({t:'res',id:spawn.id,r:{pid:42}});
  await bounded(ended.promise);assert.equal((await handle.done).exitCode,0);
  assert.equal(handle.pid,42);
  assert.deepEqual(f.frames.map(x=>x.m),['proc.spawn','proc.stdin','proc.stdin','proc.stdinEnd']);
  assert.deepEqual(f.frames.filter(x=>x.m==='proc.stdin').map(x=>Buffer.from(x.p.data,'base64').toString()),['first','second']);
  assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('warm connection still waits for remote spawn before stdin',async t=>{
  const spawning=deferred(),ended=deferred();
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='proc.spawn')spawning.resolve(frame);
    if(frame.m==='proc.stdinEnd'){ended.resolve();send({t:'ntf',m:'proc.exit',p:{hid:frame.p.hid,exitCode:0}});}
  });
  f.hello();await f.runtime.world.connect();
  const handle=f.runtime.spawn(pipe);handle.stdout.resume();handle.stderr.resume();handle.stdin.end('payload');
  const spawn=await bounded(spawning.promise);
  assert.deepEqual(f.frames.map(x=>x.m),['proc.spawn']);
  f.send({t:'res',id:spawn.id,r:{pid:43}});await bounded(ended.promise);await handle.done;
});

test('startup rejection fails queued writes and releases handle listeners',async t=>{
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='proc.spawn')send({t:'err',id:frame.id,e:{code:'FIXTURE_SPAWN_REJECTED',message:'fixture refusal'}});
  });
  const handle=f.runtime.spawn(pipe);handle.stdout.resume();handle.stderr.resume();
  handle.stdin.on('error',()=>{});
  const written=new Promise(resolve=>handle.stdin.write('queued',resolve));
  f.hello();assert.equal((await bounded(handle.done)).exitCode,127);
  assert.ok(await bounded(written));
  assert.equal(f.frames.some(x=>x.m==='proc.stdin'),false);
  assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('cold real Bio bridge delivers target and reconcile before tools',async t=>{
  const config={nodeId:'n_fixture',computePython:'/compute/env/bin/python',computeProjectsRoot:'/compute/projects',computeReleaseRoot:'/compute/release'};
  const target={...JSON.parse(readFileSync(new URL('../../../bio-agent/deploy/execution-target.example.json',import.meta.url))),
    nodeId:config.nodeId,targetId:'fixture-compute',python:config.computePython,projectsRoot:config.computeProjectsRoot,releaseRoot:config.computeReleaseRoot};
  const calls=[];
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='proc.spawn')send({t:'res',id:frame.id,r:{pid:44}});
    if(frame.m==='proc.stdin'){
      const request=JSON.parse(Buffer.from(frame.p.data,'base64').toString().trim());calls.push(request.tool);
      const result=request.tool==='target'?{identityVerified:true,executionTargetSha256:targetHash(target)}:{source:'isolated-websocket-fixture'};
      const response={schemaVersion:'bio.tool-result.v1',requestId:request.requestId,tool:request.tool,ok:true,result};
      send({t:'ntf',m:'proc.out',p:{hid:frame.p.hid,fd:'stdout',data:Buffer.from(JSON.stringify(response)+'\n').toString('base64')}});
    }
    if(frame.m==='proc.stdinEnd')send({t:'ntf',m:'proc.exit',p:{hid:frame.p.hid,exitCode:0}});
  });
  const client=new BridgeClient({project:mkdtempSync('/private/tmp/bio-cold-bridge-'),python:'/missing/control/python',
    remoteProject:'/compute/projects/'+'f'.repeat(64),executionTarget:target,
    nodeWorld:{runtime:f.runtime,connection:nodeConnection(f.runtime,config)},spawnChild:()=>{throw new Error('CONTROL_SPAWN_FORBIDDEN');}});
  // On a failed assertion close the channel first to reject all long-lived RPC timers.
  t.after(async()=>{f.peer.terminate();await client.close();});
  f.hello();await bounded(client.ready);
  assert.equal((await bounded(client.call('bio_inputs',{offset:0,limit:100}))).ok,true);
  assert.deepEqual(calls,['target','reconcile','bio_inputs']);
  await bounded(client.close());assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('terminal channel disposal ends output and resolves unknown outcome once',async t=>{
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='term.spawn')send({t:'res',id:frame.id,r:{pid:45}});
  });
  f.hello();const terminal=await f.runtime.spawnTerminal({argv:['fixture-shell']});terminal.output.resume();
  const ended=once(terminal.output,'end');let completions=0;terminal.done.then(()=>{completions++;});
  await f.plugin.dispose();
  assert.equal(f.runtime.world.closing,true,'provider disposal must release its world reference');
  assert.deepEqual(await bounded(terminal.done),{exitCode:null,signal:'SIGHUP'});await bounded(ended);
  f.runtime.world.dispatch({t:'ntf',m:'term.exit',p:{tid:terminal.tid,exitCode:0}});
  assert.equal(completions,1);assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('terminal spawn refusal releases all opened handle resources',async t=>{
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='term.spawn')send({t:'err',id:frame.id,e:{message:'FIXTURE_TERMINAL_REFUSED'}});
  });
  f.hello();await assert.rejects(f.runtime.spawnTerminal({argv:['fixture-shell']}),/FIXTURE_TERMINAL_REFUSED/);
  assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('disconnect during spawn fails queued stdin and EOF without claiming success',async t=>{
  const spawning=deferred();
  const f=await fixture(t,frame=>{if(frame.m==='proc.spawn')spawning.resolve(frame);});
  const handle=f.runtime.spawn(pipe);handle.stdout.resume();handle.stderr.resume();
  const written=new Promise(resolve=>handle.stdin.write('queued',resolve));handle.stdin.end();
  f.hello();await bounded(spawning.promise);f.peer.close(1011,'fixture disconnect');
  assert.deepEqual(await bounded(handle.done),{exitCode:null,signal:'SIGHUP'});
  assert.ok(await bounded(written));assert.equal(f.frames.some(x=>x.m==='proc.stdinEnd'),false);
  assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('process cancellation during spawn terminates only after acknowledgement',async t=>{
  const spawning=deferred(),terminated=deferred(),abort=new AbortController();
  const f=await fixture(t,frame=>{
    if(frame.m==='proc.spawn')spawning.resolve(frame);
    if(frame.m==='proc.terminate')terminated.resolve(frame);
  });
  const handle=f.runtime.spawn({...pipe,signal:abort.signal});handle.stdout.resume();handle.stderr.resume();
  const written=new Promise(resolve=>handle.stdin.write('canceled input',resolve));
  f.hello();const spawn=await bounded(spawning.promise);abort.abort();
  assert.deepEqual(await bounded(handle.done),{exitCode:null,signal:'SIGTERM'});
  assert.ok(await bounded(written));
  f.send({t:'res',id:spawn.id,r:{pid:46}});const stopped=await bounded(terminated.promise);
  assert.equal(stopped.p.hid,handle.hid);
  assert.equal(f.frames.some(x=>x.m==='proc.stdin'),false);
  assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('waitForExit observes a handle only after its spawn acknowledgement',async t=>{
  const spawning=deferred();
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='proc.spawn')spawning.resolve(frame);
    if(frame.m==='proc.waitForExit'){
      send({t:'res',id:frame.id,r:{exitCode:0,signal:null}});
      send({t:'ntf',m:'proc.exit',p:{hid:frame.p.hid,exitCode:0}});
    }
  });
  const handle=f.runtime.spawn({argv:['fixture-python']});handle.stdout.resume();handle.stderr.resume();
  const waited=handle.waitForExit();f.hello();const spawn=await bounded(spawning.promise);
  assert.deepEqual(f.frames.map(x=>x.m),['proc.spawn']);
  f.send({t:'res',id:spawn.id,r:{pid:47}});assert.equal((await bounded(waited)).exitCode,0);await bounded(handle.done);
  assert.equal(f.listeners(),0);
});

test('already canceled process and terminal cannot spawn after a late hello',async t=>{
  const f=await fixture(t),abort=new AbortController();abort.abort();
  const process=f.runtime.spawn({...pipe,signal:abort.signal});process.stdout.resume();process.stderr.resume();
  const terminal=f.runtime.spawnTerminal({argv:['fixture-shell'],signal:abort.signal});
  const rejected=assert.rejects(terminal,/canceled/);
  f.hello();await bounded(rejected);assert.equal((await bounded(process.done)).signal,'SIGTERM');
  assert.deepEqual(f.frames,[]);assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});

test('terminal cancellation in flight releases listeners and terminates after acknowledgement',async t=>{
  const spawning=deferred(),terminated=deferred(),abort=new AbortController();
  const f=await fixture(t,(frame,send)=>{
    if(frame.m==='term.spawn')spawning.resolve(frame);
    if(frame.m==='term.terminate'){terminated.resolve(frame);send({t:'res',id:frame.id,r:{}});}
  });
  const pending=f.runtime.spawnTerminal({argv:['fixture-shell'],signal:abort.signal});
  const rejected=assert.rejects(pending,/canceled/);f.hello();const spawn=await bounded(spawning.promise);
  abort.abort();assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
  await bounded(rejected);f.send({t:'res',id:spawn.id,r:{pid:48}});await bounded(terminated.promise);
});

test('ordinary terminal exit and later close complete the handle exactly once',async t=>{
  const f=await fixture(t,(frame,send)=>{if(frame.m==='term.spawn')send({t:'res',id:frame.id,r:{pid:49}});});
  f.hello();const terminal=await f.runtime.spawnTerminal({argv:['fixture-shell']});terminal.output.resume();
  let completions=0;terminal.done.then(()=>{completions++;});
  const ended=once(terminal.output,'end');f.send({t:'ntf',m:'term.exit',p:{tid:terminal.tid,exitCode:0}});
  assert.deepEqual(await bounded(terminal.done),{exitCode:0,signal:null});await bounded(ended);
  await f.plugin.dispose();assert.equal(completions,1);assert.equal(f.listeners(),0);
  await assert.rejects(terminal.write('after close'),/terminal closed/);
});

test('three providers release a shared world only after the final owner unloads',async t=>{
  const f=await fixture(t,(frame,send)=>{if(frame.m==='term.spawn')send({t:'res',id:frame.id,r:{pid:50}});});
  let fs,sandbox;
  const files=f.ctx.plugin({name:'remote-files-fixture',apply:ctx=>{fs=new RemoteFileSystem(ctx,f.runtime.config);}});
  const confined=f.ctx.plugin({name:'remote-sandbox-fixture',apply:ctx=>{sandbox=new RemoteSandboxProvider(ctx,f.runtime.config);}});
  await files.await();await confined.await();t.after(async()=>{await files.dispose();await confined.dispose();});
  assert.equal(fs.world,f.runtime.world);assert.equal(sandbox.world,f.runtime.world);assert.equal(f.runtime.world.refs,3);
  f.hello();const terminal=await f.runtime.spawnTerminal({argv:['fixture-shell']});terminal.output.resume();
  await files.dispose();await confined.dispose();assert.equal(f.runtime.world.refs,1);assert.equal(f.runtime.world.closing,false);
  await f.plugin.dispose();assert.equal((await bounded(terminal.done)).signal,'SIGHUP');
  assert.equal(f.runtime.world.refs,0);assert.equal(f.runtime.world.liveHandles,0);assert.equal(f.listeners(),0);
});
