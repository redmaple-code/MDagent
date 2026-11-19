import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
import {ClientReceipts,effectTools,effectWorkflow} from './client-receipts.mjs';
import {remoteBridgeCommand} from './remote-bridge.mjs';
import {nodeBridgeChild} from './node-world-bridge.mjs';
import {targetHash,validateTarget} from './execution-target.mjs';

export function scientificEnv(python,env=process.env){
  const result={PATH:`${dirname(python)}:/usr/bin:/bin`};
  for(const name of ['HOME','TMPDIR','LANG','LC_ALL','BIO_PREVIEW_PYMOL_PYTHON'])if(typeof env[name]==='string')result[name]=env[name];
  return result;
}

export class BridgeClient{
  #child;#pending=new Map();#ignored=new Set();#buffer=Buffer.alloc(0);#tail=Promise.resolve();#closed=false;#receipts;
  constructor({project,python,remote,nodeWorld,remoteProject,executionTarget,spawnChild=spawn}){
    this.#receipts=new ClientReceipts(project);
    if(executionTarget)validateTarget(executionTarget);
    if(nodeWorld){
      this.#child=nodeBridgeChild(nodeWorld.runtime,nodeWorld.connection,remoteProject,executionTarget);
    }else if(remote){
      const command=remoteBridgeCommand(remote,remoteProject,executionTarget);
      this.#child=spawnChild(command.executable,command.args,{shell:false,env:scientificEnv('/usr/bin/ssh'),stdio:['pipe','pipe','pipe']});
    }else{
      const script=fileURLToPath(new URL('../run_biobridge.py',import.meta.url));
      const args=['-I','-B',script,'--project',project,'--python',python];
      if(executionTarget)args.push('--execution-target',JSON.stringify(executionTarget));
      this.#child=spawnChild(python,args,{shell:false,env:scientificEnv(python),stdio:['pipe','pipe','pipe']});
    }
    this.#child.stdout.on('data',chunk=>this.#read(chunk));
    this.#child.stderr.resume();
    this.#child.on('error',()=>this.#fail('BRIDGE_UNAVAILABLE'));
    this.#child.on('exit',()=>this.#fail('BRIDGE_CLOSED'));
    this.#child.stdin.on('error',()=>this.#fail('BRIDGE_CLOSED'));
    this.ready=(executionTarget?this.control('target',{}).then(response=>{
      if(!response.ok||response.result?.identityVerified!==true||response.result.executionTargetSha256!==targetHash(executionTarget)){
        this.#child.stdin.end();throw new Error('BIO_COMPUTE_IDENTITY_MISMATCH');
      }
    }):Promise.resolve()).then(()=>this.control('reconcile',{advance:false})).then(response=>{
      if(!response.ok){this.#fail('BRIDGE_UNAVAILABLE');throw new Error('BRIDGE_UNAVAILABLE');}
    });
    this.ready.catch(()=>{});
  }
  get closed(){return this.#closed;}
  #fail(code){for(const waiter of this.#pending.values())waiter.reject(new Error(code));this.#pending.clear();this.#closed=true;}
  #read(chunk){
    this.#buffer=Buffer.concat([this.#buffer,chunk]);
    if(this.#buffer.length>128*1024&&!this.#buffer.includes(10)){this.#fail('BRIDGE_RESPONSE_LIMIT');this.#child.stdin.end();return;}
    let index;
    while((index=this.#buffer.indexOf(10))!==-1){
      const line=this.#buffer.subarray(0,index);this.#buffer=this.#buffer.subarray(index+1);
      try{
        if(line.length>128*1024)throw new Error();
        const response=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(line));
        if(response.schemaVersion!=='bio.tool-result.v1'||typeof response.ok!=='boolean'||!Object.hasOwn(response,response.ok?'result':'error'))throw new Error();
        if(this.#ignored.delete(response.requestId))continue;
        const waiter=this.#pending.get(response.requestId);
        if(!waiter||waiter.tool!==response.tool)throw new Error();
        if(waiter.effect)this.#receipts.received(response);
        if(response.ok&&waiter.version==='bio.host.v1'){
          if(['activate','acknowledge'].includes(waiter.tool))this.#receipts.acknowledge(waiter.args.sourceRequestId);
          if(waiter.tool==='recover')this.#receipts.recover(waiter.args.workflowId);
        }
        this.#pending.delete(response.requestId);waiter.resolve(response);
      }catch{this.#fail('BRIDGE_RESPONSE_INVALID');this.#child.stdin.end();return;}
    }
  }
  #request(version,tool,args,{signal}={}){
    const run=()=>new Promise((resolve,reject)=>{
      if(this.#closed||signal?.aborted){reject(new Error(signal?.aborted?'MODEL_ABORTED':'BRIDGE_CLOSED'));return;}
      const requestId=randomUUID();const data=JSON.stringify({schemaVersion:version,requestId,tool,arguments:args})+'\n';
      if(Buffer.byteLength(data)>2*1024*1024){reject(new Error('BRIDGE_REQUEST_LIMIT'));return;}
      const effect=version==='bio.tools.v1'&&effectTools.has(tool);
      if(effect){
        try{this.#receipts.guard(effectWorkflow(args));this.#receipts.begin({requestId,tool,arguments:args});}
        catch(error){reject(new Error(error.message==='TOOL_RESPONSE_UNKNOWN'?error.message:'CLIENT_RECEIPT_FAILED'));return;}
      }
      const abandon=code=>{this.#pending.delete(requestId);this.#ignored.add(requestId);finish();reject(new Error(code));};
      const abort=()=>abandon('TOOL_RESPONSE_UNKNOWN');
      const timer=setTimeout(()=>abandon('TOOL_RESPONSE_UNKNOWN'),120000);
      const finish=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);};
      this.#pending.set(requestId,{tool,args,version,effect,resolve:value=>{finish();resolve(value);},reject:error=>{finish();reject(error);}});
      signal?.addEventListener('abort',abort,{once:true});
      this.#child.stdin.write(data,error=>{if(error)abandon('TOOL_RESPONSE_UNKNOWN');});
    });
    const result=this.#tail.then(run);this.#tail=result.catch(()=>{});return result;
  }
  call(tool,args,options){return this.#request('bio.tools.v1',tool,args,options);}
  control(action,args,options){return this.#request('bio.host.v1',action,args,options);}
  async close(){
    if(this.#closed)return;
    this.#closed=true;this.#child.stdin.end();
    await new Promise(resolve=>{
      const timer=setTimeout(()=>{this.#child.kill('SIGTERM');resolve();},3000);
      this.#child.once('exit',()=>{clearTimeout(timer);resolve();});
    });
  }
}
