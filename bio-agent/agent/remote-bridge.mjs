import {createHash} from 'node:crypto';
import {readFileSync,lstatSync,realpathSync} from 'node:fs';
import {isAbsolute,join,normalize} from 'node:path';

const fields=['schemaVersion','hostAlias','configFile','knownHosts','projectsRoot','releaseRoot','python'];
const quote=value=>"'"+value.replaceAll("'","'\\''")+"'";
const absolute=value=>typeof value==='string'&&isAbsolute(value)&&normalize(value)===value&&!/[\x00-\x1f\x7f]/.test(value)&&value!=='/';

export function remoteConnection(value){
  if(!value||Object.keys(value).sort().join('|')!==[...fields].sort().join('|')||value.schemaVersion!=='bio.ssh-connection.v1')throw new Error('BIO_REMOTE_CONFIG_INVALID');
  if(typeof value.hostAlias!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.hostAlias))throw new Error('BIO_REMOTE_ALIAS_INVALID');
  for(const key of fields.filter(key=>!['schemaVersion','hostAlias'].includes(key)))if(!absolute(value[key]))throw new Error('BIO_REMOTE_PATH_INVALID');
  const pinned={};
  for(const key of ['configFile','knownHosts']){
    const path=value[key],stat=lstatSync(path);
    if(!stat.isFile()||stat.isSymbolicLink()||(key==='configFile'&&(stat.mode&0o077)!==0)||(key==='knownHosts'&&(stat.mode&0o022)!==0))throw new Error('BIO_REMOTE_AUTH_FILE_INVALID');
    if(typeof process.getuid==='function'&&![0,process.getuid()].includes(stat.uid))throw new Error('BIO_REMOTE_AUTH_FILE_INVALID');
    if(stat.size<1||stat.size>1024*1024)throw new Error('BIO_REMOTE_AUTH_FILE_INVALID');
    pinned[key]={path:realpathSync(path),sha256:createHash('sha256').update(readFileSync(path)).digest('hex')};
  }
  const identity=createHash('sha256').update(JSON.stringify({connection:value,pinned})).digest('hex');
  return Object.freeze({...value,identity});
}

export function loadRemoteConnection(path){
  if(!absolute(path)||lstatSync(path).isSymbolicLink())throw new Error('BIO_REMOTE_CONFIG_INVALID');
  return remoteConnection(JSON.parse(readFileSync(path,'utf8')));
}

export function remoteBridgeCommand(connection,project,executionTarget){
  const checked=remoteConnection(Object.fromEntries(fields.map(key=>[key,connection[key]])));
  if(connection.identity!==checked.identity)throw new Error('BIO_REMOTE_BINDING_CHANGED');
  if(!absolute(project)||!project.startsWith(checked.projectsRoot+'/')||!/^[0-9a-f]{64}$/.test(project.slice(checked.projectsRoot.length+1)))throw new Error('BIO_REMOTE_PROJECT_INVALID');
  const argv=[checked.python,'-I','-B',join(checked.releaseRoot,'run_biobridge.py'),'--project',project,'--python',checked.python];
  if(executionTarget)argv.push('--execution-target',JSON.stringify(executionTarget));
  const command=argv.map(quote).join(' ');
  return {executable:'/usr/bin/ssh',args:['-F',checked.configFile,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
    '-o','UserKnownHostsFile='+checked.knownHosts,'-o','GlobalKnownHostsFile=/dev/null','-o','ForwardAgent=no',
    '-o','IdentityAgent=none','-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','--',checked.hostAlias,command]};
}
