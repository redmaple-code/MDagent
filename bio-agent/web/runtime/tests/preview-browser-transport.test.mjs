import test from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';import {readFileSync} from 'node:fs';
import {createPreviewTransport} from '../../visualization/src/transport.mjs';import {manifestDigest} from '../visualization/direct-contracts.mjs';
const cases=JSON.parse(readFileSync(new URL('../../../visualization/direct-contract-cases.json',import.meta.url)));
async function fixture(bytes=new Uint8Array([1,2,3])){const manifest=structuredClone(cases.manifest);manifest.files[2].byteSize=bytes.length;manifest.files[2].sha256=createHash('sha256').update(bytes).digest('hex');const digest=await manifestDigest(manifest);
 return {manifest,bytes,reply:()=>new Response(bytes,{headers:{'x-bio-source-sha256':manifest.source.sourceSha256,'x-bio-file-sha256':manifest.files[2].sha256,'x-bio-manifest-sha256':digest,'x-bio-next-offset':String(bytes.length)}}),describe:()=>Response.json({source:manifest.source,transportBinding:cases.channel.transportBinding,directEnabled:true})};}
const direct=fetch=>({mode:'direct',readChunk:args=>fetch('/connector/read',args),dispose(){}});
const until=async predicate=>{for(let i=0;i<100&&!predicate();i++)await new Promise(r=>setTimeout(r,1));assert.ok(predicate());};
test('owned operation cancellation remains available after transport disposal',async()=>{
 const requests=[],t=createPreviewTransport({sessionId:'one',viewportId:'main',fetch:async(url,args)=>{requests.push({url,...args});return Response.json({state:'CANCELED'});}});
 t.dispose();assert.equal(typeof t.api.cancelOperation,'function');await t.api.cancelOperation({operationId:'147fd91d-3440-4932-8536-c395960421cb'});
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/v1/bio/preview/cancel');assert.equal(requests[0].signal.aborted,false);assert.equal(requests[0].headers['x-bio-session'],'one');
});
test('hash verification and two request slots apply before data is cached',async()=>{
 const f=await fixture();let active=0,max=0,calls=0;const fetch=async url=>{if(url.endsWith('/describe'))return f.describe();calls++;active++;max=Math.max(max,active);await new Promise(r=>setTimeout(r,5));active--;return f.reply();};
 const t=createPreviewTransport({sessionId:'one',viewportId:'main',fetch,binaryProvider:direct(fetch)});await Promise.all([t.getFile(f.manifest,'segment.xtc'),t.getFile(f.manifest,'segment.xtc'),t.getFile(f.manifest,'segment.xtc')]);assert.equal(calls,1);assert.ok(max<=2);t.dispose();
 const other=createPreviewTransport({sessionId:'two',viewportId:'main',fetch,binaryProvider:direct(fetch)});await other.getFile(f.manifest,'segment.xtc');assert.equal(calls,2);other.dispose();
});
test('corrupt, oversized and late canceled data cannot be returned',async()=>{const f=await fixture(new Uint8Array([1]));const request=async url=>url.endsWith('/describe')?f.describe():new Response(new Uint8Array([1,2]));const t=createPreviewTransport({sessionId:'s',viewportId:'main',fetch:request,binaryProvider:direct(request)});await assert.rejects(t.getFile(f.manifest,'segment.xtc'),/ARTIFACT_CHANGED/);t.dispose();});
test('fresh seek replaces canceled prefetch without old finally deleting its download',async()=>{
 const f=await fixture();let calls=0,active=0,max=0,releaseOld,releaseNew;
 const fetch=async(url,{signal})=>{if(url.endsWith('/describe'))return f.describe();calls++;active++;max=Math.max(max,active);const old=calls===1;try{await new Promise(resolve=>{if(old)releaseOld=resolve;else releaseNew=resolve;});if(signal.aborted)throw new Error('OLD_FETCH_ABORTED');return f.reply();}finally{active--;}};
 const t=createPreviewTransport({sessionId:'one',viewportId:'main',fetch,binaryProvider:direct(fetch)}),oldSignal=new AbortController(),newSignal=new AbortController();const old=t.getFile(f.manifest,'segment.xtc',oldSignal.signal,'prefetch');const oldRejected=assert.rejects(old,/OLD_FETCH_ABORTED/);await until(()=>!!releaseOld);oldSignal.abort();
 const fresh=t.getFile(f.manifest,'segment.xtc',newSignal.signal);await until(()=>!!releaseNew);assert.equal(calls,2);releaseOld();await oldRejected;
 const shared=t.getFile(f.manifest,'segment.xtc',newSignal.signal);await new Promise(r=>setImmediate(r));assert.equal(calls,2);releaseNew();assert.deepEqual(await fresh,f.bytes);assert.deepEqual(await shared,f.bytes);assert.ok(max<=2);t.dispose();
});

test('unpaired viewer never requests coordinate GET and rejects non-direct providers',async()=>{
 const f=await fixture(),requests=[];const t=createPreviewTransport({sessionId:'s',viewportId:'main',fetch:async(url,opts)=>{requests.push({url,method:opts.method});return url.endsWith('/describe')?f.describe():f.reply();}});
 await assert.rejects(t.getFile(f.manifest,'segment.xtc'),/CONNECTOR_UNAVAILABLE/);assert.equal(requests.filter(r=>r.method==='GET').length,0);
 assert.throws(()=>t.setProvider({mode:'relay'}),/DIRECT_CONFIG_INVALID/);t.dispose();
});
