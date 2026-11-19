export function createLocalConnectorClient({port=18773,sessionId,clientId,fetch:request=globalThis.fetch}){
  if(!Number.isInteger(port)||port<1||port>65535)throw new Error('DIRECT_CONFIG_INVALID');
  let key=null,pairingId=null,closed=false;const abort=new AbortController(),base='http://127.0.0.1:'+port;
  const alive=()=>{if(closed)throw new Error('BIO_REQUEST_ABORTED');};
  async function raw(path,body,signal,credential=key){
    let response;try{response=await request(base+'/v1/'+path,{method:'POST',credentials:'omit',redirect:'error',targetAddressSpace:'loopback',headers:{'content-type':'application/json',...(credential?{'x-bio-connector-session':credential}:{})},body:JSON.stringify(body),signal});}
    catch(error){if(signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');throw new Error(/permission|private network|local network/i.test(error.message)?'LOCAL_NETWORK_DENIED':'CONNECTOR_UNAVAILABLE');}
    if(!response.ok){let code;try{const value=await response.json();code=value.error;}catch{}throw new Error(/^[A-Z][A-Z0-9_]{0,95}$/.test(code)?code:'DIRECT_PROTOCOL_INVALID');}return response;
  }
  const combined=signal=>AbortSignal.any([abort.signal,AbortSignal.timeout(8000),...(signal?[signal]:[])]);
  const cleanup=(body,credential=key)=>raw('close',body,AbortSignal.timeout(8000),credential).catch(()=>{});
  return {get pairingId(){return pairingId;},
    async pair(code,signal){alive();if(key)throw new Error('DIRECT_PAIR_DENIED');const response=await raw('pair',{code,clientId,sessionId},combined(signal),null),value=await response.json();
      if(typeof value.pairingId!=='string'||!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(value.pairingId)||typeof value.connectorSession!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(value.connectorSession))throw new Error('DIRECT_PROTOCOL_INVALID');
      if(closed||signal?.aborted){void cleanup({channelId:null,reservationId:null},value.connectorSession);throw new Error('BIO_REQUEST_ABORTED');}
      key=value.connectorSession;pairingId=value.pairingId;return pairingId;
    },async reserve(channel,priority='foreground',signal){alive();if(!key)throw new Error('CONNECTOR_UNAVAILABLE');const response=await raw('open',{channel,priority},combined(signal));const value=await response.json();
      if(typeof value.reservationId!=='string'||!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(value.reservationId))throw new Error('DIRECT_PROTOCOL_INVALID');
      if(closed||signal?.aborted){void cleanup({channelId:null,reservationId:value.reservationId});throw new Error('BIO_REQUEST_ABORTED');}return value;
    },async read(reservation,permit,signal){alive();return raw('read',{reservationId:reservation.reservationId,permit},combined(signal));},
    release(reservation){return cleanup({channelId:null,reservationId:reservation.reservationId});},close(channelId){return cleanup({channelId,reservationId:null});},
    dispose(){if(closed)return;closed=true;abort.abort();if(key)void cleanup({channelId:null,reservationId:null});key=null;pairingId=null;}};
}
