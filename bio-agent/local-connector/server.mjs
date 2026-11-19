import http from 'node:http';import {createHash} from 'node:crypto';import {once} from 'node:events';
import {createPairing} from './pairing.mjs';import {createScheduler} from './scheduler.mjs';import {createSshReader} from './ssh-reader.mjs';
import {validateChannel,validatePermit} from '../web/runtime/visualization/direct-contracts.mjs';
const keys=(v,n)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===n.split(' ').sort().join('|');
const fail=code=>{throw new Error(code);};
export function createConnectorServer({config,pairing=createPairing({allowedOrigin:config.allowedOrigin}),scheduler=createScheduler(),readerFactory=createSshReader}){
  const helpers=new Map();let closed=false,previewBufferedBytes=0,maxPreviewBufferedBytes=0,maxHelpers=0,directBytes=0,controlBytes=0;
  let setup=Promise.resolve();
  async function remove(channelId){const entry=helpers.get(channelId);if(!entry)return;if(!entry.closing)entry.closing=(async()=>{await entry.reader?.close();if(helpers.get(channelId)===entry)helpers.delete(channelId);})();await entry.closing;}
  async function closePair(pair){scheduler.cancelOwner(pair.pairingId);await Promise.all([...helpers].filter(([,v])=>v.pairingId===pair.pairingId).map(([id])=>remove(id)));}
  async function helper(channel,pair,signal){
    const preceding=setup;let unlock;setup=new Promise(resolve=>unlock=resolve);await preceding;
    try{
    if(closed||signal.aborted)fail('BIO_REQUEST_ABORTED');
    let entry=helpers.get(channel.channelId);if(entry){if(entry.pairingId!==pair.pairingId||entry.active)fail('DIRECT_SLOT_LIMIT');if(entry.reader.closed){await remove(channel.channelId);entry=null;}}
    if(!entry){
      const compatible=c=>['targetId','executionTargetSha256','projectKey','uid','hostIdentitySha256'].every(k=>c[k]===channel[k])&&c.transportBinding.ownerBinding===channel.transportBinding.ownerBinding&&c.transportBinding.sessionEpoch===channel.transportBinding.sessionEpoch;
      const reusable=[...helpers].find(([,v])=>!v.active&&!v.closing&&!v.reader.closed&&v.reader.bind&&compatible(v.channel));
      if(reusable){
        helpers.delete(reusable[0]);entry=reusable[1];entry.active=true;entry.pairingId=pair.pairingId;entry.channel=channel;helpers.set(channel.channelId,entry);
        try{await entry.reader.bind(channel,signal);return entry;}catch(error){await remove(channel.channelId);throw error;}
      }
      if(helpers.size>=2){const idle=[...helpers].find(([,v])=>!v.active);if(!idle)fail('DIRECT_SLOT_LIMIT');await remove(idle[0]);}
      const profile=config.targets.find(p=>p.targetId===channel.targetId);if(!profile)fail('DIRECT_NOT_CONFIGURED');
      entry={pairingId:pair.pairingId,active:true,reader:null,channel};helpers.set(channel.channelId,entry);maxHelpers=Math.max(maxHelpers,helpers.size);
      try{entry.reader=readerFactory({profile,channel});await entry.reader.hello(signal);}catch(error){await remove(channel.channelId);throw error;}
    }
    entry.active=true;return entry;
    }finally{unlock();}
  }
  const server=http.createServer(async(req,res)=>{
    const abort=new AbortController();req.once('aborted',()=>abort.abort());res.once('close',()=>{if(!res.writableEnded)abort.abort();});
    const origin=req.headers.origin;let held,entry,reading=false,completed=false;
    const json=(status,value)=>{const data=JSON.stringify(value);controlBytes+=Buffer.byteLength(data);res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(data);};
    try{
      const port=server.address()?.port;
      if(closed||origin!==config.allowedOrigin||req.headers.host!=='127.0.0.1:'+port||!['127.0.0.1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress))fail('DIRECT_ACCESS_DENIED');
      res.setHeader('access-control-allow-origin',config.allowedOrigin);res.setHeader('vary','Origin');res.setHeader('access-control-expose-headers','x-bio-source-sha256,x-bio-file-sha256,x-bio-manifest-sha256,x-bio-next-offset');
      if(req.method==='OPTIONS'){
        if(req.headers['access-control-request-method']!=='POST'||!['/v1/pair','/v1/open','/v1/read','/v1/close'].includes(req.url))fail('INVALID_DIRECT_REQUEST');
        const requested=(req.headers['access-control-request-headers']||'').toLowerCase().split(',').map(v=>v.trim()).filter(Boolean);if(requested.some(v=>!['content-type','x-bio-connector-session'].includes(v)))fail('INVALID_DIRECT_REQUEST');
        res.writeHead(204,{'access-control-allow-methods':'POST','access-control-allow-headers':'content-type,x-bio-connector-session','access-control-allow-private-network':'true'});res.end();return;
      }
      if(req.method==='GET'&&req.url==='/v1/health'){json(200,{protocol:'bio.local-connector.v1',ready:true});return;}
      if(req.method!=='POST'||!['/v1/pair','/v1/open','/v1/read','/v1/close'].includes(req.url)||req.headers['content-type']!=='application/json'||Number(req.headers['content-length']||0)>32768)fail('INVALID_DIRECT_REQUEST');
      const parts=[];let size=0;for await(const part of req){size+=part.length;if(size>32768)fail('INVALID_DIRECT_REQUEST');parts.push(part);}
      let body;try{body=JSON.parse(Buffer.concat(parts));}catch{fail('INVALID_DIRECT_REQUEST');}
      if(req.url==='/v1/pair'){if(!keys(body,'code clientId sessionId'))fail('INVALID_DIRECT_REQUEST');json(200,pairing.pair(body));return;}
      for(const expired of pairing.expireIdle())await closePair(expired);
      const key=req.headers['x-bio-connector-session'],pair=pairing.authorize(key,{origin});
      if(req.url==='/v1/open'){
        if(!keys(body,'channel priority')||!['foreground','prefetch'].includes(body.priority))fail('INVALID_DIRECT_REQUEST');const channel=validateChannel(body.channel);
        if(channel.clientId!==pair.clientId||channel.pairingId!==pair.pairingId||channel.projectKey!==createHash('sha256').update(pair.sessionId).digest('hex'))fail('DIRECT_ACCESS_DENIED');
        held=await scheduler.reserve({clientId:pair.clientId,pairingId:pair.pairingId,channelId:channel.channelId,channel,priority:body.priority},abort.signal);
        entry=await helper(channel,pair,held.signal);
        held.signal.addEventListener('abort',()=>{if(entry.active)void remove(channel.channelId).catch(()=>{});},{once:true});
        if(held.signal.aborted||abort.signal.aborted)fail('BIO_REQUEST_ABORTED');
        json(200,{reservationId:held.reservationId});completed=true;return;
      }
      if(req.url==='/v1/close'){
        if(!keys(body,'channelId reservationId')||![body.channelId,body.reservationId].every(v=>v===null||typeof v==='string'&&/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(v)))fail('INVALID_DIRECT_REQUEST');
        if(body.reservationId){const v=scheduler.get(body.reservationId);if(!v||v.context.pairingId!==pair.pairingId||body.channelId!==null)fail('DIRECT_ACCESS_DENIED');scheduler.release(v.reservationId);}
        else if(body.channelId){const v=helpers.get(body.channelId);if(v&&v.pairingId!==pair.pairingId)fail('DIRECT_ACCESS_DENIED');scheduler.cancelChannel(pair.pairingId,body.channelId);await remove(body.channelId);}
        else {pairing.close(key);await closePair(pair);}
        json(200,{closed:true});return;
      }
      if(!keys(body,'reservationId permit'))fail('INVALID_DIRECT_REQUEST');const permit=validatePermit(body.permit),reserved=scheduler.get(body.reservationId);
      if(!reserved||reserved.context.pairingId!==pair.pairingId)fail('DIRECT_ACCESS_DENIED');const channel=reserved.context.channel;
      if(permit.channelId!==channel.channelId||permit.previewId!==channel.previewId||permit.manifestSha256!==channel.manifestSha256||permit.sourceSha256!==channel.source.sourceSha256)fail('DIRECT_BINDING_CHANGED');
      held=scheduler.consume(body.reservationId);entry=helpers.get(channel.channelId);if(!entry?.reader)fail('DIRECT_ACCESS_DENIED');reading=true;
      const signal=AbortSignal.any([abort.signal,held.signal,AbortSignal.timeout(8000)]);const cancel=()=>{void remove(channel.channelId).catch(()=>{});};signal.addEventListener('abort',cancel,{once:true});
      const buffer=Buffer.alloc(permit.limit);previewBufferedBytes+=buffer.length;maxPreviewBufferedBytes=Math.max(maxPreviewBufferedBytes,previewBufferedBytes);
      try{
        let received=0;for await(const block of entry.reader.read(permit,signal)){if(signal.aborted||received+block.data.length>buffer.length||block.offset!==permit.offset+received||block.nextOffset!==block.offset+block.data.length)fail('DIRECT_PROTOCOL_INVALID');block.data.copy(buffer,received);received+=block.data.length;}
        if(signal.aborted||received!==permit.limit)fail('DIRECT_PROTOCOL_INVALID');
        res.writeHead(200,{'content-type':'application/octet-stream','cache-control':'no-store','content-length':String(buffer.length),'x-bio-source-sha256':permit.sourceSha256,'x-bio-file-sha256':permit.fileSha256,'x-bio-manifest-sha256':permit.manifestSha256,'x-bio-next-offset':String(permit.offset+permit.limit)});
        for(let start=0;start<buffer.length;start+=16384){if(signal.aborted)fail('BIO_REQUEST_ABORTED');if(!res.write(buffer.subarray(start,start+16384)))await once(res,'drain',{signal});}
        const flushed=once(res,'finish',{signal});res.end();await flushed;
        completed=true;entry.active=false;directBytes+=buffer.length;
      }finally{previewBufferedBytes-=buffer.length;signal.removeEventListener('abort',cancel);scheduler.release(held.reservationId);if(!completed)await remove(channel.channelId);}
    }catch(error){if(held&&!completed){scheduler.release(held.reservationId);if(entry)await remove(held.context.channelId).catch(()=>{});}if(!res.headersSent)json(403,{error:/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'DIRECT_PROTOCOL_INVALID'});else res.destroy();}
  });
  const idle=setInterval(()=>{for(const pair of pairing.expireIdle())void closePair(pair).catch(()=>{});},1000);idle.unref();
  return {pairing,scheduler,get address(){return server.address();},get metrics(){return {...scheduler.metrics,helpers:helpers.size,maxHelpers,previewBufferedBytes,maxPreviewBufferedBytes,directBytes,controlBytes};},
    listen(){return new Promise((resolve,reject)=>{const error=e=>reject(new Error(e.code==='EADDRINUSE'?'DIRECT_PORT_IN_USE':'CONNECTOR_UNAVAILABLE'));server.once('error',error);server.listen(config.port,'127.0.0.1',()=>{server.removeListener('error',error);resolve();});});},
    async close(){if(closed)return;closed=true;clearInterval(idle);pairing.closeAll();scheduler.close();await Promise.all([...helpers.keys()].map(remove));server.closeAllConnections();if(server.listening)await new Promise(resolve=>server.close(resolve));}};
}
