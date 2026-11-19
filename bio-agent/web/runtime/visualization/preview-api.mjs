import {validatePreviewRequest,validateSource,previewLimits} from './contracts.mjs';
import {targetHash,canonical} from '../../../agent/execution-target.mjs';
import {createHash,randomUUID} from 'node:crypto';
import {basename} from 'node:path';
import {validateDirectRequest,validateChannel,validatePermit} from './direct-contracts.mjs';
const fields={describe:['source'],prepare:['request'],status:['jobId'],window:['previewId','start','end','stride'],read:['previewId','fileId','offset','limit','sourceSha256'],cancel:['jobId']};
const id=v=>typeof v==='string'&&/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(v);
export class PreviewApi{
  constructor(ctx,projects,config){this.ctx=ctx;this.projects=projects;this.toolNames=config.toolNames;this.directEnabled=config.directEnabled===true;this.epochs=new WeakMap();this.channels=new Map();}
  scope(sessionId,agent){
    if(!agent||this.ctx.agents.get(sessionId)!==agent||agent.session?.id!==sessionId||!this.toolNames.every(n=>agent.ctx.tools.schemas(agent).some(t=>t.name===n)))throw new Error('BIO_SESSION_ACCESS_DENIED');
  }
  async dispatch(action,payload,signal=new AbortController().signal){
    if(action==='read')throw new Error('PREVIEW_RELAY_DISABLED');
    for(const [channelId,entry] of this.channels)if(Date.now()-entry.lastSeenAtMs>=900000||this.ctx.agents.get(entry.actor.sessionId)!==entry.agent){
      this.channels.delete(channelId);void entry.record.bridge.control('preview-direct-close',{channelId,actor:entry.actor},{signal:AbortSignal.timeout(8000)}).catch(()=>{});
    }
    const direct=action.startsWith('direct-');
    const directFields={'direct-open':['previewId','source','manifestSha256','clientId','pairingId'],'direct-permit':['channelId','previewId','fileId','offset','limit','sourceSha256','clientId','pairingId'],'direct-close':['channelId','clientId','pairingId']};
    let names=fields[action]||directFields[action];
    if(names&&Object.hasOwn(payload??{},'operationId')){
      if(!['prepare','window','cancel'].includes(action)||typeof payload.operationId!=='string'||!(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/).test(payload.operationId))throw new Error('INVALID_PREVIEW_OPERATION');
      names=action==='cancel'?['operationId']:[...names,'operationId'];
    }
    if(!names||!payload||Object.keys(payload).sort().join('|')!==['sessionId','viewportId',...names].sort().join('|')||typeof payload.sessionId!=='string'||payload.sessionId.length<1||payload.sessionId.length>256||!id(payload.viewportId)||Buffer.byteLength(JSON.stringify(payload))>32768)throw new Error('INVALID_PREVIEW_REQUEST');
    const args=Object.fromEntries(names.map(k=>[k,payload[k]]));
    if(direct){validateDirectRequest(action,args);if(!this.directEnabled||!this.projects.executionTarget)throw new Error('DIRECT_NOT_CONFIGURED');signal=AbortSignal.any([signal,AbortSignal.timeout(8000)]);}
    if(action==='direct-close'){
      const owned=this.channels.get(args.channelId);
      if(!owned||owned.actor.sessionId!==payload.sessionId||owned.actor.viewportId!==payload.viewportId||owned.actor.clientId!==args.clientId||owned.actor.pairingId!==args.pairingId)throw new Error('DIRECT_ACCESS_DENIED');
      // Revocation still permits terminal cleanup of this exact registered transfer.
      this.channels.delete(args.channelId);
      try{await owned.record.bridge.control('preview-direct-close',{channelId:args.channelId,actor:owned.actor},{signal});}catch{}
      return {closed:true};
    }
    if(action==='describe')validateSource(args.source);
    if(action==='prepare')validatePreviewRequest(args.request);
    if((action==='status'||action==='cancel'&&!args.operationId)&&!id(args.jobId))throw new Error('INVALID_PREVIEW_JOB');
    if(['window','read'].includes(action)&&!id(args.previewId))throw new Error('INVALID_PREVIEW_ID');
    if(action==='read'&&(!id(args.fileId)||!Number.isSafeInteger(args.offset)||args.offset<0||!Number.isSafeInteger(args.limit)||args.limit<1||args.limit>previewLimits.bridgeReadBytes||!(/^[a-f0-9]{64}$/).test(args.sourceSha256)))throw new Error('INVALID_PREVIEW_READ');
    const agent=this.ctx.agents.get(payload.sessionId);this.scope(payload.sessionId,agent);
    const targetValue=this.projects.executionTarget,target=targetValue?targetHash(targetValue):null;
    if(!this.epochs.has(agent))this.epochs.set(agent,randomUUID());
    const transportBinding=targetValue?{ownerBinding:createHash('sha256').update(canonical([targetValue.tenantId,targetValue.nodeId])).digest('hex'),sessionEpoch:this.epochs.get(agent),executionTargetSha256:target}:null;
    const assertContext=()=>{if(signal.aborted)throw new Error(signal.reason?.name==='TimeoutError'?'DIRECT_TRANSFER_TIMEOUT':'BIO_REQUEST_ABORTED');this.scope(payload.sessionId,agent);if(target!==null&&targetHash(this.projects.executionTarget)!==target)throw new Error('BIO_EXECUTION_TARGET_CHANGED');};
    const check=async()=>{
      assertContext();
      await this.projects.guard('preview:'+action,payload.sessionId,args,signal);
      assertContext();
    };
    await check();const record=await this.projects.get(payload.sessionId);await check();
    let entry,hostArgs={...args};delete hostArgs.sourceSha256;
    if(direct){
      const actor={tenantId:targetValue.tenantId,nodeId:targetValue.nodeId,sessionId:payload.sessionId,viewportId:payload.viewportId,agentEpoch:transportBinding.sessionEpoch,clientId:args.clientId,pairingId:args.pairingId,executionTargetSha256:target};
      if(action==='direct-open'){
        if(basename(record.project)!==createHash('sha256').update(payload.sessionId).digest('hex'))throw new Error('DIRECT_BINDING_CHANGED');
        if(this.channels.size>=512)throw new Error('DIRECT_SLOT_LIMIT');
        hostArgs={binding:{...actor,target:targetValue,projectKey:basename(record.project),previewId:args.previewId,source:args.source,manifestSha256:args.manifestSha256,transportBinding}};delete hostArgs.binding.executionTargetSha256;
        entry={agent,record,actor,source:args.source,previewId:args.previewId,manifestSha256:args.manifestSha256,lastSeenAtMs:Date.now()};
      }else{
        entry=this.channels.get(args.channelId);
        if(!entry||entry.agent!==agent||entry.record!==record||canonical(entry.actor)!==canonical(actor)||action==='direct-permit'&&(args.sourceSha256!==entry.source.sourceSha256||args.previewId!==entry.previewId))throw new Error('DIRECT_ACCESS_DENIED');
        entry.lastSeenAtMs=Date.now();hostArgs=action==='direct-close'?{channelId:args.channelId,actor}:{channelId:args.channelId,fileId:args.fileId,offset:args.offset,limit:args.limit,actor};
      }
    }
    const revoke=async channelId=>{if(!channelId||!entry)return;this.channels.delete(channelId);try{await record.bridge.control('preview-direct-close',{channelId,actor:entry.actor},{signal:AbortSignal.timeout(8000)});}catch{}};
    let reply,channelId=args.channelId;
    const pending=record.bridge.control('preview-'+action,hostArgs,{signal});
    try{
      if(direct){
        let rejectAbort;const aborted=new Promise((_,reject)=>{rejectAbort=()=>reject(new Error(signal.reason?.name==='TimeoutError'?'DIRECT_TRANSFER_TIMEOUT':'BIO_REQUEST_ABORTED'));signal.addEventListener('abort',rejectAbort,{once:true});});
        try{reply=await Promise.race([pending,aborted]);}finally{signal.removeEventListener('abort',rejectAbort);}
      }else reply=await pending;
      if(direct&&action==='direct-open'&&reply.ok){channelId=reply.result.channelId;validateChannel(reply.result);
        if(canonical(reply.result.transportBinding)!==canonical(transportBinding)||reply.result.projectKey!==hostArgs.binding.projectKey||reply.result.targetId!==targetValue.targetId||reply.result.uid!==targetValue.uid||reply.result.hostIdentitySha256!==targetValue.hostIdentitySha256||reply.result.clientId!==args.clientId||reply.result.pairingId!==args.pairingId||reply.result.previewId!==args.previewId||canonical(reply.result.source)!==canonical(args.source)||reply.result.manifestSha256!==args.manifestSha256)throw new Error('DIRECT_BINDING_CHANGED');
      }
      await check();if(this.projects.records&&this.projects.records.get(payload.sessionId)!==record)throw new Error('DIRECT_BINDING_CHANGED');
      if(!reply.ok)throw new Error(reply.error?.code||'PREVIEW_OPERATION_FAILED');
      if(action==='read'&&reply.result.sourceSha256!==args.sourceSha256)throw new Error('PREVIEW_SOURCE_CHANGED');
      if(action==='describe')return {...reply.result,transportBinding,directEnabled:this.directEnabled};
      if(action==='direct-open')this.channels.set(channelId,entry);
      if(action==='direct-permit'){validatePermit(reply.result);const p=reply.result;if(p.channelId!==channelId||p.previewId!==args.previewId||p.fileId!==args.fileId||p.offset!==args.offset||p.limit!==args.limit||p.sourceSha256!==args.sourceSha256||p.manifestSha256!==entry.manifestSha256)throw new Error('DIRECT_BINDING_CHANGED');}
      if(action==='direct-close')this.channels.delete(channelId);
      return reply.result;
    }catch(error){if(direct){if(channelId)await revoke(channelId);else void pending.then(value=>value.ok&&revoke(value.result.channelId)).catch(()=>{});}throw error;}
  }
}
export function installPreviewApi(ctx,projects,config){return new PreviewApi(ctx,projects,config);}
