import test from 'node:test';
import assert from 'node:assert/strict';
import {Readable,PassThrough} from 'node:stream';
import {EventEmitter} from 'node:events';
import {readFileSync,mkdtempSync,writeFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildReaderCommand,createSshReader,classifySshFailure} from '../ssh-reader.mjs';
import {encodeRecord,decodeRecords} from '../ssh-protocol.mjs';
import {loadConnectorConfig,checkProfile} from '../config.mjs';
import {spawn,spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

function protectedProfile(t,overrides={}){
  const root=realpathSync(mkdtempSync(join(process.env.BIO_DIRECT_TEST_TMPDIR||tmpdir(),'connector-config-')));t.after(()=>rmSync(root,{recursive:true,force:true}));
  const cfg=join(root,'ssh_config'),hosts=join(root,'known_hosts'),file=join(root,'connector.json');
  const blob=Buffer.alloc(51);blob.writeUInt32BE(11);blob.write('ssh-ed25519',4);blob.writeUInt32BE(32,15);
  writeFileSync(cfg,'Host hpc-example\n HostName compute.invalid\n',{mode:0o600});writeFileSync(hosts,(overrides.targetId||profile.targetId)+' ssh-ed25519 '+blob.toString('base64')+'\n',{mode:0o600});
  writeFileSync(file,JSON.stringify({schemaVersion:'bio.local-connector.v1',allowedOrigin:'https://dsh.invalid',port:18773,targets:[{...profile,...overrides,connection:{...profile.connection,...overrides.connection,configFile:cfg,knownHosts:hosts}}]}),{mode:0o600});
  return {root,cfg,file,profile:loadConnectorConfig(file).targets[0]};
}

const cases=JSON.parse(readFileSync(new URL('../../visualization/direct-contract-cases.json',import.meta.url)));
const profile={targetId:cases.channel.targetId,hostKeyAlias:cases.channel.targetId,connection:{schemaVersion:'bio.ssh-connection.v1',hostAlias:'hpc-example',configFile:'/tmp/operator-config',knownHosts:'/tmp/operator-known-hosts',projectsRoot:"/tmp/science user's projects",releaseRoot:'/tmp/release',python:'/tmp/python'}};
test('remote command contains only protected operator paths; dynamic source stays stdin',()=>{
  const command=buildReaderCommand(profile);
  assert.equal(command.executable,'/usr/bin/ssh');assert.equal(command.args.includes('StrictHostKeyChecking=yes'),true);
  assert.equal(command.args.includes('ForwardAgent=no'),true);assert.equal(command.args.includes('HostKeyAlias='+profile.targetId),true);
  assert.equal(command.args.at(-1).includes(cases.channel.projectKey),false);
  assert.equal(command.args.at(-1).includes("'\\''"),true);
});
test('partial and oversized framing fails before byte allocation',async()=>{
  for(const value of [Buffer.from([0]),Buffer.from('shell banner'),Buffer.concat([Buffer.from([0,0,0,2,0,1,0,1]),Buffer.alloc(2)])]){
    await assert.rejects(async()=>{for await(const record of decodeRecords(Readable.from([value]))){}},/DIRECT_PROTOCOL_INVALID/);
  }
});
test('wrong identity is rejected before reading and cancellation owns only its child',async t=>{
  const {profile:checked}=protectedProfile(t);
  const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();let killed=0;
  child.kill=()=>{killed++;queueMicrotask(()=>child.emit('exit',null,'SIGTERM'));};
  let request;child.stdin.on('data',bytes=>{request=JSON.parse(bytes.subarray(8).toString());child.stdout.write(encodeRecord({kind:'hello',requestId:request.requestId,channelId:cases.channel.channelId,readerVersion:'bio.direct-reader.v1',executionTargetSha256:'a'.repeat(64),hostIdentitySha256:cases.channel.hostIdentitySha256,uid:cases.channel.uid,projectKey:cases.channel.projectKey}));});
  const reader=createSshReader({profile:checked,channel:cases.channel,spawnChild:()=>child});
  await assert.rejects(reader.hello(),/SSH_IDENTITY_MISMATCH/);await reader.close();assert.equal(killed,1);
});
test('unknown SSH error remains fatal while explicit network failures can fall back',()=>{
  assert.equal(classifySshFailure('Host key verification failed.'),'SSH_IDENTITY_MISMATCH');
  assert.equal(classifySshFailure('Permission denied (publickey).'),'SSH_AUTH_REQUIRED');
  assert.equal(classifySshFailure('Connection refused'),'SSH_NETWORK_UNAVAILABLE');
  assert.equal(classifySshFailure('unexpected error'),'DIRECT_PROTOCOL_INVALID');
});
test('network EOF after handshake differs from unknown or partial framing EOF',async t=>{
 const {profile:checked}=protectedProfile(t);
 for(const [stderr,partial,code] of [['Connection timed out',false,'SSH_NETWORK_UNAVAILABLE'],['client_loop: send disconnect: Broken pipe',false,'SSH_NETWORK_UNAVAILABLE'],['unknown',false,'DIRECT_PROTOCOL_INVALID'],['Connection timed out',true,'DIRECT_PROTOCOL_INVALID']]){
  const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();child.kill=()=>child.emit('exit',null,'SIGTERM');
  child.stdin.on('data',bytes=>{const h=JSON.parse(bytes.subarray(8).toString());if(h.kind==='hello')child.stdout.write(encodeRecord({kind:'hello',requestId:h.requestId,channelId:cases.channel.channelId,readerVersion:cases.channel.readerVersion,executionTargetSha256:cases.channel.executionTargetSha256,hostIdentitySha256:cases.channel.hostIdentitySha256,uid:cases.channel.uid,projectKey:cases.channel.projectKey}));else queueMicrotask(()=>{child.stderr.write(stderr);if(partial)child.stdout.write(Buffer.from([0]));child.stdout.end();child.emit('exit',255);});});
  const reader=createSshReader({profile:checked,channel:cases.channel,spawnChild:()=>child});await reader.hello();await assert.rejects(async()=>{for await(const block of reader.read(cases.permit)){}},new RegExp(code));await reader.close();
 }
});
test('returning early from a read terminates only the incomplete helper',async t=>{
  const {profile:checked}=protectedProfile(t);const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();let killed=0;
  child.kill=()=>{killed++;queueMicrotask(()=>child.emit('exit',null,'SIGTERM'));};
  const permit={...cases.permit,offset:0,limit:2};
  child.stdin.on('data',bytes=>{const h=JSON.parse(bytes.subarray(8).toString());
    if(h.kind==='hello')child.stdout.write(encodeRecord({kind:'hello',requestId:h.requestId,channelId:cases.channel.channelId,readerVersion:cases.channel.readerVersion,executionTargetSha256:cases.channel.executionTargetSha256,hostIdentitySha256:cases.channel.hostIdentitySha256,uid:cases.channel.uid,projectKey:cases.channel.projectKey}));
    else {const data=Buffer.from([1]);child.stdout.write(encodeRecord({kind:'data',requestId:h.requestId,previewId:permit.previewId,fileId:permit.fileId,sourceSha256:permit.sourceSha256,manifestSha256:permit.manifestSha256,fileSha256:permit.fileSha256,byteSize:permit.byteSize,offset:0,nextOffset:1,sha256:createHash('sha256').update(data).digest('hex')},data));}
  });
  const reader=createSshReader({profile:checked,channel:cases.channel,spawnChild:()=>child});await reader.hello();
  for await(const block of reader.read(permit)){break;}
  assert.equal(reader.closed,true);assert.equal(killed,1);
});
test('operator config replacement refuses an existing profile pin',t=>{
  const {cfg,profile:checked}=protectedProfile(t);writeFileSync(cfg,'Host hpc-example\n HostName changed.invalid\n');
  assert.throws(()=>checkProfile(checked),/DIRECT_BINDING_CHANGED/);
});
test('slow stdio consumers bound queued framing bytes and early return stops its producer',async()=>{
  let pushed=0;const frame=encodeRecord({kind:'data'},Buffer.alloc(65536));
  const stream=new Readable({highWaterMark:65536,read(){pushed++;this.push(frame);}});
  const records=decodeRecords(stream)[Symbol.asyncIterator]();await records.next();await new Promise(resolve=>setTimeout(resolve,25));
  assert.ok(pushed<=3);assert.ok(stream.readableLength<=frame.length);await records.return();assert.equal(stream.destroyed,true);
});
test('real local Python reader exchanges exact bytes and leaves science unchanged',async t=>{
  const python='/opt/anaconda3/envs/modeling/bin/python',repo=fileURLToPath(new URL('../../',import.meta.url));
  const base=protectedProfile(t);
  const setup=spawnSync(python,['-B','-c',`import json,hashlib,sys\nfrom pathlib import Path\nfrom tests.direct_fixture import make_direct_fixture,direct_binding\nfrom bioagent.visualization.direct_permits import DirectPermitStore\nfrom bioagent.visualization.published_reader import PublishedPreviewReader\nroot=Path(sys.argv[1])/hashlib.sha256(b'session-test').hexdigest()\nf=make_direct_fixture(root)\ns=DirectPermitStore(root,reader=PublishedPreviewReader(root,f['store']))\nc=s.open_channel(direct_binding(f))\ne=f['manifest']['files'][0]\np=s.issue(c['channelId'],e['fileId'],0,e['byteSize'])\nprint(json.dumps(dict(channel=c,permit=p,original=hashlib.sha256((f['output']/'trajectory.dcd').read_bytes()).hexdigest())))`,base.root],{cwd:repo,env:{...process.env,PYTHONPATH:join(repo,'src')+':'+repo},encoding:'utf8'});
  assert.equal(setup.status,0,'local fixture setup failed');const receipt=JSON.parse(setup.stdout);
  const {profile:checked}=protectedProfile(t,{targetId:receipt.channel.targetId,hostKeyAlias:receipt.channel.targetId,connection:{projectsRoot:base.root,releaseRoot:repo,python}});
  let child;const reader=createSshReader({profile:checked,channel:receipt.channel,spawnChild:()=>child=spawn(python,['-I','-B',join(repo,'run_biopreview_direct.py'),'--projects-root',base.root,'--python',python],{stdio:['pipe','pipe','pipe']})});
  t.after(()=>reader.close());await reader.hello();const chunks=[];for await(const block of reader.read(receipt.permit))chunks.push(block.data);
  const data=Buffer.concat(chunks);assert.equal(data.length,receipt.permit.limit);
  const expected=readFileSync(join(base.root,receipt.channel.projectKey,'previews/files',receipt.channel.previewId,receipt.permit.fileId));
  assert.equal(data.equals(expected),true);
  await assert.rejects(reader.bind({...receipt.channel,projectKey:'a'.repeat(64)}),/DIRECT_BINDING_CHANGED/);
  await reader.bind(receipt.channel);assert.equal(reader.closed,false);
  await reader.close();assert.ok(child.exitCode!==null||child.signalCode!==null);
  assert.equal(createHash('sha256').update(readFileSync(join(base.root,receipt.channel.projectKey,'runtime-attempts/attempt/output/trajectory.dcd'))).digest('hex'),receipt.original);
});
