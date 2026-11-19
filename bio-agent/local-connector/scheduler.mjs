import {randomUUID} from 'node:crypto';
export function createScheduler({activeLimit=2,queueLimit=32,clock=Date.now}={}){
  const active=new Map(),queue=[];let closed=false,lastClient=null,maxActive=0;
  function pump(){while(!closed&&active.size<activeLimit&&queue.length){
    const foreground=queue.filter(v=>v.context.priority==='foreground');const pool=foreground.length?foreground:queue;
    const selected=pool.find(v=>v.context.clientId!==lastClient)||pool[0];queue.splice(queue.indexOf(selected),1);
    if(selected.signal?.aborted){selected.reject(new Error('BIO_REQUEST_ABORTED'));continue;}
    lastClient=selected.context.clientId;const controller=new AbortController(),reservationId=randomUUID();
    const reservation={reservationId,context:selected.context,signal:controller.signal,controller,created:clock(),consumed:false,cleanup:selected.cleanup};
    selected.cleanup?.();const abort=()=>release(reservationId);selected.signal?.addEventListener('abort',abort,{once:true});reservation.cleanup=()=>selected.signal?.removeEventListener('abort',abort);
    active.set(reservationId,reservation);maxActive=Math.max(maxActive,active.size);selected.resolve(reservation);
  }}
  function release(id){const v=active.get(id);if(!v)return;active.delete(id);v.cleanup?.();v.controller.abort();pump();}
  const timer=setInterval(()=>expireIdle(),1000);timer.unref();
  function expireIdle(){for(const [id,v] of active)if(!v.consumed&&clock()-v.created>=15000)release(id);}
  return {reserve(context,signal){if(closed||signal?.aborted)return Promise.reject(new Error('BIO_REQUEST_ABORTED'));if(queue.length>=queueLimit)return Promise.reject(new Error('DIRECT_SLOT_LIMIT'));
    return new Promise((resolve,reject)=>{const item={context,signal,resolve,reject};const abort=()=>{const i=queue.indexOf(item);if(i>=0){queue.splice(i,1);reject(new Error('BIO_REQUEST_ABORTED'));}};signal?.addEventListener('abort',abort,{once:true});item.cleanup=()=>signal?.removeEventListener('abort',abort);queue.push(item);pump();});},
    consume(id){const v=active.get(id);if(!v||v.consumed)throw new Error('DIRECT_ACCESS_DENIED');v.consumed=true;return v;},get:id=>active.get(id),release,expireIdle,
    cancelOwner(pairingId){for(const v of [...queue])if(v.context.pairingId===pairingId){queue.splice(queue.indexOf(v),1);v.cleanup?.();v.reject(new Error('BIO_REQUEST_ABORTED'));}for(const [id,v] of active)if(v.context.pairingId===pairingId)release(id);},
    cancelChannel(pairingId,channelId){for(const v of [...queue])if(v.context.pairingId===pairingId&&v.context.channelId===channelId){queue.splice(queue.indexOf(v),1);v.cleanup?.();v.reject(new Error('BIO_REQUEST_ABORTED'));}for(const [id,v] of active)if(v.context.pairingId===pairingId&&v.context.channelId===channelId)release(id);},
    get metrics(){return {active:active.size,queued:queue.length,maxActive};},close(){closed=true;clearInterval(timer);for(const v of queue.splice(0)){v.cleanup?.();v.reject(new Error('BIO_REQUEST_ABORTED'));}for(const id of [...active.keys()])release(id);}};
}
