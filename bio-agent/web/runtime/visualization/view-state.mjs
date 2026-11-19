import {existsSync,lstatSync,readFileSync,writeFileSync,renameSync,openSync,closeSync,fsyncSync} from 'node:fs';
import {join} from 'node:path';import {createHash,randomUUID} from 'node:crypto';
import {validateSource,validatePreviewRequest} from './contracts.mjs';
import {validateAppearance} from './appearance.mjs';
const fields='schemaVersion sessionId viewportId sourceSha256 source previewRequest previewId timePs sourceFrameIndex representation selection camera'.split(' ');
export function validateViewState(state){
  const expected=state?.schemaVersion==='bio.view-state.v2'?[...fields,'appearance']:fields;
  if(!state||Object.keys(state).sort().join('|')!==expected.slice().sort().join('|')||!['bio.view-state.v1','bio.view-state.v2'].includes(state.schemaVersion)||typeof state.sessionId!=='string'||state.sessionId.length<1||state.sessionId.length>256||!(/^[a-z0-9][a-z0-9_.-]{0,127}$/).test(state.viewportId)||!(/^[a-f0-9]{64}$/).test(state.sourceSha256)||!Number.isFinite(state.timePs)||state.timePs<0||!Number.isSafeInteger(state.sourceFrameIndex)||state.sourceFrameIndex<0||!['cartoon','sticks','spheres','lines'].includes(state.representation)||!Array.isArray(state.selection)||state.selection.length>512||!state.selection.every(i=>Number.isSafeInteger(i)&&i>=0)||new Set(state.selection).size!==state.selection.length||JSON.stringify(state).length>16384)throw new Error('INVALID_VIEW_STATE');
  if(state.schemaVersion==='bio.view-state.v2')validateAppearance(state.appearance,'INVALID_VIEW_STATE');
  if(state.source!==null){validateSource(state.source,'INVALID_VIEW_STATE');if(state.source.sourceSha256!==state.sourceSha256)throw new Error('INVALID_VIEW_STATE');}
  if(state.previewRequest!==null){validatePreviewRequest(state.previewRequest);if(state.previewRequest.source.sourceSha256!==state.sourceSha256)throw new Error('INVALID_VIEW_STATE');}
  if(state.previewId!==null&&!/^preview-[a-f0-9]{64}$/.test(state.previewId))throw new Error('INVALID_VIEW_STATE');
  const camera=state.camera;
  if(camera!==null){const allowed=['mode','fov','position','up','target','radius','radiusMax','fog','clipFar','minNear','minFar'];
    if(!camera||Object.keys(camera).some(k=>!allowed.includes(k))||!['position','up','target'].every(k=>Array.isArray(camera[k])&&camera[k].length===3&&camera[k].every(Number.isFinite))||!['perspective','orthographic'].includes(camera.mode)||Object.entries(camera).some(([k,v])=>!['position','up','target','mode','clipFar'].includes(k)&&!Number.isFinite(v)))throw new Error('INVALID_VIEW_STATE');}
  return state;
}
export class ViewStateStore{
  constructor(project){this.project=project;}
  path(sessionId){return join(this.project,'view-'+createHash('sha256').update(sessionId).digest('hex')+'.json');}
  load(sessionId){const file=this.path(sessionId);if(!existsSync(file))return null;if(lstatSync(file).isSymbolicLink()||lstatSync(file).size>32768)throw new Error('INVALID_VIEW_STATE');const value=JSON.parse(readFileSync(file,'utf8'));validateViewState(value.state);if(value.state.sessionId!==sessionId||!Number.isSafeInteger(value.revision))throw new Error('INVALID_VIEW_STATE');return {...value.state,stateRevision:value.revision};}
  save(sessionId,state,expectedRevision){validateViewState(state);if(state.sessionId!==sessionId)throw new Error('INVALID_VIEW_STATE');const old=this.load(sessionId);if((old?.stateRevision||0)!==expectedRevision)throw new Error('VIEW_STATE_CHANGED');
    const file=this.path(sessionId),temporary=file+'.'+randomUUID()+'.tmp';writeFileSync(temporary,JSON.stringify({state,revision:expectedRevision+1})+'\n',{flag:'wx',mode:0o600});const fd=openSync(temporary,'r');try{fsyncSync(fd);}finally{closeSync(fd);}renameSync(temporary,file);return this.load(sessionId);
  }
}
