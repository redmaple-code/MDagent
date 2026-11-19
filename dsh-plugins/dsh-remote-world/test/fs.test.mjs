import test from 'node:test';
import assert from 'node:assert/strict';
import {getEventListeners,once} from 'node:events';
import {WebSocketServer} from 'ws';
import {RemoteWorld} from '../lib/client.js';
import {RemoteFileSystem} from '../lib/fs.js';

async function bounded(promise){
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('FIXTURE_TIMEOUT')),1500);})]);}
  finally{clearTimeout(timer);}
}

function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}

async function fixture(t,handler){
  const server=new WebSocketServer({host:'127.0.0.1',port:0});
  await once(server,'listening');
  const world=RemoteWorld.get({url:`ws://127.0.0.1:${server.address().port}`,token:''});
  t.after(async()=>{
    world.release();
    for(const peer of server.clients)peer.terminate();
    await new Promise(resolve=>server.close(resolve));
  });
  let peer;
  server.on('connection',socket=>{
    peer=socket;
    peer.send(JSON.stringify({t:'ntf',m:'hello',p:{protocol:1}}));
    peer.on('message',raw=>handler(JSON.parse(raw),peer));
  });
  await bounded(world.connect());
  return {world,get peer(){return peer;},streamText:signal=>RemoteFileSystem.prototype.streamText.call({world},{targetKey:'/fixture.txt'},signal)};
}

function sendBatch(peer,...frames){
  // Real frames in one socket write expose the response/Promise registration gap.
  peer._socket.cork();
  try{for(const frame of frames)peer.send(JSON.stringify(frame));}
  finally{peer._socket.uncork();}
}

test('file stream retains data and EOF sent in the same read as its response',async t=>{
  const f=await fixture(t,(request,peer)=>sendBatch(peer,
    {t:'res',id:request.id,r:{sid:'fixture-stream'}},
    {t:'ntf',m:'stream.data',p:{sid:'fixture-stream',data:'first chunk'}},
    {t:'ntf',m:'stream.data',p:{sid:'fixture-stream',data:'second chunk'}},
    {t:'ntf',m:'stream.end',p:{sid:'fixture-stream'}}));
  const stream=await f.streamText();
  assert.deepEqual(await bounded(Array.fromAsync(stream)),['first chunk','second chunk']);
  assert.equal(f.world.streams.size,0);
  assert.equal(f.world.busy,false);
});

