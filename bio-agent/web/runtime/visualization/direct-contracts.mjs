import contract from '../../../visualization/direct-contracts.json' with {type:'json'};
import {validateSource,validateProcessing,previewLimits} from './contracts.mjs';

export const directLimits=Object.freeze(contract.limits);
export const readerVersion=contract.readerVersion;
export const recoverableDirectCodes=new Set(contract.recoverableCodes);
const fail=code=>{throw new Error(code);};
const keys=(v,fields)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(' ')==fields.split(' ').sort().join(' ');
const id=v=>typeof v==='string'&&/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(v);
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const int=(v,min=0,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;

export function validateTransportBinding(v,code='DIRECT_BINDING_CHANGED'){
  if(!keys(v,contract.bindingFields)||!hash(v.ownerBinding)||!id(v.sessionEpoch)||!hash(v.executionTargetSha256))fail(code);
  return v;
}
export function validateDirectRequest(action,v){
  const code='INVALID_DIRECT_REQUEST',fields=contract.requestFields[action];
  if(!fields||!keys(v,fields)||!fields.split(' ').filter(k=>k.endsWith('Id')).every(k=>id(v[k])))fail(code);
  if(action==='direct-open'){validateSource(v.source,code);if(!hash(v.manifestSha256))fail(code);}
  if(action==='direct-permit'&&(!int(v.offset,0,67108864)||!int(v.limit,1,directLimits.permitBytes)||v.offset+v.limit>67108864||!hash(v.sourceSha256)))fail(code);
  if(new TextEncoder().encode(JSON.stringify(v)).length>directLimits.jsonBytes)fail(code);
  return v;
}
export function validateChannel(v){
  const code='DIRECT_BINDING_CHANGED';
  if(!keys(v,contract.channelFields)||v.schemaVersion!=='bio.direct-channel.v1'||v.readerVersion!==readerVersion||!['channelId','targetId','clientId','pairingId','previewId'].every(k=>id(v[k]))||!['executionTargetSha256','projectKey','manifestSha256'].every(k=>hash(v[k])))fail(code);
  if(!int(v.uid,0)||!hash(v.hostIdentitySha256))fail(code);
  validateSource(v.source,code);validateTransportBinding(v.transportBinding,code);
  if(v.executionTargetSha256!==v.transportBinding.executionTargetSha256)fail(code);
  return v;
}
export function validatePermit(v){
  const code='DIRECT_PROTOCOL_INVALID';
  if(!keys(v,contract.permitFields)||v.schemaVersion!=='bio.direct-permit.v1'||!['channelId','previewId','fileId'].every(k=>id(v[k]))||!['sourceSha256','manifestSha256','fileSha256'].every(k=>hash(v[k]))||typeof v.handle!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(v.handle)||!int(v.byteSize,1,67108864)||!int(v.offset,0,v.byteSize-1)||!int(v.limit,1,directLimits.permitBytes)||v.offset+v.limit>v.byteSize||!int(v.expiresAtMs,1))fail(code);
  return v;
}
export function validateDirectManifest(v){
  const code='INVALID_PREVIEW_MANIFEST';
  if(!(keys(v,'schemaVersion previewId source atomCount sourceFrameCount atomMapFile topologyFile frames files precision previewOnly')||keys(v,'schemaVersion previewId source atomCount sourceFrameCount atomMapFile topologyFile frames files precision previewOnly processing'))||v.schemaVersion!=='bio.preview.v1'||!id(v.previewId)||!int(v.atomCount,1,1000000)||!int(v.sourceFrameCount,1,1000000)||v.precision!==3||v.previewOnly!==true||!Array.isArray(v.frames)||v.frames.length<1||v.frames.length>200||!Array.isArray(v.files)||v.files.length<2||v.files.length>202)fail(code);
  validateSource(v.source,code);if(Object.hasOwn(v,'processing'))validateProcessing(v.processing,code);const files=new Map();
  for(const f of v.files){
    if(!keys(f,'fileId byteSize sha256 kind previewFrameStart previewFrameCount')||!id(f.fileId)||files.has(f.fileId)||!hash(f.sha256)||!int(f.byteSize,1,previewLimits.cacheBytes)||!['topology','atom-map','xtc'].includes(f.kind))fail(code);
    if(f.kind==='xtc'?(!int(f.previewFrameStart,0,v.frames.length-1)||!int(f.previewFrameCount,1,64)||f.byteSize>previewLimits.httpMaxBytes||f.previewFrameCount*v.atomCount*12>previewLimits.windowBytes):(f.previewFrameStart!==null||f.previewFrameCount!==null))fail(code);
    files.set(f.fileId,f);
  }
  if(files.get(v.topologyFile)?.kind!=='topology'||files.get(v.atomMapFile)?.kind!=='atom-map')fail(code);
  let lastFrame=-1,lastTime=-Infinity;
  v.frames.forEach((f,i)=>{const segment=files.get(f.segmentId);
    if(!keys(f,'sourceFrameIndex segmentId segmentFrameIndex timePs')||!int(f.sourceFrameIndex,0,v.sourceFrameCount-1)||f.sourceFrameIndex<=lastFrame||segment?.kind!=='xtc'||!int(f.segmentFrameIndex,0,segment.previewFrameCount-1)||i!==segment.previewFrameStart+f.segmentFrameIndex||!Number.isFinite(f.timePs)||f.timePs<0||f.timePs<=lastTime)fail(code);
    lastFrame=f.sourceFrameIndex;lastTime=f.timePs;
  });
  for(const f of v.files)if(f.kind==='xtc'&&v.frames.filter(x=>x.segmentId===f.fileId).length!==f.previewFrameCount)fail(code);
  if(v.source.kind==='input'&&(v.frames.length!==1||v.sourceFrameCount!==1))fail(code);
  return v;
}
const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
export async function manifestDigest(manifest){
  validateDirectManifest(manifest);
  const frames=manifest.frames.map(f=>{const bytes=new Uint8Array(8);new DataView(bytes.buffer).setFloat64(0,f.timePs===0?0:f.timePs,false);return {...f,timePs:Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')};});
  const json=JSON.stringify(ordered(['bio.direct-manifest-identity.v1',{...manifest,frames}]));
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(json))),b=>b.toString(16).padStart(2,'0')).join('');
}
