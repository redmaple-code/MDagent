import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validatePreviewRequest,validateViewCommand,previewLimits} from '../visualization/contracts.mjs';

test('Python and browser share closed request cases',()=>{
  for(const c of JSON.parse(readFileSync(new URL('../../../visualization/contract-cases.json',import.meta.url)))){
    if(c.valid)assert.deepEqual(validatePreviewRequest(c.request),c.request);
    else assert.throws(()=>validatePreviewRequest(c.request),/INVALID_PREVIEW_REQUEST/,c.name);
  }
});
test('limits bind buffers, shared egress, frame count and speed',()=>{
  assert.equal(previewLimits.overviewFrames,200);assert.equal(previewLimits.windowFrames,64);
  assert.equal(previewLimits.windowBytes,32*1024**2);assert.equal(previewLimits.cacheBytes,64*1024**2);
  assert.equal(previewLimits.httpMaxBytes,1024**2);assert.equal(previewLimits.egressBitsPerSecond,4000000);
  assert.equal(previewLimits.minFps,1);assert.equal(previewLimits.maxFps,20);
});
test('PBC requests allow only raw or unwrap without accepting executable configuration',()=>{
  const request=JSON.parse(readFileSync(new URL('../../../visualization/contract-cases.json',import.meta.url))).find(c=>c.valid).request;
  for(const pbc of ['raw','unwrap'])assert.deepEqual(validatePreviewRequest({...request,pbc}),{...request,pbc});
  for(const pbc of [null,true,'wrap',{mode:'unwrap'}])assert.throws(()=>validatePreviewRequest({...request,pbc}),/INVALID_PREVIEW_REQUEST/);
  assert.throws(()=>validatePreviewRequest({...request,pbc:'unwrap',python:'/foreign/python'}),/INVALID_PREVIEW_REQUEST/);
});
const expected={sessionId:'session',viewportId:'main',sourceSha256:'a'.repeat(64),sourceRevision:1,visualRevision:1,frameRevision:1,cameraRevision:1};
const command={operationId:'00000000-0000-4000-8000-000000000001',expected,kind:'seekTime',payload:{timePs:2}};
test('display command has no executable or free selector input',()=>{
  assert.deepEqual(validateViewCommand(command),command);
  for(const c of [{...command,payload:{timePs:NaN}},{...command,path:'x'},
    {...command,kind:'select',payload:{selector:'protein'}},{...command,expected:{...expected,frameRevision:true}},
    {...command,kind:'play',payload:{startFrame:0,endFrame:1,fps:21,direction:1,loop:false}}])assert.throws(()=>validateViewCommand(c),/INVALID_VIEW_COMMAND/);
});
