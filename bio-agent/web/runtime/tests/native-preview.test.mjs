import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,existsSync,realpathSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {fileURLToPath} from 'node:url';import {spawnSync} from 'node:child_process';
import {projectForSession} from '../bio-runtime.mjs';import {BridgeClient,scientificEnv} from '../../../agent/bridge-client.mjs';
import {Session} from '@deepseek-ai/dsh-session';
const native=await import('../../../../scripts/serve-bio-dsh-preview.mjs').catch(e=>{if(e.code!=='ERR_MODULE_NOT_FOUND')throw e;return {};});
const plugin=await import('../../../../scripts/bio-dsh-preview-plugin.mjs').catch(e=>{if(e.code!=='ERR_MODULE_NOT_FOUND')throw e;return {};});
const repoRoot=fileURLToPath(new URL('../../../../',import.meta.url)),python=process.env.BIO_TEST_PYTHON||'/opt/anaconda3/envs/modeling/bin/python';
const root=realpathSync(mkdtempSync(join(tmpdir(),'native-preview-')));
test('native_profile_is_owned_and_readonly',async()=>{
  assert.equal(typeof native.prepareNativeProfile,'function','native DSH profile is required');
  await assert.rejects(native.prepareNativeProfile({stateRoot:root,repoRoot,python}),/NATIVE_STATE_NOT_NEW/);
  await assert.rejects(native.prepareNativeProfile({stateRoot:join(repoRoot,'bio-agent','new-state'),repoRoot,python}),/NATIVE_STATE_BOUNDARY/);
  const profile=await native.prepareNativeProfile({stateRoot:join(root,'profile'),repoRoot,python});assert.equal(profile.viewEnabled,false);assert.equal(profile.engineeringOnly,true);
  for(const action of ['bio_submit','human:approve','human:submit','advance','human:recover'])await assert.rejects(plugin.engineeringGate(action),/ENGINEERING_READONLY/);
  await plugin.engineeringGate('preview:describe');await plugin.engineeringGate('human:inputs');
  const env=scientificEnv(python,{BIO_WEB_TEST_API_KEY:'synthetic',HOME:root});assert.equal(env.BIO_WEB_TEST_API_KEY,undefined);assert.equal(env.HOME,root);
  assert.notEqual(projectForSession(profile.projectsRoot,'a'),projectForSession(profile.projectsRoot,'b'));assert.equal(existsSync(join(profile.home,'credentials.yaml')),false);
});
test('native profile forwards only the operator-selected PyMOL runtime setting',async()=>{
  const previous=process.env.BIO_PREVIEW_PYMOL_PYTHON;
  process.env.BIO_PREVIEW_PYMOL_PYTHON='/operator/pymol/bin/python';
  try{
    const profile=await native.prepareNativeProfile({stateRoot:join(root,'profile-pbc'),repoRoot,python});
    assert.equal(profile.env.BIO_PREVIEW_PYMOL_PYTHON,'/operator/pymol/bin/python');
    assert.equal(profile.env.BIO_WEB_TEST_API_KEY,undefined);
  }finally{if(previous===undefined)delete process.env.BIO_PREVIEW_PYMOL_PYTHON;else process.env.BIO_PREVIEW_PYMOL_PYTHON=previous;}
});
test('native_fixture_matches_actual_session',()=>{
  const out=join(root,'fixture'),session='native-real-session';const result=spawnSync(python,['-B',join(repoRoot,'bio-agent/tests/direct_browser_fixture.py'),'--out',out,'--session-id',session],{encoding:'utf8',env:scientificEnv(python)});
  assert.equal(result.status,0,'fixture must support the real native session');const fixture=JSON.parse(readFileSync(join(out,'fixture.json')));
  assert.equal(fixture.project,projectForSession(join(out,'compute'),session));assert.equal(fixture.manifest.source.kind,'accepted');assert.equal(fixture.manifest.sourceFrameCount,203);assert.ok(existsSync(join(fixture.project,'inputs/engineering-protein.pdb')));
});
test('native accepted fixture exposes bounded completion metadata without invented worker progress',async()=>{
  const out=join(root,'progress-fixture'),session='progress-session';
  const result=spawnSync(python,['-B',join(repoRoot,'bio-agent/tests/direct_browser_fixture.py'),'--out',out,'--session-id',session],{encoding:'utf8',env:scientificEnv(python)});
  assert.equal(result.status,0);const fixture=JSON.parse(readFileSync(join(out,'fixture.json')));
  const bridge=new BridgeClient({project:fixture.project,python,executionTarget:fixture.target});
  try{
    const reply=await bridge.control('progress',{workflowId:'engineering',revision:1});
    assert.equal(reply.ok,true,JSON.stringify(reply));assert.equal(reply.result.state,'COMPLETED');
    assert.equal(reply.result.stages[0].kind,'production');assert.equal(reply.result.stages[0].progress,null);
  }finally{await bridge.close();}
});
test('native demo task history is attributed to the fixture plugin rather than a user or model',()=>{
  assert.equal(typeof plugin.seedNativeTask,'function');const session=Session.create('session-native-structure');plugin.seedNativeTask(session,'session-native-structure');
  const message=session.events.find(e=>e.type==='user/message');assert.equal(message.data.source.kind,'plugin');assert.equal(message.data.source.form,'notice');assert.match(message.data.content[0].text,/本地工程演示/);
  assert.equal(session.events.some(e=>e.type==='assistant/message'||e.type==='step/start'),false);
  assert.equal(session.events.filter(e=>e.type==='turn/start').length,1);assert.equal(session.events.find(e=>e.type==='turn/end').data.reason.kind,'aborted');
});
test('native demo tasks exist before any model request',async()=>{
  assert.equal(typeof native.startNativePreview,'function');const stateRoot=join(root,'launch');const service=await native.startNativePreview({stateRoot,repoRoot,python,port:3085,connectorPort:18775});
  try{
    for(let i=0;i<50&&!existsSync(join(stateRoot,'native-sessions.json'));i++)await new Promise(r=>setTimeout(r,100));
    assert.ok(existsSync(join(stateRoot,'native-sessions.json')),'native empty session startup must publish actual agents');
    const receipt=JSON.parse(readFileSync(join(stateRoot,'native-sessions.json')));assert.equal(receipt.sessionIds.length,2);assert.equal(receipt.modelRequests,0);assert.equal(receipt.realDshAgents,true);
  }finally{await service.close();}
});
