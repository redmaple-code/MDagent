import {loadCompiler} from '../../deploy/compiler.mjs';
import {installBio} from './bio-runtime.mjs';
import {loadRemoteConnection} from '../../agent/remote-bridge.mjs';
import {BridgeClient} from '../../agent/bridge-client.mjs';
import {nodeConnection} from '../../agent/node-world-bridge.mjs';
import {loadTarget} from '../../agent/execution-target.mjs';
import {loadExecutionGate} from './execution-gate.mjs';

export async function apply(ctx,config){
  const Compile=await loadCompiler(config.compilerRoot);
  const executionTarget=loadTarget(config.executionTargetFile);
  if(config.mode!=='ssh'&&config.gateMode==='bio-grant-only')throw new Error('BIO_NODE_PLATFORM_GATE_REQUIRED');
  const executionGate=await loadExecutionGate(ctx,{...config,executionTarget});
  const paths={computeProjectsRoot:executionTarget.projectsRoot,computeReleaseRoot:executionTarget.releaseRoot,computePython:executionTarget.python};
  for(const key of Object.keys(paths))if(config[key]&&config[key]!==paths[key])throw new Error('BIO_EXECUTION_TARGET_CONFIG_MISMATCH');
  if(config.mode==='ssh'){
    const remote=loadRemoteConnection(config.connectionFile);
    if(remote.python!==executionTarget.python||remote.projectsRoot!==executionTarget.projectsRoot||remote.releaseRoot!==executionTarget.releaseRoot)throw new Error('BIO_EXECUTION_TARGET_CONFIG_MISMATCH');
    return installBio(ctx,{...config,python:remote.python,remote,executionTarget,executionGate},Compile);
  }
  if(!['node-world','node-ssh'].includes(config.mode)||config.nodeId!==executionTarget.nodeId)throw new Error('BIO_REMOTE_MODE_OR_NODE_INVALID');
  const remote=nodeConnection(ctx.subprocess,{...config,...paths});
  return installBio(ctx,{...config,python:remote.python,remote,executionTarget,executionGate,
    bridgeFactory:options=>new BridgeClient({...options,remote:undefined,nodeWorld:{runtime:ctx.subprocess,connection:remote}})},Compile);
}
