import {randomBytes as secureBytes,randomUUID,createHash,timingSafeEqual} from 'node:crypto';
const hash=v=>createHash('sha256').update(v).digest();
const id=v=>typeof v==='string'&&/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(v);
const fail=()=>{throw new Error('DIRECT_PAIR_DENIED');};
export function createPairing({allowedOrigin,clock=Date.now,randomBytes=secureBytes}){
  let codeHash,expires=0,attempts=0;const pairs=new Map();
  function expireIdle(){const expired=[];for(const [key,value] of pairs)if(clock()-value.lastSeenAtMs>=900000){expired.push(value);pairs.delete(key);}return expired;}
  return {issueCode(){const code=randomBytes(16).toString('base64url');codeHash=hash(code);expires=clock()+120000;attempts=0;return code;},
    pair({code,clientId,sessionId}){
      expireIdle();if(!codeHash||clock()>=expires||attempts>=5||typeof code!=='string'||code.length>128||!id(clientId)||typeof sessionId!=='string'||sessionId.length<1||sessionId.length>256||/[\x00-\x1f\x7f]/.test(sessionId))fail();
      attempts++;if(!timingSafeEqual(hash(code),codeHash)||pairs.size>=128)fail();codeHash=null;
      const connectorSession=randomBytes(32).toString('base64url'),pairingId=randomUUID();pairs.set(hash(connectorSession).toString('hex'),{pairingId,clientId,sessionId,origin:allowedOrigin,lastSeenAtMs:clock()});return {connectorSession,pairingId};
    },authorize(key,{origin}){
      if(typeof key!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(key)||origin!==allowedOrigin)fail();const value=pairs.get(hash(key).toString('hex'));
      if(!value||clock()-value.lastSeenAtMs>=900000)fail();value.lastSeenAtMs=clock();return {...value};
    },expireIdle,close(key){if(typeof key!=='string')return null;const h=hash(key).toString('hex'),value=pairs.get(h);pairs.delete(h);return value||null;},
    closeAll(){codeHash=null;const values=[...pairs.values()];pairs.clear();return values;}};
}
