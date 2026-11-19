import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import * as Plugin from '../index.mjs';
import {loadCompiler} from '../../../bio-agent/deploy/compiler.mjs';
import {wireParameters} from '../../../bio-agent/web/runtime/wire-contracts.mjs';

test('workspace plugin resolves the bounded compiler without loading AutoMD model packages',async()=>{
  const Compile=await loadCompiler(Plugin.compilerRoot());
  assert.equal(Plugin.toolNames.length,11);
  assert.equal(Compile(wireParameters('bio_capabilities')).Check({}),true);
  assert.equal(Compile(wireParameters('bio_capabilities')).Check({grant:true}),false);
  assert.deepEqual(Plugin.inject,['tools','systemPrompt','agents','subprocess']);
});

test('workspace entry fails before opening a compute process when the target is absent',async()=>{
  const root=realpathSync(mkdtempSync(join(tmpdir(),'bio-plugin-packaging-')));
  await assert.rejects(Plugin.apply({}, {projectsRoot:join(root,'control'),mode:'node-world',
    executionTargetFile:join(root,'missing.json'),nodeId:'n_test',gateMode:'platform'}),/BIO_EXECUTION_TARGET_FILE_INVALID/);
});

test('workspace node entry refuses the isolated grant-only policy',async()=>{
  const root=realpathSync(mkdtempSync(join(tmpdir(),'bio-plugin-gate-')));
  const target=JSON.parse(readFileSync(new URL('../../../bio-agent/deploy/execution-target.example.json',import.meta.url),'utf8'));
  const path=join(root,'target.json');writeFileSync(path,JSON.stringify(target),{mode:0o600});
  await assert.rejects(Plugin.apply({}, {projectsRoot:join(root,'control'),mode:'node-world',
    executionTargetFile:path,nodeId:target.nodeId,gateMode:'bio-grant-only'}),/BIO_NODE_PLATFORM_GATE_REQUIRED/);
});
