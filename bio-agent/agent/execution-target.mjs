import {createHash} from 'node:crypto';
import {lstatSync,readFileSync,realpathSync} from 'node:fs';
import {isAbsolute,normalize} from 'node:path';

export function canonical(value){
  const ordered=item=>Array.isArray(item)?item.map(ordered):item&&typeof item==='object'?Object.fromEntries(Object.keys(item).sort().map(key=>[key,ordered(item[key])])):item;
  return JSON.stringify(ordered(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
export const targetHash=target=>createHash('sha256').update(canonical(target)).digest('hex');
const absolute=value=>typeof value==='string'&&value.length<=1024&&isAbsolute(value)&&normalize(value)===value&&value!=='/'&&!/[\x00-\x1f\x7f]/.test(value);
export function validateTarget(target){
  const fields=['schemaVersion','tenantId','nodeId','targetId','targetRevision','hostIdentitySha256','uid','python','projectsRoot','releaseRoot','resourcePolicy'];
  if(!target||Object.keys(target).sort().join('|')!==fields.sort().join('|')||target.schemaVersion!=='bio.execution-target.v1')throw new Error('BIO_EXECUTION_TARGET_INVALID');
  for(const key of ['tenantId','nodeId','targetId'])if(typeof target[key]!=='string'||!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(target[key]))throw new Error('BIO_EXECUTION_TARGET_INVALID');
  if(!Number.isInteger(target.targetRevision)||target.targetRevision<1||!Number.isInteger(target.uid)||target.uid<0||!/^[0-9a-f]{64}$/.test(target.hostIdentitySha256))throw new Error('BIO_EXECUTION_TARGET_INVALID');
  for(const key of ['python','projectsRoot','releaseRoot'])if(!absolute(target[key]))throw new Error('BIO_EXECUTION_TARGET_PATH_INVALID');
  const p=target.resourcePolicy;
  if(!p||Object.keys(p).sort().join('|')!==['quotaRoot','cpuThreads','maxConcurrentJobs','gpuDevices','maxOutputBytes'].sort().join('|')||!absolute(p.quotaRoot)||!Number.isInteger(p.cpuThreads)||p.cpuThreads<1||p.cpuThreads>4096||!Number.isInteger(p.maxConcurrentJobs)||p.maxConcurrentJobs<1||p.maxConcurrentJobs>256||!Number.isInteger(p.maxOutputBytes)||p.maxOutputBytes<1048576||p.maxOutputBytes>10995116277760||!Array.isArray(p.gpuDevices)||p.gpuDevices.length>64||new Set(p.gpuDevices).size!==p.gpuDevices.length||p.gpuDevices.some(i=>!Number.isInteger(i)||i<0||i>63))throw new Error('BIO_RESOURCE_POLICY_INVALID');
  return target;
}
export function loadTarget(path){
  if(!absolute(path))throw new Error('BIO_EXECUTION_TARGET_PATH_INVALID');
  let target;
  try{
    const stat=lstatSync(path);
    if(!stat.isFile()||realpathSync(path)!==path||stat.size>65536||(stat.mode&0o022)!==0||typeof process.getuid==='function'&&![0,process.getuid()].includes(stat.uid))throw new Error('BIO_EXECUTION_TARGET_FILE_INVALID');
    target=JSON.parse(readFileSync(path,'utf8'));
  }catch{throw new Error('BIO_EXECUTION_TARGET_FILE_INVALID');}
  validateTarget(target);Object.freeze(target.resourcePolicy.gpuDevices);Object.freeze(target.resourcePolicy);
  return Object.freeze(target);
}
