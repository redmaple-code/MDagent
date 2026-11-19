import {ViewCommandHandler} from '../../runtime/visualization/view-api.mjs';
export async function bindView({dock,sessionId,viewportId,call,signal}){
  const clientId=dock.clientId||crypto.randomUUID();let closed=false,timer;
  const handler=dock.handler;
  const send=async(action,value)=>{const reply=await call(action,{sessionId,viewportId,clientId,...value});if(!reply.ok)throw new Error(reply.error?.message||'VIEW_OPERATION_FAILED');return reply.value;};
  const close=()=>{if(closed)return;closed=true;clearTimeout(timer);signal?.removeEventListener('abort',close);void send('detach',{}).catch(()=>{});};
  if(signal?.aborted)return close;
  signal?.addEventListener('abort',close,{once:true});
  try{const initial=await send('attach',{observation:handler.observe()});
    if(closed){void send('detach',{}).catch(()=>{});return close;}
    if(initial.restore)await dock.restore(initial.restore);
    if(closed)return close;
  }catch(error){close();throw error;}
  async function poll(){
    if(closed)return;
    try{const reply=await send('poll',{observation:handler.observe(),state:dock.metadata()});
      if(closed)return;
      if(reply.command){let receipt;try{receipt=await handler.execute(reply.command);}catch(error){receipt={operationId:reply.command.operationId,errorCode:error.message};}if(!closed)await send('ack',{receipt,state:dock.metadata()});}
    }catch(error){if(!closed){dock.player.pause();dock.showError(error.message);}}
    if(!closed)timer=setTimeout(poll,300);
  }
  void poll();return close;
}
