import {manifestDigest,validateChannel,validatePermit} from '../../runtime/visualization/direct-contracts.mjs';
const canonical=value=>JSON.stringify(value,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const same=(a,b)=>canonical(a)===canonical(b);
export function createDirectProvider({api,connector,clientId}){
  const channels=new Map(),abort=new AbortController();let closed=false;
  function alive(signal){if(closed||signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');}
  async function channelFor(manifest,digest,binding,signal){
    const key=digest+':'+binding.sessionEpoch;let task=channels.get(key);
    if(!task){task={tail:Promise.resolve(),promise:api('direct-open',{previewId:manifest.previewId,source:manifest.source,manifestSha256:digest,clientId,pairingId:connector.pairingId},signal).then(c=>{
      validateChannel(c);if(c.clientId!==clientId||c.pairingId!==connector.pairingId||c.manifestSha256!==digest||c.previewId!==manifest.previewId||!same(c.source,manifest.source)||!same(c.transportBinding,binding))throw new Error('DIRECT_BINDING_CHANGED');return c;
    })};channels.set(key,task);task.promise.catch(()=>{if(channels.get(key)===task)channels.delete(key);});}
    const channel=await task.promise;alive(signal);return {channel,task};
  }
  return {mode:'direct',async readChunk({manifest,fileId,offset,limit,signal,priority='foreground',transportBinding}){
    alive(signal);const combined=AbortSignal.any([abort.signal,...(signal?[signal]:[])]),digest=await manifestDigest(manifest),entry=manifest.files.find(f=>f.fileId===fileId);
    const {channel,task}=await channelFor(manifest,digest,transportBinding,combined);
    const preceding=task.tail;let unlock;task.tail=new Promise(r=>unlock=r);
    try{await preceding;alive(combined);
      for(let attempt=0;attempt<2;attempt++){
        let reservation;try{
          reservation=await connector.reserve(channel,priority,combined);alive(combined);
          const permit=await api('direct-permit',{channelId:channel.channelId,previewId:manifest.previewId,fileId,offset,limit,sourceSha256:manifest.source.sourceSha256,clientId,pairingId:connector.pairingId},combined);
          validatePermit(permit);if(permit.channelId!==channel.channelId||permit.previewId!==manifest.previewId||permit.manifestSha256!==digest||permit.sourceSha256!==manifest.source.sourceSha256||permit.fileId!==fileId||permit.fileSha256!==entry.sha256||permit.byteSize!==entry.byteSize||permit.offset!==offset||permit.limit!==limit)throw new Error('DIRECT_BINDING_CHANGED');
          const reply=await connector.read(reservation,permit,combined);if(!reply.ok)throw new Error('DIRECT_PROTOCOL_INVALID');
          const reader=reply.body.getReader(),parts=[];let size=0;
          try{while(true){let packet;try{packet=await reader.read();}catch(error){throw new Error(combined.aborted?'BIO_REQUEST_ABORTED':error.name==='TypeError'?'CONNECTOR_UNAVAILABLE':'DIRECT_PROTOCOL_INVALID');}const {value,done}=packet;if(done)break;size+=value.length;if(size>limit)throw new Error('DIRECT_PROTOCOL_INVALID');parts.push(value);}}
          finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
          alive(combined);if(size!==limit)throw new Error('DIRECT_PROTOCOL_INVALID');const bytes=new Uint8Array(size);let start=0;for(const part of parts){bytes.set(part,start);start+=part.length;}return new Response(bytes,{headers:reply.headers});
        }catch(error){if(error.message!=='DIRECT_PERMIT_EXPIRED')throw error;if(attempt)throw new Error('DIRECT_TRANSFER_TIMEOUT');}
        finally{if(reservation)await connector.release(reservation);}
      }
    }finally{unlock();}
  },dispose(){if(closed)return;closed=true;abort.abort();for(const task of channels.values())void task.promise.then(c=>{void connector.close(c.channelId);const args={channelId:c.channelId,clientId,pairingId:c.pairingId};void (api.closeChannel?api.closeChannel(args):api('direct-close',args)).catch(()=>{});}).catch(()=>{});channels.clear();}};
}
