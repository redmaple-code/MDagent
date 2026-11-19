import {timingSafeEqual,createHash} from 'node:crypto';
import {PreviewApi} from './preview-api.mjs';
export function verifyRelayToken(observed,expected){
  if(typeof expected!=='string'||!expected||typeof observed!=='string'||Buffer.byteLength(observed)!==Buffer.byteLength(expected)||!timingSafeEqual(Buffer.from(observed),Buffer.from(expected)))throw new Error('BIO_WORLD_TOKEN_REQUIRED');
}
export function decodeRead(value){
  const bytes=Buffer.from(value.data||'','base64');
  if(bytes.length<1||bytes.length>65536||bytes.toString('base64')!==value.data||value.nextOffset!==value.offset+bytes.length||createHash('sha256').update(bytes).digest('hex')!==value.sha256)throw new Error('PREVIEW_CHUNK_CHANGED');
  return bytes;
}
export function installPreviewRelay(ctx,projects,config){
  if(ctx.webServer.host!=='127.0.0.1')throw new Error('BIO_WEB_LOOPBACK_REQUIRED');
  const api=new PreviewApi(ctx,projects,config);
  ctx.effect(()=>ctx.webServer.register({kind:'prefix',path:'/_bio/preview',handler:async(req,res)=>{
    const abort=new AbortController();req.once('aborted',()=>abort.abort());res.once('close',()=>{if(!res.writableEnded)abort.abort();});
    try{
      verifyRelayToken(req.headers['x-world-token'],ctx.subprocess?.world?.config?.token);
      if(req.method!=='POST')throw new Error('INVALID_PREVIEW_REQUEST');
      const parts=[];let size=0;for await(const part of req){size+=part.length;if(size>32768)throw new Error('INVALID_PREVIEW_REQUEST');parts.push(part);}
      const action=new URL(req.url,'http://loopback').pathname.split('/').at(-1),payload=JSON.parse(Buffer.concat(parts));
      const value=await api.dispatch(action,payload,abort.signal);
      res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(value));
    }catch(error){if(!res.headersSent){res.writeHead(403,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify({error:/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'PREVIEW_OPERATION_FAILED'}));}else res.destroy();}
  }}));return api;
}
