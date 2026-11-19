import test from 'node:test';import assert from 'node:assert/strict';import {bindView} from '../../visualization/src/view-bridge.mjs';
const settle=()=>new Promise(r=>setImmediate(r));
test('a delayed attach is detached after disposal and never starts polling or restoration',async()=>{
  let attach;const calls=[];const signal=new AbortController();const dock={handler:{observe:()=>({})},restore:async()=>assert.fail('stale restore'),player:{pause(){}},showError(){}};
  const binding=bindView({dock,sessionId:'old',viewportId:'main',signal:signal.signal,call:async(action)=>{calls.push(action);if(action==='attach')return new Promise(r=>attach=r);return {ok:true,value:{}};}});
  await settle();signal.abort();attach({ok:true,value:{restore:{}}});const detach=await binding;detach();await settle();
  assert.equal(calls.filter(c=>c==='attach').length,1);assert.ok(calls.includes('detach'));assert.equal(calls.includes('poll'),false);
});
test('a delayed poll after disposal cannot execute a display command or schedule another poll',async()=>{
  let poll;const calls=[];const signal=new AbortController();const dock={handler:{observe:()=>({}),execute:async()=>assert.fail('stale command')},metadata:()=>null,player:{pause(){}},showError(){}};
  const detach=await bindView({dock,sessionId:'old',viewportId:'main',signal:signal.signal,call:async(action)=>{calls.push(action);if(action==='poll')return new Promise(r=>poll=r);return {ok:true,value:{}};}});
  await settle();detach();poll({ok:true,value:{command:{}}});await settle();assert.deepEqual(calls,['attach','poll','detach']);
});
