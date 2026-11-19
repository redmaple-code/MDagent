import {createHash} from 'node:crypto';
import {closeSync,constants,fsyncSync,lstatSync,mkdirSync,openSync,readFileSync,readdirSync,writeSync} from 'node:fs';
import {join,resolve} from 'node:path';

export const effectTools=new Set(['bio_plan','bio_continue','bio_submit']);
export const effectWorkflow=args=>args.workflowId??args.intent?.workflowId;

// Metadata only; sent identities are durable before any effect is sent to Python.
export class ClientReceipts{
  constructor(project){
    const root=resolve(project);mkdirSync(root,{recursive:true});
    for(const part of ['chat','chat/client-requests']){
      const directory=join(root,part);mkdirSync(directory,{recursive:true});
      if(lstatSync(directory).isSymbolicLink()||!lstatSync(directory).isDirectory())throw new Error('CLIENT_RECEIPT_PATH');
    }
    this.directory=join(root,'chat/client-requests');
  }
  path(id){if(typeof id!=='string'||!/^[a-f0-9-]{36}$/.test(id))throw new Error('CLIENT_RECEIPT_ID');return join(this.directory,id+'.jsonl');}
  append(id,event,first=false){
    const descriptor=openSync(this.path(id),constants.O_WRONLY|constants.O_NOFOLLOW|(first?constants.O_CREAT|constants.O_EXCL:constants.O_APPEND),0o600);
    try{const bytes=Buffer.from(JSON.stringify(event)+'\n');let offset=0;while(offset<bytes.length)offset+=writeSync(descriptor,bytes,offset,bytes.length-offset);fsyncSync(descriptor);}finally{closeSync(descriptor);}
  }
  begin(request){
    if(!effectTools.has(request.tool))return;
    this.append(request.requestId,{type:'sent',requestId:request.requestId,tool:request.tool,workflowId:effectWorkflow(request.arguments),
      revision:request.arguments.revision??request.arguments.intent?.revision,
      argumentsSha256:createHash('sha256').update(JSON.stringify(request.arguments)).digest('hex')},true);
  }
  pending(){
    const records=[];
    for(const name of readdirSync(this.directory)){
      if(!/^[a-f0-9-]{36}\.jsonl$/.test(name))continue;
      const path=join(this.directory,name),stat=lstatSync(path);
      if(!stat.isFile()||stat.isSymbolicLink()||stat.size>16384)throw new Error('CLIENT_RECEIPT_INVALID');
      let events;
      try{events=readFileSync(path,'utf8').trim().split('\n').map(line=>JSON.parse(line));}catch{throw new Error('CLIENT_RECEIPT_INVALID');}
      const sent=events[0];if(sent.type!=='sent'||this.path(sent.requestId)!==path)throw new Error('CLIENT_RECEIPT_INVALID');
      const resolved=events.some(event=>['acknowledged','reconciled'].includes(event.type)
        ||(event.type==='received'&&event.ok===false&&event.code!=='TOOL_RESPONSE_UNKNOWN'));
      if(!resolved)records.push(sent);
    }
    return records;
  }
  guard(workflowId){if(this.pending().some(record=>record.workflowId===workflowId))throw new Error('TOOL_RESPONSE_UNKNOWN');}
  received(response){this.append(response.requestId,{type:'received',ok:response.ok,code:response.error?.code});}
  acknowledge(id){this.append(id,{type:'acknowledged'});}
  recover(workflowId){for(const record of this.pending())if(record.workflowId===workflowId)this.append(record.requestId,{type:'reconciled'});}
}
