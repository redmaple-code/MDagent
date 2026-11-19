import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';import {spawnSync} from 'node:child_process';
const catalog=await import('../../visualization/src/source-catalog.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const hash='a'.repeat(64),input={id:'protein',role:'structure',path:'protein.pdb',sha256:hash,byteSize:100};
const choice={key:'workflow:md:1',kind:'workflow',workflowId:'md',revision:1};
const ref={kind:'accepted',sourceId:'result-attempt',sourceSha256:hash,workflowId:'md',revision:1,stageId:'production',attemptId:'attempt'};
const workflow={workflowId:'md',revision:1,state:'COMPLETED',stages:[{id:'production',state:'COMPLETED'}],attempts:[{stageId:'production',attemptId:'attempt',state:'COMPLETED',acceptance:{manifestSha256:hash}}]};
test('each repeated registered ID resolves the exact selected file through the actual compute resolver',async()=>{
  const root=mkdtempSync(join(tmpdir(),'bio-input-preview-'));
  const python=process.env.BIO_TEST_PYTHON||'/opt/anaconda3/envs/modeling/bin/python';
  const env={PATH:process.env.PATH,PYTHONPATH:resolve('bio-agent/src'),TMPDIR:process.env.TMPDIR};
  const pdb='ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C  \nEND\n';
  const run=code=>{const result=spawnSync(python,['-B','-c',code,root],{env,encoding:'utf8'});assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);};
  try{
    const fs=await import('node:fs');fs.mkdirSync(join(root,'inputs'));writeFileSync(join(root,'inputs','Protein.pdb'),pdb);writeFileSync(join(root,'inputs','蛋白😀.pdb'),pdb);
    for(const sameBytes of [false,true]){
      writeFileSync(join(root,'inputs','protein.ent'),sameBytes?pdb:pdb.replace('0.000','1.000'));
      const items=run("import json,sys;from pathlib import Path;from bioagent.intake import list_inputs;print(json.dumps(list_inputs(Path(sys.argv[1]),0,32)['items']))");
      assert.equal(items[0].id,items[1].id);
      const choices=(await catalog.loadPreviewChoices(async action=>({items:action==='inputs'?items:[],nextOffset:null}))).choices;
      const selected=await Promise.all(choices.map(c=>catalog.resolvePreviewChoice(null,c,'')));
      assert.equal(new Set(selected.map(r=>r.source.sourceId)).size,choices.length,'filename remains part of identity even for identical bytes');
      const refs=JSON.stringify(selected.map(r=>r.source));
      const actual=run("import json,sys;from pathlib import Path;from types import SimpleNamespace;from bioagent.visualization.sources import resolve_source;refs=json.loads("+JSON.stringify(refs)+");print(json.dumps([resolve_source(SimpleNamespace(project=Path(sys.argv[1])),ref)['topology'].name for ref in refs]))");
      assert.deepEqual(actual,choices.map(c=>c.input.path));
    }
  }finally{rmSync(root,{recursive:true,force:true});}
});
test('case-sensitive registered names keep distinct preview identities without changing intake IDs',async()=>{
  const upper={...input,path:'Protein.pdb'},lower={...input,path:'protein.pdb'};
  const results=await Promise.all([upper,lower].map(input=>catalog.resolvePreviewChoice(null,{kind:'input',input},'')));
  assert.notEqual(results[0].source.sourceId,results[1].source.sourceId);assert.equal(upper.id,lower.id);
});
test('catalog_pages_keep_choices',async()=>{
  assert.equal(typeof catalog.loadPreviewChoices,'function','source catalog is required');const calls=[];
  const api=async(action,args)=>{calls.push(action);if(action==='get')return {workflowId:'pending',revision:1,state:'RUNNING',stages:[],attempts:[]};return action==='inputs'?{items:[input,{...input,path:'Protein.pdb',sha256:'b'.repeat(64)}]}:{items:[{workflowId:args.offset?'md-b':'md-a',revision:1,state:'COMPLETED'},{workflowId:'pending',revision:1,state:'RUNNING'}],nextOffset:args.offset?null:100};};
  const first=await catalog.loadPreviewChoices(api),second=await catalog.loadPreviewChoices(api,{offset:first.nextOffset});
  const choices=[...first.choices,...second.choices];assert.equal(new Set(choices.map(c=>c.key)).size,4);assert.equal(choices.length,4);assert.equal(calls.filter(a=>a==='inputs').length,1);assert.ok(choices.filter(c=>c.kind==='input').every(c=>c.label.includes('.pdb')));
});
test('multiple_stages_require_selection',async()=>{
  assert.equal(typeof catalog.loadCompletedStages,'function','source catalog is required');
  const value={...workflow,stages:[...workflow.stages,{id:'equilibration',state:'COMPLETED'}],attempts:[...workflow.attempts,{stageId:'equilibration',attemptId:'eq',state:'COMPLETED',acceptance:{manifestSha256:'b'.repeat(64)}}]};
  const api=async(action,args)=>action==='get'?value:{sourceRef:{...ref,stageId:args.stageId,attemptId:args.stageId==='production'?'attempt':'eq',sourceSha256:args.stageId==='production'?hash:'b'.repeat(64)},attemptId:args.stageId==='production'?'attempt':'eq',items:[{role:'topology',path:'system.pdb'}],nextOffset:null};
  const stages=await catalog.loadCompletedStages(api,'md',1);assert.deepEqual(stages.map(s=>s.id),['production','equilibration']);
  await assert.rejects(catalog.resolvePreviewChoice(async()=>value,choice,''),/PREVIEW_STAGE_REQUIRED/);
});
test('human summary acceptance is verified by the authoritative artifacts API',async()=>{
  const summary={...workflow,attempts:workflow.attempts.map(({acceptance,...a})=>a)};
  const api=async action=>action==='get'?summary:{sourceRef:ref,attemptId:'attempt',items:[{role:'topology',path:'system.pdb'}],nextOffset:null};
  assert.deepEqual(await catalog.loadCompletedStages(api,'md',1),[{id:'production',label:'production'}]);
  assert.equal((await catalog.resolvePreviewChoice(api,choice,'production')).source.sourceSha256,hash);
  const denied=async action=>{if(action==='get')return summary;throw new Error('RESULT_NOT_ACCEPTED');};
  assert.deepEqual(await catalog.loadCompletedStages(denied,'md',1),[]);
});
test('stale_or_mixed_source_is_rejected',async()=>{
  assert.equal(typeof catalog.resolvePreviewChoice,'function','source catalog is required');
  await assert.rejects(catalog.resolvePreviewChoice(async()=>({...workflow,revision:2}),choice,'production'),/PREVIEW_SOURCE_CHANGED/);
  const api=async(action,args)=>action==='get'?workflow:{sourceRef:args.offset?{...ref,attemptId:'foreign'}:ref,attemptId:'attempt',items:args.offset?[{role:'trajectory',path:'md.dcd'}]:[{role:'topology',path:'system.pdb'}],nextOffset:args.offset?null:1};
  await assert.rejects(catalog.resolvePreviewChoice(api,choice,'production'),/PREVIEW_SOURCE_CHANGED/);
});
test('accepted paired filenames follow manifest pages and are rechecked',async()=>{
  assert.equal(typeof catalog.resolvePreviewChoice,'function','source catalog is required');
  const api=async(action,args)=>action==='get'?workflow:{sourceRef:ref,attemptId:'attempt',items:args.offset?[{role:'trajectory',path:'second.dcd'}]:[{role:'topology',path:'system.pdb'},{role:'trajectory',path:'first.dcd'}],nextOffset:args.offset?null:2};
  const result=await catalog.resolvePreviewChoice(api,choice,'production');assert.deepEqual(result,{source:ref,structureName:'system.pdb',trajectoryNames:['first.dcd','second.dcd']});
  const notAccepted={...workflow,attempts:[]};assert.deepEqual(await catalog.loadCompletedStages(async()=>notAccepted,'md',1),[]);
});
test('an accepted modeling stage is viewable before the whole workflow completes',async()=>{
  const active={...workflow,state:'RUNNING',stages:[{id:'production',state:'COMPLETED'},{id:'next',state:'RUNNING'}]};
  const api=async(action)=>action==='inputs'?{items:[]}:action==='list'?{items:[{workflowId:'md',revision:1,state:'RUNNING'}],nextOffset:null}:action==='get'?active:{sourceRef:ref,attemptId:'attempt',items:[{role:'topology',path:'modeled.pdb'}],nextOffset:null};
  const page=await catalog.loadPreviewChoices(api);assert.equal(page.choices.length,1);assert.equal(page.choices[0].workflowId,'md');
  const stages=await catalog.loadCompletedStages(api,'md',1);assert.deepEqual(stages,[{id:'production',label:'production'}]);
  const resolved=await catalog.resolvePreviewChoice(api,page.choices[0],'production');assert.equal(resolved.structureName,'modeled.pdb');assert.deepEqual(resolved.trajectoryNames,[]);
});