test('empty file stream retains EOF sent immediately after its response',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,
    {t:'res',id:request.id,r:{sid:'empty-stream'}},
    {t:'ntf',m:'stream.end',p:{sid:'empty-stream'}}));
  const stream=await f.streamText(abort.signal);
  assert.deepEqual(await bounded(Array.fromAsync(stream)),[]);
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('file stream retains data before an immediate stream error and releases its signal',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,
    {t:'res',id:request.id,r:{sid:'failed-stream'}},
    {t:'ntf',m:'stream.data',p:{sid:'failed-stream',data:'partial text'}},
    {t:'ntf',m:'stream.error',p:{sid:'failed-stream',message:'FIXTURE_READ_FAILED'}}));
  const stream=await f.streamText(abort.signal),iterator=stream[Symbol.asyncIterator]();
  assert.deepEqual(await bounded(iterator.next()),{value:'partial text',done:false});
  await assert.rejects(bounded(iterator.next()),/FIXTURE_READ_FAILED/);
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('abort after a stream response but before its continuation cannot leave a hanging reader',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,{t:'res',id:request.id,r:{sid:'abort-gap-stream'}}));
  // Runs after response dispatch, still in the socket read, before any Promise continuation.
  f.world.ws.on('message',raw=>{if(JSON.parse(raw).t==='res')abort.abort();});
  const stream=await f.streamText(abort.signal);
  await assert.rejects(bounded(Array.fromAsync(stream)),{code:'FS_ABORTED'});
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('returning a file iterator releases its pending reader and stream registration',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,{t:'res',id:request.id,r:{sid:'returned-stream'}}));
  const stream=await f.streamText(abort.signal),iterator=stream[Symbol.asyncIterator]();
  const waiting=iterator.next();
  assert.deepEqual(await bounded(iterator.return()),{value:undefined,done:true});
  assert.deepEqual(await bounded(waiting),{value:undefined,done:true});
  assert.deepEqual(await bounded(iterator.next()),{value:undefined,done:true});
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('stopping file iteration discards buffered text and ignores late notifications',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>{
    if(request.m==='fs.streamText')sendBatch(peer,
      {t:'res',id:request.id,r:{sid:'stopped-stream'}},
      {t:'ntf',m:'stream.data',p:{sid:'stopped-stream',data:'read this'}},
      {t:'ntf',m:'stream.data',p:{sid:'stopped-stream',data:'leave this'}});
    else sendBatch(peer,{t:'res',id:request.id,r:'barrier'});
  });
  const stream=await f.streamText(abort.signal),iterator=stream[Symbol.asyncIterator]();
  assert.deepEqual(await bounded(iterator.next()),{value:'read this',done:false});
  await iterator.return();
  sendBatch(f.peer,
    {t:'ntf',m:'stream.data',p:{sid:'stopped-stream',data:'late text'}},
    {t:'ntf',m:'stream.end',p:{sid:'stopped-stream'}});
  await bounded(f.world.request('fixture.barrier',{}));
  assert.deepEqual(await bounded(iterator.next()),{value:undefined,done:true});
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('disconnect rejects a waiting file reader and releases its abort listener',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,{t:'res',id:request.id,r:{sid:'disconnected-stream'}}));
  const stream=await f.streamText(abort.signal),iterator=stream[Symbol.asyncIterator]();
  const rejected=assert.rejects(bounded(iterator.next()),/mdshell connection closed/);
  f.peer.close(1011,'fixture disconnect');
  await rejected;
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('aborting an in-flight stream request drops its late response and notifications',async t=>{
  const requested=deferred(),abort=new AbortController();
  const f=await fixture(t,(request,peer)=>{
    if(request.m==='fs.streamText')requested.resolve(request);
    else sendBatch(peer,{t:'res',id:request.id,r:'barrier'});
  });
  const rejected=assert.rejects(f.streamText(abort.signal),{code:'FS_ABORTED'});
  const request=await bounded(requested.promise);abort.abort();await rejected;
  sendBatch(f.peer,
    {t:'res',id:request.id,r:{sid:'late-stream'}},
    {t:'ntf',m:'stream.data',p:{sid:'late-stream',data:'late text'}},
    {t:'ntf',m:'stream.end',p:{sid:'late-stream'}});
  await bounded(f.world.request('fixture.barrier',{}));
  assert.equal(f.world.streams.size,0);
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('unknown stream notifications never create retained stream registrations',async t=>{
  const f=await fixture(t,(request,peer)=>sendBatch(peer,{t:'res',id:request.id,r:'barrier'}));
  const frames=Array.from({length:256},(_,i)=>({t:'ntf',m:'stream.data',p:{sid:`unknown-${i}`,data:'ignored'}}));
  sendBatch(f.peer,...frames,
    {t:'ntf',m:'stream.end',p:{sid:'unknown-end'}},
    {t:'ntf',m:'stream.error',p:{sid:'unknown-error',message:'ignored'}});
  await bounded(f.world.request('fixture.barrier',{}));
  assert.equal(f.world.streams.size,0);
  assert.equal(f.world.busy,false);
});

test('a malformed stream response rejects without retaining a registration',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,{t:'res',id:request.id,r:{}}));
  await assert.rejects(bounded(f.streamText(abort.signal)),/invalid stream id/);
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('duplicate stream ids reject without replacing the active reader',async t=>{
  const f=await fixture(t,(request,peer)=>{
    if(request.m==='fs.streamText')sendBatch(peer,{t:'res',id:request.id,r:{sid:'duplicate-stream'}});
    else sendBatch(peer,{t:'res',id:request.id,r:'barrier'});
  });
  const original=await f.streamText();
  await assert.rejects(bounded(f.streamText()),/duplicate stream id/);
  sendBatch(f.peer,
    {t:'ntf',m:'stream.data',p:{sid:'duplicate-stream',data:'original reader'}},
    {t:'ntf',m:'stream.end',p:{sid:'duplicate-stream'}});
  assert.deepEqual(await bounded(Array.fromAsync(original)),['original reader']);
  assert.equal(f.world.busy,false);
});

test('aborting an active file stream discards unread text and releases its resources',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,
    {t:'res',id:request.id,r:{sid:'aborted-stream'}},
    {t:'ntf',m:'stream.data',p:{sid:'aborted-stream',data:'unread text'}}));
  const stream=await f.streamText(abort.signal),iterator=stream[Symbol.asyncIterator]();
  abort.abort();
  await assert.rejects(bounded(iterator.next()),{code:'FS_ABORTED'});
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('already aborted file reads cannot send a stream request',async t=>{
  const abort=new AbortController();abort.abort();
  let requests=0;
  const f=await fixture(t,()=>{requests++;});
  await assert.rejects(f.streamText(abort.signal),{code:'FS_ABORTED'});
  assert.equal(requests,0);
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('refused stream requests release their request signal without opening a stream',async t=>{
  const abort=new AbortController();
  const f=await fixture(t,(request,peer)=>sendBatch(peer,
    {t:'err',id:request.id,e:{name:'FsError',code:'FS_NOT_FOUND',message:'fixture missing'}}));
  await assert.rejects(f.streamText(abort.signal),{code:'FS_NOT_FOUND'});
  assert.equal(f.world.busy,false);
  assert.equal(getEventListeners(abort.signal,'abort').length,0);
});

test('concurrent stream responses register each sid before interleaved data and EOF',async t=>{
  const requests=[];
  const f=await fixture(t,(request,peer)=>{
    requests.push(request);
    if(requests.length!==2)return;
    sendBatch(peer,
      {t:'res',id:requests[0].id,r:{sid:'stream-one'}},
      {t:'ntf',m:'stream.data',p:{sid:'stream-one',data:'one'}},
      {t:'res',id:requests[1].id,r:{sid:'stream-two'}},
      {t:'ntf',m:'stream.data',p:{sid:'stream-two',data:'two'}},
      {t:'ntf',m:'stream.end',p:{sid:'stream-two'}},
      {t:'ntf',m:'stream.end',p:{sid:'stream-one'}});
  });
  const streams=await bounded(Promise.all([f.streamText(),f.streamText()]));
  assert.deepEqual(await bounded(Promise.all(streams.map(stream=>Array.fromAsync(stream)))),[['one'],['two']]);
  assert.equal(f.world.busy,false);
});
