import {targetHash} from '../../agent/execution-target.mjs';

const binding=target=>({tenantId:target.tenantId,nodeId:target.nodeId,executionTargetSha256:targetHash(target)});

function worldAccess(runtime,target){
  const config=runtime?.world?.config;
  let world;
  try{world=new URL(config?.url);}catch{throw new Error('BIO_NODE_BINDING_INVALID');}
  if(runtime?.constructor?.name!=='RemoteSubprocessRuntime'||!['ws:','wss:'].includes(world.protocol)
    ||world.pathname!=='/v1/world/'+target.nodeId||world.username||world.password||world.search||world.hash
    ||typeof config.token!=='string'||!/^mdw_[A-Za-z0-9_-]{16,128}$/.test(config.token))throw new Error('BIO_NODE_BINDING_INVALID');
  world.protocol=world.protocol==='wss:'?'https:':'http:';world.pathname='/v1/bio/access';
  return {url:world.href,token:config.token};
}

async function boundedDecision(response){
  if(!response.ok||!response.body?.getReader)throw new Error('BIO_PLATFORM_ACCESS_DENIED');
  const reader=response.body.getReader(),chunks=[];let size=0;
  try{
    while(true){
      const {done,value}=await reader.read();if(done)break;
      size+=value.byteLength;if(size>32768)throw new Error('BIO_PLATFORM_DECISION_INVALID');
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}

// This adapter is loaded by the operator-owned preset, never exposed as a model tool.
export function makeBioGate({runtime,executionTarget,fetch:request=globalThis.fetch}){
  const expected=binding(executionTarget),origin=worldAccess(runtime,executionTarget).url;
  return {authorize:async(value,signal)=>{
    if(signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');
    if(value?.schemaVersion!=='bio.platform-access.v1'||Object.entries(expected).some(([key,item])=>value[key]!==item))throw new Error('BIO_PLATFORM_ACCESS_DENIED');
    const current=worldAccess(runtime,executionTarget);
    if(current.url!==origin)throw new Error('BIO_NODE_BINDING_CHANGED');
    try{
      const response=await request(current.url,{method:'POST',redirect:'error',
        headers:{'content-type':'application/json','x-world-token':current.token},body:JSON.stringify(value),
        signal:signal?AbortSignal.any([signal,AbortSignal.timeout(8000)]):AbortSignal.timeout(8000)});
      const decision=await boundedDecision(response);
      if(decision.allowed!==true||Object.entries(value).some(([key,item])=>key!=='schemaVersion'&&decision[key]!==item))throw new Error('BIO_PLATFORM_ACCESS_DENIED');
      return decision;
    }catch{
      throw new Error(signal?.aborted?'BIO_REQUEST_ABORTED':'BIO_PLATFORM_ACCESS_DENIED');
    }
  }};
}

export function createBioGate({ctx,executionTarget}){
  return makeBioGate({runtime:ctx.subprocess,executionTarget});
}
