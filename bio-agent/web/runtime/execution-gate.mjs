import {lstatSync,realpathSync} from 'node:fs';
import {isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
import {targetHash} from '../../agent/execution-target.mjs';

// This service is an operator adapter to the original tenant write gate, never a model tool.
export async function loadExecutionGate(ctx,{executionTarget,gateMode='platform',platformGateModule}){
  if(gateMode==='bio-grant-only')return async()=>{}; // Explicit isolated host policy; Bio still requires a scientific grant.
  if(gateMode!=='platform'||!isAbsolute(platformGateModule||''))throw new Error('BIO_PLATFORM_GATE_REQUIRED');
  const stat=lstatSync(platformGateModule);
  if(!stat.isFile()||realpathSync(platformGateModule)!==platformGateModule||stat.size>1024*1024||(stat.mode&0o022)!==0
    ||typeof process.getuid==='function'&&![0,process.getuid()].includes(stat.uid))throw new Error('BIO_PLATFORM_GATE_MODULE_INVALID');
  const module=await import(pathToFileURL(platformGateModule).href);
  if(typeof module.createBioGate!=='function')throw new Error('BIO_PLATFORM_GATE_MODULE_INVALID');
  const gate=await module.createBioGate({ctx,executionTarget});
  if(typeof gate?.authorize!=='function')throw new Error('BIO_PLATFORM_GATE_MODULE_INVALID');
  const binding={tenantId:executionTarget.tenantId,nodeId:executionTarget.nodeId,executionTargetSha256:targetHash(executionTarget)};
  return async(action,sessionId,arguments_={},signal)=>{
    if(signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');
    const request={schemaVersion:'bio.platform-access.v1',...binding,action,sessionId,
      ...(typeof arguments_.workflowId==='string'?{workflowId:arguments_.workflowId}:{}),
      ...(Number.isInteger(arguments_.revision)?{revision:arguments_.revision}:{})};
    const decision=await gate.authorize(request,signal);
    if(!decision||decision.allowed!==true||Object.entries(request).some(([k,v])=>k!=='schemaVersion'&&decision[k]!==v))throw new Error('BIO_PLATFORM_ACCESS_DENIED');
  };
}
