import test from 'node:test';
import assert from 'node:assert/strict';
import {copyFile,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {BridgeClient,scientificEnv} from '../bridge-client.mjs';
import {ClientReceipts} from '../client-receipts.mjs';
import {execFileSync} from 'node:child_process';
const python=process.env.BIO_TEST_PYTHON||execFileSync('python3',['-c','import sys; print(sys.executable)'],{encoding:'utf8'}).trim();

test('real bridge imports an operator structure without rewriting the source',async()=>{
  const project=await mkdtemp(join(tmpdir(),'bio-chat-client-'));
  const bridge=new BridgeClient({project,python});
  try{
    const source=fileURLToPath(new URL('../../runtime/ksh_openmm/tests/fixtures/leu-ser.pdb',import.meta.url));
    const before=await readFile(source);
    assert.equal((await bridge.control('import',{source})).ok,true);
    const response=await bridge.call('bio_inputs',{offset:0,limit:10});
    assert.equal(response.result.items[0].byteSize,2751);
    assert.deepEqual(await readFile(source),before);
    assert.equal((await bridge.call('bio_review',{})).ok,false);
  }finally{await bridge.close();}
});
test('scientific child environment excludes model credentials',()=>{
  const env=scientificEnv(python,{HOME:'/test',DEEPSEEK_API_KEY:'local-test-sentinel',OPENAI_API_KEY:'other'});
  assert.equal(env.HOME,'/test');
  assert.ok(!JSON.stringify(env).includes('local-test-sentinel'));
});

test('sent effect identity survives client reconstruction until explicit recovery',async()=>{
  const {ClientReceipts}=await import('../client-receipts.mjs');
  const project=await mkdtemp(join(tmpdir(),'bio-chat-client-receipts-'));
  const value={requestId:'00000000-0000-4000-8000-000000000001',tool:'bio_submit',arguments:{workflowId:'protein-test',revision:1}};
  const first=new ClientReceipts(project);first.begin(value);
  const restarted=new ClientReceipts(project);
  assert.throws(()=>restarted.guard('protein-test'),/TOOL_RESPONSE_UNKNOWN/);
  restarted.recover('protein-test');
  assert.doesNotThrow(()=>restarted.guard('protein-test'));
  const text=await readFile(first.path(value.requestId),'utf8');
  assert.ok(text.includes(value.requestId));assert.ok(text.includes('reconciled'));
});

test('a received successful submit still needs durable acknowledgement across restart',async()=>{
  const project=await mkdtemp(join(tmpdir(),'bio-chat-received-effect-'));
  const value={requestId:'00000000-0000-4000-8000-000000000002',tool:'bio_submit',arguments:{workflowId:'protein-test',revision:1}};
  const first=new ClientReceipts(project);first.begin(value);first.received({requestId:value.requestId,ok:true});
  const second=new ClientReceipts(project);
  assert.throws(()=>second.guard('protein-test'),/TOOL_RESPONSE_UNKNOWN/);
  second.acknowledge(value.requestId);
  assert.doesNotThrow(()=>new ClientReceipts(project).guard('protein-test'));
});

