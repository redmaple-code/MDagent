import contract from '../../../visualization/contracts.json' with {type:'json'};
import {validateAppearance} from './appearance.mjs';
export const previewLimits=Object.freeze(contract.limits);
const fail=code=>{throw new Error(code);};
const keys=(v,n)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(' ')==n.split(' ').sort().join(' ');
const int=(v,min=0,max=1000000)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const id=v=>typeof v==='string'&&/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(v);
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function validateSource(v,code='INVALID_PREVIEW_REQUEST'){
  if(!keys(v,'kind sourceId sourceSha256 workflowId revision stageId attemptId')||!['input','accepted'].includes(v.kind)||!id(v.sourceId)||!hash(v.sourceSha256))fail(code);
  if(v.kind==='input'?!['workflowId','revision','stageId','attemptId'].every(k=>v[k]===null):!['workflowId','stageId','attemptId'].every(k=>id(v[k]))||!int(v.revision,1))fail(code);
  return v;
}
export function validatePreviewRequest(v){
  const code='INVALID_PREVIEW_REQUEST';
  if(!(keys(v,'source profile atomSet startFrame endFrame stride')||keys(v,'source profile atomSet startFrame endFrame stride pbc')))fail(code);
  if(Object.hasOwn(v,'pbc')&&!['raw','unwrap'].includes(v.pbc))fail(code);
  validateSource(v.source,code);
  if(!['overview','detail'].includes(v.profile)||!['overview','solute-heavy','all'].includes(v.atomSet)||!int(v.startFrame)||!int(v.endFrame)||v.endFrame<v.startFrame||!int(v.stride,1))fail(code);
  if(v.source.kind==='input'&&(v.startFrame!==0||v.endFrame!==0))fail(code);
  if(v.profile==='detail'&&Math.floor((v.endFrame-v.startFrame)/v.stride)+1>previewLimits.windowFrames)fail(code);
  return v;
}
export function validateProcessing(v,code='INVALID_PREVIEW_MANIFEST'){
  if(!keys(v,'mode makeWhole makeWholeVersion unwrap unwrapVersion runtimeSha256')||v.mode!=='unwrap'||v.makeWhole!=='MDAnalysis'||v.unwrap!=='pymol.cmd.pbc_unwrap'||!hash(v.runtimeSha256)||!['makeWholeVersion','unwrapVersion'].every(k=>typeof v[k]==='string'&&v[k].length>0&&v[k].length<=128&&!/[\x00-\x1f\x7f]/.test(v[k])))fail(code);
  return v;
}
export function validateViewBinding(v,code='INVALID_VIEW_COMMAND'){
  if(!keys(v,'sessionId viewportId sourceSha256 sourceRevision visualRevision frameRevision cameraRevision')||typeof v.sessionId!=='string'||v.sessionId.length<1||v.sessionId.length>256||/[\x00-\x1f\x7f]/.test(v.sessionId)||!id(v.viewportId)||(v.sourceSha256!==null&&!hash(v.sourceSha256))||!['sourceRevision','visualRevision','frameRevision','cameraRevision'].every(k=>int(v[k],0,Number.MAX_SAFE_INTEGER)))fail(code);
  return v;
}
export function validateViewCommand(v){
  const code='INVALID_VIEW_COMMAND';
  if(!keys(v,'operationId expected kind payload')||typeof v.operationId!=='string'||!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v.operationId)||!contract.viewCommands.includes(v.kind))fail(code);
  validateViewBinding(v.expected,code);const p=v.payload;
  switch(v.kind){
    case 'openSource':if(!keys(p,'source'))fail(code);validateSource(p.source,code);break;
    case 'setRepresentation':if(!(keys(p,'representation')||keys(p,'representation appearance'))||!['cartoon','sticks','spheres','lines'].includes(p.representation))fail(code);if(Object.hasOwn(p,'appearance'))validateAppearance(p.appearance,code);break;
    case 'select':if(!keys(p,'sourceAtomIndices')||!Array.isArray(p.sourceAtomIndices)||p.sourceAtomIndices.length>512||!p.sourceAtomIndices.every(x=>int(x))||new Set(p.sourceAtomIndices).size!==p.sourceAtomIndices.length)fail(code);break;
    case 'seekTime':if(!keys(p,'timePs')||!Number.isFinite(p.timePs)||p.timePs<0)fail(code);break;
    case 'play':if(!keys(p,'startFrame endFrame fps direction loop')||!int(p.startFrame)||!int(p.endFrame)||p.endFrame<p.startFrame||!int(p.fps,previewLimits.minFps,previewLimits.maxFps)||![1,-1].includes(p.direction)||typeof p.loop!=='boolean')fail(code);break;
    default:if(!keys(p,''))fail(code);
  }return v;
}
