import {createHash} from 'node:crypto';

const fields={context:[],inputs:[],capabilities:[],list:['offset'],get:['workflowId'],progress:['workflowId','revision'],review:['workflowId','revision'],
  approve:['workflowId','revision','reviewSha256'],submit:['workflowId','revision'],cancel:['workflowId'],recover:['workflowId'],
  'upload-begin':['name','byteSize','sha256'],'upload-chunk':['uploadId','offset','data'],'upload-finish':['uploadId'],
  artifacts:['workflowId','stageId','offset'], 'download-begin':['workflowId','stageId','path','sha256'],
  'download-chunk':['downloadId','offset','limit'],'download-finish':['downloadId']};
fields['resource-preference']=['execution','cpuThreads'];

export class HumanWorkflowApi{
  constructor(ctx,projects,{tenantId,toolNames}){this.ctx=ctx;this.projects=projects;this.tenantId=tenantId;this.toolNames=toolNames;this.tails=new Map();}
  async dispatch(action,payload,signal=new AbortController().signal){
    if(!Object.hasOwn(fields,action)||!payload||typeof payload!=='object'||Array.isArray(payload)
      ||Object.keys(payload).sort().join('|')!==['sessionId',...fields[action]].sort().join('|')
      ||typeof payload.sessionId!=='string'||payload.sessionId.length<1||payload.sessionId.length>256
      ||Buffer.byteLength(JSON.stringify(payload))>128*1024)throw new Error('BIO_HUMAN_REQUEST_INVALID');
    const agent=this.ctx.agents.get(payload.sessionId);
    if(!agent||agent.session?.id!==payload.sessionId||!this.toolNames.every(name=>agent.ctx.tools.schemas(agent).some(tool=>tool.name===name)))throw new Error('BIO_SESSION_ACCESS_DENIED');
    const args=Object.fromEntries(fields[action].map(name=>[name,payload[name]]));
    const run=async()=>{
      if(signal.aborted)throw new Error('BIO_REQUEST_ABORTED');
      // Progress reads the same scoped workflow as get, with a smaller reply.
      await this.projects.guard('human:'+(action==='progress'?'get':action),payload.sessionId,args,signal);
      const record=await this.projects.get(payload.sessionId);
      if(signal.aborted)throw new Error('BIO_REQUEST_ABORTED');
      if(this.ctx.agents.get(payload.sessionId)!==agent||!this.toolNames.every(name=>agent.ctx.tools.schemas(agent).some(tool=>tool.name===name)))throw new Error('BIO_SESSION_ACCESS_DENIED');
      const host=(name,value)=>record.bridge.control(name,value,{signal});
      const model=(name,value)=>record.tools.get(name).execute('human-web',value,signal).then(result=>result.details);
      let reply;
      if(action==='context')reply=await host('target',{});
      else if(action==='inputs')reply=await model('bio_inputs',{offset:0,limit:100});
      else if(action==='capabilities')reply=await model('bio_capabilities',{});
      else if(action==='list')reply=await model('bio_list',{offset:args.offset,limit:100});
      else if(action==='get')reply=await model('bio_get',args);
      else if(action==='progress'){
        const bridge=record.bridge,project=record.project;
        const assertContext=()=>{
          if(signal.aborted)throw new Error('BIO_REQUEST_ABORTED');
          if(this.ctx.agents.get(payload.sessionId)!==agent||agent.session?.id!==payload.sessionId
            ||!this.toolNames.every(name=>agent.ctx.tools.schemas(agent).some(tool=>tool.name===name)))throw new Error('BIO_SESSION_ACCESS_DENIED');
          if(this.projects.records?.get(payload.sessionId)!==record
            ||record.bridge!==bridge||record.project!==project)throw new Error('BIO_SESSION_ACCESS_DENIED');
        };
        const check=async()=>{assertContext();await this.projects.guard('human:get',payload.sessionId,args,signal);assertContext();};
        await check();
        reply=await host('progress',args);
        await check();
      }
      else if(action==='submit')reply=await model('bio_submit',args);
      else if(action==='cancel')reply=await model('bio_cancel',args);
      else if(action==='artifacts')reply=await model('bio_artifacts',{...args,limit:100});
      else if(action==='approve')reply=await host('approve',{...args,actor:'web:'+(this.tenantId.length<=124?this.tenantId:createHash('sha256').update(this.tenantId).digest('hex'))});
      else if(action==='recover'){
        reply=await host('recover',args);
        if(reply.ok){
          record.uncertain?.delete(args.workflowId);
          if(['READY','RUNNING','COMPLETED'].includes(reply.result?.state)){
            const resumed=await host('resume',args);
            if(resumed.ok)reply=resumed;
            else if(!['SUBMISSION_SOURCE_REQUIRED','RECOVERY_NEEDS_EXPLICIT_SUBMIT'].includes(resumed.error?.code))reply=resumed;
            else reply={...reply,result:{...reply.result,explicitSubmitRequired:reply.result.state==='READY'}};
          }
        }
      }else reply=await host(action,args);
      if(reply.ok&&reply.result?.fullResult){
        const full=reply.result.fullResult;
        const name=full.path?.split('/').at(-1);
        if(!/^[0-9a-f]{64}\.json$/.test(name)||full.byteSize>8*1024*1024)throw new Error('BIO_FULL_RECORD_INVALID');
        const chunks=[];let offset=0;
        while(offset<full.byteSize){
          const part=await host('record-chunk',{recordId:name,offset,limit:65536});
          if(!part.ok||part.result.offset!==offset||part.result.nextOffset<=offset)throw new Error('BIO_FULL_RECORD_UNAVAILABLE');
          const chunk=Buffer.from(part.result.data,'base64');
          if(part.result.nextOffset!==offset+chunk.length)throw new Error('BIO_FULL_RECORD_INVALID');
          chunks.push(chunk);offset=part.result.nextOffset;
        }
        const bytes=Buffer.concat(chunks);
        if(bytes.length!==full.byteSize||createHash('sha256').update(bytes).digest('hex')!==full.sha256)throw new Error('BIO_FULL_RECORD_CHANGED');
        reply=JSON.parse(bytes.toString('utf8'));
      }
      if(!reply.ok)throw new Error(reply.error?.code||'BIO_OPERATION_FAILED');
      if(action==='resource-preference')record.resourcePreference=reply.result;
      if(action==='context'&&reply.result.resourcePreference)record.resourcePreference=reply.result.resourcePreference;
      return reply.result;
    };
    const previous=this.tails.get(payload.sessionId)||Promise.resolve();
    const next=previous.catch(()=>{}).then(run);
    this.tails.set(payload.sessionId,next);
    try{return await next;}finally{if(this.tails.get(payload.sessionId)===next)this.tails.delete(payload.sessionId);}
  }
}

export function installHumanApi(ctx,projects,config){
  const api=new HumanWorkflowApi(ctx,projects,config);
  ctx.effect(()=>ctx.connection.rpc.handle('/bio-workflow',async(action,payload,signal)=>{
    try{return {ok:true,value:await api.dispatch(action,payload,signal)};}
    catch(error){return {ok:false,error:{code:'bad-request',details:{issues:[]},message:/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'BIO_OPERATION_FAILED'}};}
  },{authority:'trusted-host'}));
  return api;
}
