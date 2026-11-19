import test from 'node:test';import assert from 'node:assert/strict';
import {SharedEgress,assessView} from '../../../../scripts/validate-bio-visualization.mjs';
test('shared proxy limits all viewers together and counts cold assets',async()=>{
  let now=0;const network=[];const budget=new SharedEgress({bitsPerSecond:4000000,clock:()=>now,sleep:async ms=>{now+=ms;await Promise.resolve();},onSend:item=>network.push(item)});
  await Promise.all(['one','three','five'].map(owner=>budget.send(owner,new Uint8Array(32768),'asset',()=>{})));
  assert.equal(network.reduce((n,r)=>n+r.bytes,0),98304);assert.ok(network.some(r=>r.kind==='asset'));
  assert.ok(98304*8<=5000000*now/1000+16384*8);assert.equal(new Set(network.map(r=>r.owner)).size,3);
});
test('report cannot accept a missing actual renderer readback',()=>{
  assert.equal(assessView({},{}).status,'NOT_ACCEPTED');
  assert.equal(assessView({renderer:{ready:false}},{durationSeconds:180}).status,'NOT_ACCEPTED');
});
