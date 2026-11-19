import {spawn} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {join} from 'node:path';
import {validateChannel,validatePermit,directLimits as L} from '../web/runtime/visualization/direct-contracts.mjs';
import {checkProfile} from './config.mjs';
import {decodeRecords,encodeRecord} from './ssh-protocol.mjs';

const quote=v=>"'"+v.replaceAll("'","'\\''")+"'";
const fail=code=>{throw new Error(code);};
const keys=(v,list)=>Object.keys(v).sort().join('|')==list.split(' ').sort().join('|');
export function buildReaderCommand(profile){
  const c=profile.connection;
  if(profile.hostKeyAlias!==profile.targetId||!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(profile.targetId))fail('DIRECT_CONFIG_INVALID');
  const command=[c.python,'-I','-B',join(c.releaseRoot,'run_biopreview_direct.py'),'--projects-root',c.projectsRoot,'--python',c.python].map(quote).join(' ');
  return {executable:'/usr/bin/ssh',args:['-F',c.configFile,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
    '-o','UserKnownHostsFile='+c.knownHosts,'-o','GlobalKnownHostsFile=/dev/null','-o','HostKeyAlias='+profile.hostKeyAlias,
    '-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','--',c.hostAlias,command]};
}
export function classifySshFailure(stderr){
  if(/host key|REMOTE HOST IDENTIFICATION|known_hosts/i.test(stderr))return 'SSH_IDENTITY_MISMATCH';
  if(/Permission denied|authentication failed|Too many authentication/i.test(stderr))return 'SSH_AUTH_REQUIRED';
  if(/Connection refused|Network is unreachable|No route to host|Connection timed out|Connection reset by peer|client_loop: send disconnect: Broken pipe/i.test(stderr))return 'SSH_NETWORK_UNAVAILABLE';
  return 'DIRECT_PROTOCOL_INVALID';
}
export function createSshReader({profile,channel,spawnChild=spawn}){
  validateChannel(channel);if(channel.targetId!==profile.targetId)fail('DIRECT_BINDING_CHANGED');
  const identity=c=>JSON.stringify([c.targetId,c.executionTargetSha256,c.projectKey,c.uid,c.hostIdentitySha256,c.transportBinding.ownerBinding,c.transportBinding.sessionEpoch]);
  const pinnedIdentity=identity(channel);
  checkProfile(profile);const command=buildReaderCommand(profile);
  const env={PATH:'/usr/bin:/bin'};for(const k of ['HOME','TMPDIR','LANG','LC_ALL','SSH_AUTH_SOCK'])if(process.env[k])env[k]=process.env[k];
  const child=spawnChild(command.executable,command.args,{shell:false,env,stdio:['pipe','pipe','pipe']});
  let stderr='',closed=false,closing,ready=false,busy=false,exitCode;
  let resolveExit;const exited=new Promise(resolve=>resolveExit=resolve);
  child.stderr.on('data',part=>{stderr=(stderr+String(part)).slice(-4096);});
  child.on('error',()=>{exitCode=-1;resolveExit();child.stdout.destroy();});
  child.on('exit',code=>{exitCode=code;resolveExit();});
  child.stdin.on('error',()=>{});
  const records=decodeRecords(child.stdout)[Symbol.asyncIterator]();
  async function next(signal){
    if(closed||signal?.aborted)fail('BIO_REQUEST_ABORTED');
    const deadline=AbortSignal.timeout(8000),combined=signal?AbortSignal.any([signal,deadline]):deadline;
    let rejectAbort;const aborted=new Promise((_,reject)=>rejectAbort=()=>reject(new Error(signal?.aborted?'BIO_REQUEST_ABORTED':ready?'DIRECT_TRANSFER_TIMEOUT':'SSH_NETWORK_UNAVAILABLE')));
    combined.addEventListener('abort',rejectAbort,{once:true});
    try{
      const value=await Promise.race([records.next(),aborted]);
      if(value.done){
        if(exitCode===undefined){let timer;await Promise.race([exited,new Promise(resolve=>timer=setTimeout(resolve,250))]);clearTimeout(timer);}
        fail(exitCode===255?classifySshFailure(stderr):'DIRECT_PROTOCOL_INVALID');
      }
      return value.value;
    }finally{combined.removeEventListener('abort',rejectAbort);}
  }
  function send(header){if(closed)fail('BIO_REQUEST_ABORTED');child.stdin.write(encodeRecord(header));}
  function response(record,id){
    if(record.header.requestId!==id)fail('DIRECT_PROTOCOL_INVALID');
    if(record.header.kind==='error'){
      if(!keys(record.header,'kind requestId code')||record.binary.length||!/^[A-Z][A-Z0-9_]{0,95}$/.test(record.header.code))fail('DIRECT_PROTOCOL_INVALID');
      fail(record.header.code);
    }
  }
  async function close(){
    if(closing)return closing;closed=true;
    closing=(async()=>{child.stdin.end();const terminate=setTimeout(()=>child.kill('SIGTERM'),150);
      const force=setTimeout(()=>child.kill('SIGKILL'),1500);let stop;
      const deadline=new Promise((_,reject)=>stop=setTimeout(()=>reject(new Error('DIRECT_TRANSFER_TIMEOUT')),2500));
      try{await Promise.race([exited,deadline]);}finally{clearTimeout(terminate);clearTimeout(force);clearTimeout(stop);child.stdout.destroy();child.stderr.destroy();}})();
    return closing;
  }
  return {get closed(){return closed||exitCode!==undefined;},
    async bind(nextChannel,signal){validateChannel(nextChannel);if(identity(nextChannel)!==pinnedIdentity)fail('DIRECT_BINDING_CHANGED');if(busy)fail('DIRECT_SLOT_LIMIT');channel=nextChannel;return this.hello(signal);},
    async hello(signal){
      checkProfile(profile);const id=randomUUID();send({kind:'hello',requestId:id,channel});
      try{const record=await next(signal);response(record,id);const h=record.header;
        if(!keys(h,'kind requestId channelId readerVersion executionTargetSha256 hostIdentitySha256 uid projectKey')||record.binary.length||h.kind!=='hello'||h.channelId!==channel.channelId||h.readerVersion!==channel.readerVersion||h.executionTargetSha256!==channel.executionTargetSha256||h.hostIdentitySha256!==channel.hostIdentitySha256||h.uid!==channel.uid||h.projectKey!==channel.projectKey)fail('SSH_IDENTITY_MISMATCH');
        ready=true;return h;
      }catch(error){await close();throw error;}
    },
    async *read(permit,signal){
      validatePermit(permit);checkProfile(profile);
      if(!ready||busy||permit.channelId!==channel.channelId||permit.previewId!==channel.previewId||permit.sourceSha256!==channel.source.sourceSha256||permit.manifestSha256!==channel.manifestSha256)fail('DIRECT_BINDING_CHANGED');
      busy=true;const id=randomUUID();let offset=permit.offset,complete=false;send({kind:'read',requestId:id,channelId:permit.channelId,handle:permit.handle});
      const abort=()=>{void close().catch(()=>{});};signal?.addEventListener('abort',abort,{once:true});
      try{while(true){
        const record=await next(signal);response(record,id);const h=record.header;
        if(h.kind==='done'){
          if(!keys(h,'kind requestId nextOffset')||record.binary.length||h.nextOffset!==offset||offset!==permit.offset+permit.limit)fail('DIRECT_PROTOCOL_INVALID');
          complete=true;break;
        }
        if(!keys(h,'kind requestId previewId fileId sourceSha256 manifestSha256 fileSha256 byteSize offset nextOffset sha256')||h.kind!=='data'||record.binary.length<1||record.binary.length>L.frameBytes||h.previewId!==permit.previewId||h.fileId!==permit.fileId||h.sourceSha256!==permit.sourceSha256||h.manifestSha256!==permit.manifestSha256||h.fileSha256!==permit.fileSha256||h.byteSize!==permit.byteSize||h.offset!==offset||h.nextOffset!==offset+record.binary.length||h.nextOffset>permit.offset+permit.limit||createHash('sha256').update(record.binary).digest('hex')!==h.sha256)fail('DIRECT_FILE_CHANGED');
        offset=h.nextOffset;yield {...h,data:record.binary};
      }}catch(error){await close();throw error;}finally{signal?.removeEventListener('abort',abort);busy=false;if(!complete)await close();}
    },close};
}
