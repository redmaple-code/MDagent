import display from './view-contracts.json' with {type:'json'};
import {validateViewBinding,validateViewCommand} from './contracts.mjs';
import {ViewStateStore,validateViewState} from './view-state.mjs';
import {bindingMatches} from '../../visualization/src/frame-binding.mjs';
import {validateAppearance} from './appearance.mjs';
export const displayToolNames=Object.keys(display.tools);
const clientKey=(sessionId,clientId)=>JSON.stringify([sessionId,clientId]);
export function validateObservation(o){
  const fields='binding ready playing buffering sourceFrameIndex timePs selection representation camera previewFrameIndex previewFrameCount sourceFrameCount'.split(' ');
  if(o&&Object.hasOwn(o,'appearance'))fields.push('appearance');
  if(!o||Object.keys(o).sort().join('|')!==fields.sort().join('|')||JSON.stringify(o).length>16384)throw new Error('INVALID_VIEW_OBSERVATION');
  validateViewBinding(o.binding,'INVALID_VIEW_OBSERVATION');
  if(Object.hasOwn(o,'appearance'))validateAppearance(o.appearance,'INVALID_VIEW_OBSERVATION');
  if(!Number.isInteger(o.previewFrameIndex)||!Number.isInteger(o.previewFrameCount)||o.previewFrameCount<1||o.previewFrameCount>200||o.previewFrameIndex<0||o.previewFrameIndex>=o.previewFrameCount||!Number.isInteger(o.sourceFrameCount)||o.sourceFrameCount<1||o.sourceFrameCount>1000000)throw new Error('INVALID_VIEW_OBSERVATION');
  if(![o.ready,o.playing,o.buffering].every(v=>typeof v==='boolean')||!(o.sourceFrameIndex===null||Number.isSafeInteger(o.sourceFrameIndex)&&o.sourceFrameIndex>=0)||!(o.timePs===null||Number.isFinite(o.timePs)&&o.timePs>=0)||!Array.isArray(o.selection)||o.selection.length>512||!o.selection.every(i=>Number.isSafeInteger(i)&&i>=0)||!['cartoon','sticks','spheres','lines'].includes(o.representation))throw new Error('INVALID_VIEW_OBSERVATION');
  return o;
}
export class ViewRuntime{
  constructor(ctx,projects,config){Object.assign(this,{ctx,projects,config});this.views=new Map();this.pending=new Map();}
  async guard(sessionId,action,signal){const agent=this.ctx.agents.get(sessionId);const required=[...this.config.toolNames,...(displayToolNames.includes(action)?[action]:[])];const check=()=>{if(!agent||this.ctx.agents.get(sessionId)!==agent||agent.session?.id!==sessionId)throw new Error('BIO_SESSION_ACCESS_DENIED');const scoped=agent.ctx.tools.schemas(agent);if(!required.every(n=>scoped.some(t=>t.name===n)))throw new Error('BIO_SESSION_ACCESS_DENIED');if(signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');};check();await this.projects.guard(action,sessionId,{},signal);check();return check;}
  heartbeat(sessionId,viewportId,clientId,observation){validateObservation(observation);if(observation.binding.sessionId!==sessionId||observation.binding.viewportId!==viewportId||typeof clientId!=='string'||clientId.length>128)throw new Error('INVALID_VIEW_OBSERVATION');
    const key=clientKey(sessionId,clientId);const old=this.views.get(key);if(old&&old.viewportId!==viewportId)throw new Error('VIEW_BINDING_CHANGED');if(old&&(observation.binding.sourceRevision<old.observation.binding.sourceRevision||observation.binding.sourceSha256!==old.observation.binding.sourceSha256&&observation.binding.sourceRevision<=old.observation.binding.sourceRevision))throw new Error('VIEW_BINDING_CHANGED');if(this.views.size>=128&&!this.views.has(key))throw new Error('VIEWPORT_LIMIT');this.views.set(key,{sessionId,viewportId,clientId,key,observation,metadata:old?.metadata,at:Date.now()});}
  view(sessionId,viewportId){const views=[...this.views.values()].filter(v=>v.sessionId===sessionId&&(viewportId==='active'||v.viewportId===viewportId)&&Date.now()-v.at<5000);if(views.length===0)throw new Error('VIEWPORT_OFFLINE');if(views.length!==1)throw new Error('VIEWPORT_AMBIGUOUS');return views[0];}
  async checkSource(view,signal){
    if(this.config.verifySource===false)return;
    if(!view.metadata?.source||view.metadata.sourceSha256!==view.observation.binding.sourceSha256)throw new Error('VIEW_SOURCE_UNAVAILABLE');
    const record=await this.projects.get(view.sessionId);
    await this.projects.guard('preview:describe',view.sessionId,{},signal);
    const reply=await record.bridge.control('preview-describe',{source:view.metadata.source},{signal});
    if(!reply.ok)throw new Error(reply.error?.code||'PREVIEW_SOURCE_CHANGED');
    const hash=reply.result.sourceSha256??reply.result.source?.sourceSha256;if(hash!==view.observation.binding.sourceSha256)throw new Error('PREVIEW_SOURCE_CHANGED');
  }
  async inspect(sessionId,viewportId,signal){const check=await this.guard(sessionId,'bio_observe',signal);const view=this.view(sessionId,viewportId);check();const result=await this.command(sessionId,view.viewportId,{operationId:crypto.randomUUID(),expected:view.observation.binding,kind:'observe',payload:{}},signal,'bio_observe');return result.observation;}
  async observe(sessionId,viewportId,signal){const observed=await this.inspect(sessionId,viewportId,signal);if(!observed.ready)throw new Error('VIEWPORT_NOT_READY');return observed;}
  async command(sessionId,viewportId,command,signal,capability='bio_view'){validateViewCommand(command);const reading=command.kind==='observe';const check=await this.guard(sessionId,capability,signal);const view=this.view(sessionId,viewportId);
    if(!reading&&!bindingMatches(command.expected,view.observation.binding,command.kind))throw new Error('VIEW_BINDING_CHANGED');if(this.pending.has(view.key))throw new Error('VIEWPORT_BUSY');
    if(!reading&&view.observation.ready){await this.checkSource(view,signal);check();}
    const reply=await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{this.pending.delete(view.key);reject(new Error('VIEWPORT_OFFLINE'));},15000);const stop=()=>{clearTimeout(timeout);this.pending.delete(view.key);reject(new Error('BIO_REQUEST_ABORTED'));};signal?.addEventListener('abort',stop,{once:true});this.pending.set(view.key,{command,delivered:false,resolve:value=>{clearTimeout(timeout);signal?.removeEventListener('abort',stop);resolve(value);},reject:error=>{clearTimeout(timeout);signal?.removeEventListener('abort',stop);reject(error);}});});
    check();validateObservation(reply.observation);if(reply.observation.ready)await this.checkSource(this.view(sessionId,view.viewportId),signal);check();if(reply.operationId!==command.operationId||(!reading&&!reply.observation.ready)||reply.observation.binding.sessionId!==sessionId||reply.observation.binding.viewportId!==view.viewportId)throw new Error('INVALID_VIEW_RECEIPT');
    const expectedHash=command.kind==='openSource'?command.payload.source.sourceSha256:command.expected.sourceSha256;if(!reading&&reply.observation.binding.sourceSha256!==expectedHash)throw new Error('VIEW_BINDING_CHANGED');return reply;
  }
  async dispatch(action,payload,signal){
    const fields={attach:['sessionId','viewportId','clientId','observation'],poll:['sessionId','viewportId','clientId','observation','state'],ack:['sessionId','viewportId','clientId','receipt','state'],detach:['sessionId','viewportId','clientId']};
    if(!fields[action]||!payload||Object.keys(payload).sort().join('|')!==fields[action].slice().sort().join('|')||JSON.stringify(payload).length>32768)throw new Error('INVALID_VIEW_REQUEST');
    const check=await this.guard(payload.sessionId,'view:'+action,signal);
    if(action==='detach'){this.views.delete(clientKey(payload.sessionId,payload.clientId));const pending=this.pending.get(clientKey(payload.sessionId,payload.clientId));this.pending.delete(clientKey(payload.sessionId,payload.clientId));pending?.reject(new Error('VIEWPORT_OFFLINE'));return {};}
    if(action==='ack'){const view=this.view(payload.sessionId,payload.viewportId);if(view.clientId!==payload.clientId)throw new Error('VIEWPORT_AMBIGUOUS');const pending=this.pending.get(clientKey(payload.sessionId,payload.clientId));if(!pending||payload.receipt.operationId!==pending.command.operationId)throw new Error('INVALID_VIEW_RECEIPT');this.pending.delete(clientKey(payload.sessionId,payload.clientId));if(payload.receipt.errorCode)pending.reject(new Error(payload.receipt.errorCode));else{this.heartbeat(payload.sessionId,payload.viewportId,payload.clientId,payload.receipt.observation);if(payload.state){validateViewState(payload.state);if(payload.state.sourceSha256!==payload.receipt.observation.binding.sourceSha256||payload.state.sessionId!==payload.sessionId||payload.state.viewportId!==payload.viewportId)throw new Error('INVALID_VIEW_STATE');this.views.get(view.key).metadata=payload.state;}pending.resolve(payload.receipt);}return {};}
    this.heartbeat(payload.sessionId,payload.viewportId,payload.clientId,payload.observation);check();
    if(action==='poll'&&payload.state&&payload.observation.ready){validateViewState(payload.state);if(payload.state.sessionId!==payload.sessionId||payload.state.viewportId!==payload.viewportId||payload.state.sourceSha256!==payload.observation.binding.sourceSha256)throw new Error('INVALID_VIEW_STATE');this.views.get(clientKey(payload.sessionId,payload.clientId)).metadata=payload.state;}
    let saved=null;if(this.config.persist!==false){const record=await this.projects.get(payload.sessionId);check();const store=new ViewStateStore(record.receiptProject);saved=store.load(payload.sessionId);
      if(action==='poll'&&payload.state&&payload.observation.ready){saved=store.save(payload.sessionId,payload.state,saved?.stateRevision||0);}}
    if(action==='attach')return {restore:saved};const pending=this.pending.get(clientKey(payload.sessionId,payload.clientId));if(pending&&!pending.delivered){pending.delivered=true;return {command:pending.command};}return {command:null};
  }
}
export function installViewRuntime(ctx,projects,config,Compile){
  const runtime=new ViewRuntime(ctx,projects,config);
  for(const name of displayToolNames){const contract=display.tools[name],check=Compile(contract.parameters);ctx.tools.register({name,description:contract.description,parameters:contract.parameters,output:{schema:{},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},execute:async(args,exec)=>{
    if(!check.Check(args)||exec.agent?.session?.id!==exec.agent?.id)throw new Error('INVALID_VIEW_REQUEST');if(name==='bio_observe'){const o=await runtime.inspect(exec.agent.id,args.viewportId,exec.signal);return o.ready?o:{ok:false,errorCode:'VIEWPORT_NOT_READY',observation:o};}return runtime.command(exec.agent.id,args.viewportId,validateViewCommand(JSON.parse(args.commandJson)),exec.signal);
  }});}
  ctx.inject(['connection'],rpcCtx=>rpcCtx.effect(()=>rpcCtx.connection.rpc.handle('/bio-view',async(action,payload,signal)=>{
    try{return {ok:true,value:await runtime.dispatch(action,payload,signal)};}catch(error){return {ok:false,error:{code:'bad-request',details:{issues:[]},message:/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'VIEW_OPERATION_FAILED'}};}
  },{authority:'trusted-host'})));return runtime;
}
