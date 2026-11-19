import {readFileSync,lstatSync,realpathSync} from 'node:fs';
import {isAbsolute} from 'node:path';
import {execFileSync} from 'node:child_process';
import {remoteConnection} from '../agent/remote-bridge.mjs';

const connectionFields=['schemaVersion','hostAlias','configFile','knownHosts','projectsRoot','releaseRoot','python'];
const fail=()=>{throw new Error('DIRECT_CONFIG_INVALID');};
const keys=(v,list)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')==[...list].sort().join('|');
export function checkProfile(profile){
  try{
    const current=remoteConnection(Object.fromEntries(connectionFields.map(k=>[k,profile.connection[k]])));
    if(!profile.connection.identity||current.identity!==profile.connection.identity)throw new Error();
  }catch{throw new Error('DIRECT_BINDING_CHANGED');}
}
export function loadConnectorConfig(path){
  try{
    if(!isAbsolute(path)||realpathSync(path)!==path)fail();
    const stat=lstatSync(path);if(!stat.isFile()||stat.size>32768||(stat.mode&0o077)||![0,process.getuid()].includes(stat.uid))fail();
    const value=JSON.parse(readFileSync(path,'utf8'));
    if(!keys(value,['schemaVersion','allowedOrigin','port','targets'])||value.schemaVersion!=='bio.local-connector.v1')fail();
    const origin=new URL(value.allowedOrigin);
    if(origin.origin!==value.allowedOrigin||origin.username||origin.password||!(origin.protocol==='https:'||origin.protocol==='http:'&&origin.hostname==='127.0.0.1')||!Number.isInteger(value.port)||value.port<1||value.port>65535||!Array.isArray(value.targets)||value.targets.length<1||value.targets.length>64)fail();
    const seen=new Set();const targets=value.targets.map(p=>{
      if(!keys(p,['targetId','connection','hostKeyAlias'])||!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(p.targetId)||p.hostKeyAlias!==p.targetId||seen.has(p.targetId))fail();seen.add(p.targetId);
      const connection=remoteConnection(p.connection);
      execFileSync('/usr/bin/ssh-keygen',['-F',p.hostKeyAlias,'-f',connection.knownHosts],{stdio:['ignore','pipe','pipe'],maxBuffer:1024*1024});
      return Object.freeze({...p,connection});
    });
    return Object.freeze({...value,targets:Object.freeze(targets)});
  }catch{fail();}
}
