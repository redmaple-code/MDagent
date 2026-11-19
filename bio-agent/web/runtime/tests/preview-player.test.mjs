import test from 'node:test';import assert from 'node:assert/strict';
import {createPreviewPlayer} from '../../visualization/src/player.mjs';
const manifest={previewId:'preview-a',source:{sourceSha256:'a'.repeat(64)},atomCount:2,topologyFile:'top',atomMapFile:'map',frames:[
  {sourceFrameIndex:0,segmentId:'seg',segmentFrameIndex:0,timePs:1.25},{sourceFrameIndex:202,segmentId:'seg',segmentFrameIndex:1,timePs:506.25}],
  files:[{fileId:'top',kind:'topology'},{fileId:'map',kind:'atom-map'},{fileId:'seg',kind:'xtc',previewFrameCount:2}]};
function fixture(transport={getFile:async()=>new Uint8Array(24)}){const drawn=[];const renderer={open:async()=>{},setFrame:async(b,i)=>drawn.push(i),observe:()=>({ready:true}),dispose(){},setRepresentation(){},select(){},focus(){}};
  return {drawn,player:createPreviewPlayer({renderer,transport,onState:()=>{}})};}
test('seek uses true time, maps zero based source to one based UI and includes last',async()=>{
  const f=fixture();await f.player.open(manifest);const result=await f.player.seekTime(500);
  assert.equal(result.actualTimePs,506.25);assert.equal(f.player.state.sourceFrameNumber,203);assert.deepEqual(f.drawn,[0,1]);f.player.dispose();
});
test('single frame is static and corruption never reaches renderer',async()=>{
  const f=fixture();await f.player.open({...manifest,frames:manifest.frames.slice(0,1)});
  assert.throws(()=>f.player.play({startFrame:0,endFrame:0,fps:10,direction:1,loop:false}),/PREVIEW_STATIC_STRUCTURE/);f.player.dispose();
  const bad=fixture({getFile:async()=>{throw new Error('ARTIFACT_CHANGED');}});await assert.rejects(bad.player.open(manifest),/ARTIFACT_CHANGED/);assert.deepEqual(bad.drawn,[]);
});
test('pause does not race canceled seek, automatic ticks preserve navigation revision',async()=>{
  const f=fixture();await f.player.open(manifest);f.player.play({startFrame:0,endFrame:1,fps:20,direction:1,loop:false});
  const revision=f.player.state.frameRevision;await f.player.finished;
  assert.equal(f.player.state.frameRevision,revision);assert.equal(f.player.state.sourceFrameNumber,203);f.player.dispose();
});
test('new source waits for an already running renderer mutation',async()=>{
  const log=[];let release;let block=false;
  const renderer={open:async m=>log.push('open-'+m.previewId),setFrame:async()=>{if(block){log.push('old-start');await new Promise(r=>release=r);log.push('old-end');}},observe:()=>({ready:true}),dispose(){}};
  const player=createPreviewPlayer({renderer,transport:{getFile:async()=>new Uint8Array(24)}});await player.open(manifest);block=true;
  const seek=player.seekTime(500);await new Promise(r=>setImmediate(r));
  const next=player.open({...manifest,previewId:'preview-b',source:{sourceSha256:'b'.repeat(64)}});await new Promise(r=>setImmediate(r));
  assert.equal(log.includes('open-preview-b'),false);block=false;release();await assert.rejects(seek,/BIO_REQUEST_ABORTED/);await next;
  assert.ok(log.indexOf('old-end')<log.indexOf('open-preview-b'));player.dispose();
});
const resumable={...manifest,frames:[
  {sourceFrameIndex:0,segmentId:'seg',segmentFrameIndex:0,timePs:0},
  {sourceFrameIndex:4,segmentId:'seg',segmentFrameIndex:1,timePs:10},
  {sourceFrameIndex:9,segmentId:'seg',segmentFrameIndex:2,timePs:25},
  {sourceFrameIndex:19,segmentId:'seg',segmentFrameIndex:3,timePs:50}],
  files:manifest.files.map(f=>f.fileId==='seg'?{...f,previewFrameCount:4}:f)};
test('Play resumes a sought frame and keeps its real time mapping',async()=>{
  const f=fixture();try{
    await f.player.open(resumable);await f.player.seekTime(24);f.drawn.length=0;
    f.player.play({startFrame:0,endFrame:3,fps:20,direction:1,loop:false});await f.player.finished;
    assert.deepEqual(f.drawn,[2,3]);assert.equal(f.player.state.timePs,50);assert.equal(f.player.state.sourceFrameNumber,20);
  }finally{f.player.dispose();}
});
test('Play resumes the actual paused frame in the requested direction',async()=>{
  let paused;const stopped=new Promise(r=>paused=r),drawn=[];let actual={ready:true};
  const renderer={open:async()=>{},dispose(){},observe:()=>actual,setFrame:async(_bytes,index,frame)=>{drawn.push(index);actual={ready:true,sourceSha256:resumable.source.sourceSha256,sourceFrameIndex:frame.sourceFrameIndex,timePs:frame.timePs};}};
  let armed=false;const player=createPreviewPlayer({renderer,transport:{getFile:async()=>new Uint8Array(48)},onState:state=>{if(armed&&state.playing&&state.frameIndex===2){armed=false;void player.pause().then(paused);}}});
  try{
    await player.open(resumable);armed=true;player.play({startFrame:0,endFrame:3,fps:20,direction:1,loop:false});await stopped;await player.finished;drawn.length=0;
    player.play({startFrame:0,endFrame:3,fps:20,direction:-1,loop:false});await player.finished;
    assert.deepEqual(drawn,[2,1,0]);
  }finally{player.dispose();}
});
test('Play starts at the range boundary when the current frame is outside it',async()=>{
  const f=fixture();try{
    await f.player.open(resumable);f.drawn.length=0;
    f.player.play({startFrame:1,endFrame:3,fps:20,direction:-1,loop:false});await f.player.finished;
    assert.deepEqual(f.drawn,[3,2,1]);
  }finally{f.player.dispose();}
});
test('loop playback resumes the last selected frame before wrapping',async()=>{
  const drawn=[];let stop;const stopped=new Promise(r=>stop=r);let armed=false;
  const renderer={open:async()=>{},dispose(){},observe:()=>({ready:true}),setFrame:async(_bytes,index)=>drawn.push(index)};
  const player=createPreviewPlayer({renderer,transport:{getFile:async()=>new Uint8Array(48)},onState:state=>{if(armed&&state.playing&&drawn.length===2){armed=false;void player.pause().then(stop);}}});
  try{
    await player.open(resumable);await player.seekTime(50);drawn.length=0;armed=true;
    player.play({startFrame:0,endFrame:3,fps:20,direction:1,loop:true});await stopped;await player.finished;
    assert.deepEqual(drawn,[3,0]);
  }finally{player.dispose();}
});
