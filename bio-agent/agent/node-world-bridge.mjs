import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {dirname,isAbsolute,join,normalize} from 'node:path';
import {validateTarget} from './execution-target.mjs';

const absolute=value=>typeof value==='string'&&isAbsolute(value)&&normalize(value)===value&&!/[\x00-\x1f\x7f]/.test(value)&&value!=='/';

export function nodeConnection(runtime,config){
  if(config.mode!==undefined&&!['node-world','node-ssh'].includes(config.mode))throw new Error('BIO_REMOTE_MODE_INVALID');
  if(runtime?.constructor?.name!=='RemoteSubprocessRuntime'||typeof runtime.spawn!=='function'||!runtime.world?.config?.url)throw new Error('BIO_REMOTE_NODE_REQUIRED');
  let url;try{url=new URL(runtime.world.config.url);}catch{throw new Error('BIO_NODE_BINDING_INVALID');}
  const expected='/v1/world/'+config.nodeId;
  if(!['ws:','wss:'].includes(url.protocol)||!/^n_[A-Za-z0-9_-]{1,100}$/.test(config.nodeId)||url.pathname!==expected||url.username||url.password||url.search||url.hash)throw new Error('BIO_NODE_BINDING_INVALID');
  const connection={transport:'node-world',nodeId:config.nodeId,hostAlias:config.nodeId,
    projectsRoot:config.computeProjectsRoot,releaseRoot:config.computeReleaseRoot,python:config.computePython};
  for(const name of ['projectsRoot','releaseRoot','python'])if(!absolute(connection[name]))throw new Error('BIO_NODE_PATH_INVALID');
  if(config.mode==='node-ssh'){
    for(const name of ['gatewayPython','gatewayReleaseRoot','gatewayConnectionFile'])if(!absolute(config[name]))throw new Error('BIO_NODE_GATEWAY_PATH_INVALID');
    Object.assign(connection,{transport:'node-ssh',gatewayPython:config.gatewayPython,
      gatewayReleaseRoot:config.gatewayReleaseRoot,gatewayConnectionFile:config.gatewayConnectionFile});
  }
  // Per-instance world tokens rotate normally; node identity and paths must remain bound.
  const identity=createHash('sha256').update(JSON.stringify({connection,url:url.href})).digest('hex');
  return Object.freeze({...connection,identity});
}

export function nodeBridgeChild(runtime,connection,project,executionTarget){
  const checked=nodeConnection(runtime,{nodeId:connection.nodeId,computeProjectsRoot:connection.projectsRoot,
    computeReleaseRoot:connection.releaseRoot,computePython:connection.python,
    ...(connection.transport==='node-ssh'?{mode:'node-ssh',gatewayPython:connection.gatewayPython,
      gatewayReleaseRoot:connection.gatewayReleaseRoot,gatewayConnectionFile:connection.gatewayConnectionFile}:{})});
  if(checked.identity!==connection.identity)throw new Error('BIO_NODE_BINDING_CHANGED');
  if(executionTarget){
    validateTarget(executionTarget);
    if(executionTarget.nodeId!==connection.nodeId||['python','releaseRoot','projectsRoot'].some(key=>executionTarget[key]!==connection[key]))throw new Error('BIO_EXECUTION_TARGET_CONFIG_MISMATCH');
  }
  if(!absolute(project)||!project.startsWith(connection.projectsRoot+'/')||!/^[0-9a-f]{64}$/.test(project.slice(connection.projectsRoot.length+1)))throw new Error('BIO_REMOTE_PROJECT_INVALID');
  let argv=[connection.python,'-I','-B',join(connection.releaseRoot,'run_biobridge.py'),'--project',project,'--python',connection.python];
  if(connection.transport==='node-ssh'){
    if(!executionTarget)throw new Error('BIO_EXECUTION_TARGET_REQUIRED');
    argv=[connection.gatewayPython,'-I','-B',join(connection.gatewayReleaseRoot,'run_biogateway.py'),
      '--connection',connection.gatewayConnectionFile,'--project',project];
  }
  if(executionTarget)argv.push('--execution-target',JSON.stringify(executionTarget));
  const python=connection.gatewayPython||connection.python;
  const handle=runtime.spawn({argv,cwd:connection.gatewayReleaseRoot||connection.projectsRoot,
    env:{PATH:dirname(python)+':/usr/bin:/bin'},stdio:{stdin:'pipe',stdout:'pipe',stderr:'pipe'},graceMs:3000});
  if(!handle?.stdin||!handle.stdout||!handle.stderr||typeof handle.done?.then!=='function'||typeof handle.terminate!=='function')throw new Error('BIO_NODE_HANDLE_INVALID');
  const child=new EventEmitter();Object.assign(child,{stdin:handle.stdin,stdout:handle.stdout,stderr:handle.stderr,
    kill:()=>handle.terminate()});
  handle.done.then(result=>child.emit('exit',result.exitCode,result.signal),()=>child.emit('error',new Error('BIO_NODE_CONNECTION_LOST')));
  return child;
}
