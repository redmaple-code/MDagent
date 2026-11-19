import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {createHash} from 'node:crypto';
import {createPairing} from '../pairing.mjs';import {createConnectorServer} from '../server.mjs';
const cases=JSON.parse(readFileSync(new URL('../../visualization/direct-contract-cases.json',import.meta.url)));
async function fixture(t){let spawns=0,closes=0;const pairing=createPairing({allowedOrigin:'https://dsh.invalid'});
 const server=createConnectorServer({config:{allowedOrigin:'https://dsh.invalid',port:0,targets:[{targetId:cases.channel.targetId}]},pairing,readerFactory:()=>{spawns++;return {hello:async()=>{},bind:async()=>{},read:async function*(p){const data=Buffer.from([1,2]);yield {data,offset:0,nextOffset:2};},close:async()=>{closes++;}};}});
 await server.listen();t.after(()=>server.close());const url='http://127.0.0.1:'+server.address.port;
 const request=(path,body,key,origin='https://dsh.invalid')=>fetch(url+path,{method:'POST',headers:{origin,'content-type':'application/json',...(key?{'x-bio-connector-session':key}:{})},body:JSON.stringify(body)});
 return {server,pairing,request,url,get spawns(){return spawns;},get closes(){return closes;}};
}
test('unknown Origin and preflight never spawn SSH; health has no operator paths',async t=>{const f=await fixture(t);assert.equal((await f.request('/v1/open',{},null,'null')).status,403);assert.equal((await f.request('/v1/open',{},null,'https://other.invalid')).status,403);
 const pre=await fetch(f.url+'/v1/open',{method:'OPTIONS',headers:{origin:'https://dsh.invalid','access-control-request-method':'POST','access-control-request-headers':'content-type,x-bio-connector-session','access-control-request-private-network':'true'}});assert.equal(pre.status,204);assert.equal(pre.headers.get('access-control-allow-private-network'),'true');assert.equal(f.spawns,0);
 const health=await fetch(f.url+'/v1/health',{headers:{origin:'https://dsh.invalid'}});assert.deepEqual(Object.keys(await health.json()).sort(),['protocol','ready']);});
test('pair bound to client and session; reserve before read, close and port occupied',async t=>{const f=await fixture(t);const paired=await (await f.request('/v1/pair',{code:f.pairing.issueCode(),clientId:cases.channel.clientId,sessionId:'session-test'})).json();
 const channel={...cases.channel,pairingId:paired.pairingId,projectKey:createHash('sha256').update('session-test').digest('hex')};
 assert.equal((await f.request('/v1/open',{channel:{...channel,clientId:'foreign'},priority:'foreground'},paired.connectorSession)).status,403);assert.equal(f.spawns,0);
 const opened=await (await f.request('/v1/open',{channel,priority:'foreground'},paired.connectorSession)).json();assert.ok(opened.reservationId);assert.equal(f.spawns,1);
 const res=await f.request('/v1/read',{reservationId:opened.reservationId,permit:{...cases.permit,offset:0,limit:2}},paired.connectorSession);assert.equal(res.status,200);assert.deepEqual([...new Uint8Array(await res.arrayBuffer())],[1,2]);assert.ok(f.server.metrics.maxPreviewBufferedBytes<=1048576);
 assert.equal((await f.request('/v1/read',{reservationId:opened.reservationId,permit:cases.permit},paired.connectorSession)).status,403);
 await f.request('/v1/close',{channelId:null,reservationId:null},paired.connectorSession);assert.equal(f.closes,1);
 const other=createConnectorServer({config:{allowedOrigin:'https://dsh.invalid',port:f.server.address.port,targets:[]},pairing:f.pairing});await assert.rejects(other.listen(),/DIRECT_PORT_IN_USE/);await other.close();});
test('five same-project views reuse bounded idle helpers across paired channels',async t=>{const f=await fixture(t);for(let i=0;i<5;i++){
 const paired=await (await f.request('/v1/pair',{code:f.pairing.issueCode(),clientId:'client-'+i,sessionId:'session-test'})).json();const channel={...cases.channel,channelId:'channel-'+i,clientId:'client-'+i,pairingId:paired.pairingId,projectKey:createHash('sha256').update('session-test').digest('hex')};
 const opened=await (await f.request('/v1/open',{channel,priority:'foreground'},paired.connectorSession)).json();const reply=await f.request('/v1/read',{reservationId:opened.reservationId,permit:{...cases.permit,channelId:channel.channelId,offset:0,limit:2}},paired.connectorSession);assert.equal(reply.status,200);await reply.arrayBuffer();}
 assert.equal(f.spawns,1);assert.equal(f.server.metrics.maxHelpers,1);});
