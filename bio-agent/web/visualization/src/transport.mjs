import {previewLimits as L} from '../../runtime/visualization/contracts.mjs';
import {manifestDigest,validateTransportBinding} from '../../runtime/visualization/direct-contracts.mjs';
export const sha256=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
const canonical=value=>JSON.stringify(value,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export function createPreviewTransport({sessionId,viewportId,clientId=crypto.randomUUID(),fetch:request=globalThis.fetch,binaryProvider,onTransport=()=>{}}){
  const cache=new Map(),pending=new Map(),waiters=[],authorizations=new WeakMap();let used=0,active=0,closed=false,provider=binaryProvider,generation=0;const abort=new AbortController();
  if(binaryProvider&&binaryProvider.mode!=='direct')throw new Error('DIRECT_CONFIG_INVALID');
  const headers={'x-bio-session':sessionId,'x-bio-viewport':viewportId};
  const alive=signal=>{if(closed||signal?.aborted||abort.signal.aborted)throw new Error('BIO_REQUEST_ABORTED');};
  const combined=signal=>signal?AbortSignal.any([signal,abort.signal]):abort.signal;
  async function slot(signal){alive(signal);if(active>=2)await new Promise((resolve,reject)=>{const item={resolve,reject};const cancel=()=>{const i=waiters.indexOf(item);if(i>=0){waiters.splice(i,1);reject(new Error('BIO_REQUEST_ABORTED'));}};signal?.addEventListener('abort',cancel,{once:true});item.cleanup=()=>signal?.removeEventListener('abort',cancel);waiters.push(item);});else active++;try{alive(signal);}catch(error){release();throw error;}}
  function release(){const next=waiters.shift();if(next){next.cleanup?.();next.resolve();}else active--;}
  async function api(action,args={},signal){alive(signal);const reply=await request('/v1/bio/preview/'+action,{method:'POST',credentials:'same-origin',redirect:'error',headers:{...headers,'content-type':'application/json'},body:JSON.stringify(args),signal:combined(signal)});
    if(!reply.ok){let code;try{const value=await reply.json();code=value.error||value.detail;}catch{}throw new Error(/^[A-Z][A-Z0-9_]{0,95}$/.test(code)?code:'PREVIEW_NOT_ENABLED');}alive(signal);const value=await reply.json();alive(signal);return value;}
  api.closeChannel=args=>request('/v1/bio/preview/direct-close',{method:'POST',credentials:'same-origin',redirect:'error',headers:{...headers,'content-type':'application/json'},body:JSON.stringify(args),signal:AbortSignal.timeout(8000)}).catch(()=>{});
  api.cancelOperation=args=>request('/v1/bio/preview/cancel',{method:'POST',credentials:'same-origin',redirect:'error',headers:{...headers,'content-type':'application/json'},body:JSON.stringify(args),signal:AbortSignal.timeout(8000)}).catch(()=>{});
  async function authorizeFile(manifest,signal){try{const digest=await manifestDigest(manifest),description=await api('describe',{source:manifest.source},signal);alive(signal);
    if(description.directEnabled!==true)throw new Error('DIRECT_NOT_CONFIGURED');
    if(canonical(description.source)!==canonical(manifest.source))throw new Error('PREVIEW_SOURCE_CHANGED');validateTransportBinding(description.transportBinding);
    const value={binding:description.transportBinding,digest,directEnabled:description.directEnabled===true,generation};authorizations.set(manifest,value);return value;
    }catch(error){authorizations.delete(manifest);cache.clear();used=0;throw error;}
  }
  const keyFor=(manifest,entry,auth)=>canonical([auth.binding.ownerBinding,auth.binding.sessionEpoch,clientId,auth.binding.executionTargetSha256,manifest.source,auth.digest,entry.fileId,entry.sha256]);
  async function getFile(manifest,fileId,signal,priority='foreground'){
    alive(signal);const entry=manifest.files.find(f=>f.fileId===fileId);if(!entry||!Number.isSafeInteger(entry.byteSize)||entry.byteSize<1||entry.byteSize>L.cacheBytes)throw new Error('ARTIFACT_CHANGED');
    if(!provider)throw new Error('CONNECTOR_UNAVAILABLE');
    let auth=await authorizeFile(manifest,signal);alive(signal);const key=keyFor(manifest,entry,auth);
    if(cache.has(key)){const bytes=cache.get(key);cache.delete(key);cache.set(key,bytes);return bytes;}
    let task=pending.get(key);
    if(!task||task.signal?.aborted){task={signal};pending.set(key,task);task.promise=(async()=>{
      const epoch=generation,originalAuth=auth,chosen=provider;
      const bytes=new Uint8Array(entry.byteSize);let offset=0;
      while(offset<entry.byteSize){const limit=Math.min(L.httpTargetBytes,entry.byteSize-offset);await slot(combined(signal));
        try{const response=await chosen.readChunk({manifest,fileId,offset,limit,signal:combined(signal),priority,transportBinding:auth.binding});if(!response.ok)throw new Error('ARTIFACT_CHANGED');const reader=response.body.getReader();let size=0;
          try{while(true){const {value,done}=await reader.read();if(done)break;if(size+value.length>limit)throw new Error('ARTIFACT_CHANGED');bytes.set(value,offset+size);size+=value.length;}}
          finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
          alive(signal);if(epoch!==generation)throw new Error('BIO_REQUEST_ABORTED');
          if(size!==limit||response.headers.get('x-bio-source-sha256')!==manifest.source.sourceSha256||response.headers.get('x-bio-file-sha256')!==entry.sha256||response.headers.get('x-bio-manifest-sha256')!==auth.digest||Number(response.headers.get('x-bio-next-offset'))!==offset+size)throw new Error('ARTIFACT_CHANGED');offset+=size;
        }finally{release();}
      }
      if(await sha256(bytes)!==entry.sha256)throw new Error('ARTIFACT_CHANGED');alive(signal);if(epoch!==generation)throw new Error('BIO_REQUEST_ABORTED');
      const checked=await authorizeFile(manifest,signal);if(canonical(checked.binding)!==canonical(originalAuth.binding))throw new Error('DIRECT_BINDING_CHANGED');
      while(used+bytes.length>L.cacheBytes&&cache.size){const oldest=cache.keys().next().value;used-=cache.get(oldest).length;cache.delete(oldest);}cache.set(key,bytes);used+=bytes.length;onTransport({mode:'direct',reasonCode:null});return bytes;
    })();}
    try{const bytes=await task.promise;alive(signal);return bytes;}finally{if(pending.get(key)===task)pending.delete(key);}
  }
  return {api,getFile,authorizeFile,beginView:authorizeFile,clientId,
    hasFile(manifest,fileId){const auth=authorizations.get(manifest),entry=manifest.files.find(f=>f.fileId===fileId);return !!(auth&&auth.generation===generation&&entry&&cache.has(keyFor(manifest,entry,auth)));},
    setProvider(value){if(value&&value.mode!=='direct')throw new Error('DIRECT_CONFIG_INVALID');if(provider!==value)provider?.dispose();provider=value;generation++;cache.clear();pending.clear();used=0;},
    get memoryBytes(){return used;},dispose(){if(closed)return;provider?.dispose();closed=true;abort.abort();generation++;cache.clear();pending.clear();used=0;while(waiters.length){const v=waiters.shift();v.cleanup?.();v.reject(new Error('BIO_REQUEST_ABORTED'));}}};
}
