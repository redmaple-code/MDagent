import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {directLimits,validateDirectRequest,validateChannel,validatePermit,manifestDigest} from '../visualization/direct-contracts.mjs';

const cases=JSON.parse(readFileSync(new URL('../../../visualization/direct-contract-cases.json',import.meta.url)));
test('closed direct requests reject client paths, owners and boolean ranges',()=>{
  for(const c of cases.validRequests)assert.deepEqual(validateDirectRequest(c.action,c.value),c.value);
  for(const c of cases.invalidRequests)assert.throws(()=>validateDirectRequest(c.action,c.value),/INVALID_DIRECT_REQUEST/);
});
test('channel and permit bind exact target, protocol and range',()=>{
  validateChannel(cases.channel);validatePermit(cases.permit);
  for(const patch of [{path:'/tmp'},{projectKey:'../other'},{executionTargetSha256:'a'.repeat(64)},{readerVersion:'unknown'}])assert.throws(()=>validateChannel({...cases.channel,...patch}));
  for(const patch of [{limit:262145},{offset:true},{offset:524288},{expiresAtMs:NaN},{handle:'short'}])assert.throws(()=>validatePermit({...cases.permit,...patch}));
});
test('canonical manifest digest uses literal cross-language float64 vector',async()=>{
  assert.equal(await manifestDigest(cases.manifest),cases.manifestDigest);
  const same=structuredClone(cases.manifest);same.frames[0].timePs=-0;
  assert.equal(await manifestDigest(same),cases.manifestDigest);
  for(const mutate of [m=>m.source.sourceSha256='b'.repeat(64),m=>m.frames[1].timePs=2.5,m=>m.files[0].sha256='b'.repeat(64)]){
    const changed=structuredClone(cases.manifest);mutate(changed);assert.notEqual(await manifestDigest(changed),cases.manifestDigest);
  }
});
test('direct identity binds processing metadata and rejects unknown processing fields',async()=>{
  const processing={mode:'unwrap',makeWhole:'MDAnalysis',makeWholeVersion:'2.10.0',unwrap:'pymol.cmd.pbc_unwrap',unwrapVersion:'3.2.0a',runtimeSha256:'c'.repeat(64)};
  const derived={...cases.manifest,processing};
  assert.notEqual(await manifestDigest(derived),cases.manifestDigest);
  assert.notEqual(await manifestDigest({...derived,processing:{...processing,runtimeSha256:'d'.repeat(64)}}),await manifestDigest(derived));
  await assert.rejects(manifestDigest({...derived,processing:{...processing,python:'/foreign/python'}}),/INVALID_PREVIEW_MANIFEST/);
});
test('permit boundary denies next byte and untrusted nonfinite time',()=>{
  const request={...cases.validRequests[1].value,limit:directLimits.permitBytes};validateDirectRequest('direct-permit',request);
  assert.throws(()=>validateDirectRequest('direct-permit',{...request,limit:request.limit+1}));
});
