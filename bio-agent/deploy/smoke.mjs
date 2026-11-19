#!/usr/bin/env node
// No model request, grant, OpenMM Context or MD run; tool calls use the configured SSH node.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {packageAt,prepareServer,releaseRoot} from './server-launch.mjs';

const args=process.argv.slice(2),options={};
for(let i=0;i<args.length;i+=2){
  if(!['--state','--dsh-root','--connection','--execution-target'].includes(args[i])||options[args[i]]||!args[i+1])throw new Error('BIO_SMOKE_ARGUMENT_INVALID');
  options[args[i]]=args[i+1];
}
const config=await prepareServer({state:options['--state'],dshRoot:options['--dsh-root'],connectionFile:options['--connection'],executionTargetFile:options['--execution-target']});
const importPackage=async(name,versions)=>import(pathToFileURL(join(packageAt(options['--dsh-root'],name,versions),'lib/index.js')));
const [{Context},Prompt,Tools,Agents,{createScope},Bio,{projectForSession,toolNames}]=await Promise.all([
  importPackage('@deepseek-ai/cordis',['4.0.1','4.0.4']),importPackage('@deepseek-ai/dsh-system-prompt','0.1.0-rc.6'),
  importPackage('@deepseek-ai/dsh-tools','0.1.0-rc.6'),importPackage('@deepseek-ai/dsh-agent','0.1.0-rc.6'),
  importPackage('@deepseek-ai/dsh-scope','0.1.0-rc.6'),import(pathToFileURL(join(releaseRoot,'web/runtime/server-plugin.mjs'))),
  import(pathToFileURL(join(releaseRoot,'web/runtime/bio-plugin.mjs'))),
]);
const ctx=new Context(),prompt=ctx.plugin(Prompt.default,{persona:'GLOBAL CODING PERSONA'});await prompt.await();
const tools=ctx.plugin(Tools.default,{mode:'native'});await tools.await();
const agents=ctx.plugin(Agents.default,{});await agents.await();
// This harness selects explicit SSH mode; the default node mode must reject this local sentinel.
ctx.provide('subprocess',{spawn(){throw new Error('CONTROL_HOST_SPAWN_FORBIDDEN');}});
ctx.tools.register({name:'bash',description:'blocked smoke sentinel',parameters:{type:'object'},output:{schema:{},render:()=>[]},execute:async()=>{throw new Error('HOST_TOOL_EXECUTED');}});
const preset={},scope=createScope(ctx,preset),agent={id:'server-linux-smoke',session:{id:'server-linux-smoke'}};
const joined=createScope(ctx,agent,{parent:preset});
const run=async(name,arguments_)=>{
  const result=await ctx.tools.execute({name,arguments:arguments_,agent,signal:new AbortController().signal,callId:'offline-server-smoke'});
  return result;
};
try{
  const plugin=scope.ctx.plugin(Bio,{mode:'ssh',projectsRoot:config.projects,connectionFile:config.connectionFile,executionTargetFile:config.executionTargetFile,
    compilerRoot:config.dependencies.typebox,gateMode:'bio-grant-only'});await plugin.await();
  assert.deepEqual(ctx.tools.schemas(agent).map(row=>row.name).sort(),[...toolNames].sort());
  const denied=await run('bash',{});assert.equal(denied.isError,true);
  const invalid=await run('bio_capabilities',{grant:true});assert.equal(invalid.isError,true);
  const caps=await run('bio_capabilities',{});assert.equal(caps.value?.ok,true);
  const inputs=await run('bio_inputs',{offset:0,limit:100});assert.equal(inputs.value?.ok,true);
  const intent=JSON.parse(readFileSync(join(releaseRoot,'validation/chat-cases/intents/water.json'),'utf8'));
  intent.workflowId='server-isolated-water-draft';intent.title='Server migration DRAFT engineering probe';
  intent.researchGoal='Draft-only Linux deployment acceptance; no simulation or convergence claim.';
  const planned=await run('bio_plan',{intentJson:JSON.stringify(intent)});
  const scienceReady=caps.value.result.capabilities.dynamics;
  let planStatus;
  if(scienceReady){
    assert.equal(planned.value?.ok,true);assert.equal(planned.value.result.state,'DRAFT');assert.equal(planned.value.result.grantPresent,false);
    const submitted=await run('bio_submit',{workflowId:intent.workflowId,revision:1});
    assert.equal(submitted.value?.ok,false);assert.equal(submitted.value.error.code,'GRANT_REQUIRED');planStatus='DRAFT_AND_GRANT_REFUSAL_PASS';
  }else{
    assert.equal(planned.value?.ok,false);assert.equal(planned.value.error.code,'PREFLIGHT_BLOCKED');planStatus='MISSING_ENVIRONMENT_REFUSED';
  }
  const project=projectForSession(config.remote.projectsRoot,agent.id);
  const values=await run('bio_list',{offset:0,limit:100});assert.equal(values.value?.ok,true);
  const rows={workflows:values.value.result.items.length,grants:0,attempts:0};
  for(const value of values.value.result.items){
    const item=await run('bio_get',{workflowId:value.workflowId});assert.equal(item.value?.ok,true);
    if(item.value.result.grantPresent)rows.grants++;
    rows.attempts+=item.value.result.attempts.length;
  }
  assert.equal(rows.grants,0);assert.equal(rows.attempts,0);assert.equal(rows.workflows,scienceReady?1:0);
  console.log(JSON.stringify({schemaVersion:'bio.server-smoke.v1',componentAcceptance:'PASS',scientificEnvironmentReady:scienceReady,
    planStatus,tools:toolNames.length,hostToolsBlocked:true,invalidArgumentsBlocked:true,
    packages:caps.value.result.packages,capabilities:caps.value.result.capabilities,rows,
    executionTransport:'ssh',computeHost:config.remote.hostAlias,modelRequests:0,simulationExecuted:false,webUIAccepted:false,project,
    stateCountEvidence:'Fixed remote Bio list/get readback; independent database audit is a separate gate.'}));
}finally{await joined.dispose();await scope.dispose();await agents.dispose();await tools.dispose();await prompt.dispose();}
