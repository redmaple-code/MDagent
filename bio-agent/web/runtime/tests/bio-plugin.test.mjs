import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,symlinkSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {runInNewContext} from 'node:vm';
import {Context} from '@deepseek-ai/cordis';
import SystemPrompt,{renderPrompt} from '@deepseek-ai/dsh-system-prompt';
import Tools from '@deepseek-ai/dsh-tools';
import Agents from '@deepseek-ai/dsh-agent';
import {createScope} from '@deepseek-ai/dsh-scope';
import {execFileSync} from 'node:child_process';
import {compilerRoot} from '../../../../dsh-plugins/dsh-bio-agent/index.mjs';
import {waterIntent} from './fixtures.mjs';
import * as Bio from '../bio-runtime.mjs';
import {HumanWorkflowApi} from '../human-api.mjs';
import {ViewRuntime} from '../visualization/view-runtime.mjs';
import {loadCompiler} from '../../../deploy/compiler.mjs';
import {wireParameters,decodeWireArguments} from '../wire-contracts.mjs';
import {presentCall,presentResult} from '../tool-presentation.mjs';
const {SessionProjects,projectForSession,toolNames}=Bio;

const sdk={Compile:await loadCompiler(compilerRoot())};
const python=process.env.BIO_TEST_PYTHON||execFileSync('python3',['-c','import sys; print(sys.executable)'],{encoding:'utf8'}).trim();
const root=mkdtempSync(join(tmpdir(),'bio-web-plugin-test-'));
const options={projectsRoot:join(root,'projects'),python,Compile:sdk.Compile};
const exec=id=>({agent:{id,session:{id}},signal:new AbortController().signal,callId:'offline-test'});

test('session identity chooses an owned hash path and cannot select a caller cwd',()=>{
  const path=projectForSession(root,'../../outside');assert.equal(path.startsWith(root+'/'),true);
  assert.equal(path.slice(root.length+1).length,64);assert.notEqual(path,projectForSession(root,'another'));
});

test('invalid arguments and absent owner cannot start a bridge',async()=>{
  let created=0;const pool=new SessionProjects({...options,bridgeFactory:()=>{created++;throw new Error('should not start');}});
  await assert.rejects(pool.execute('bio_capabilities',{unknown:true},exec('args')),/INVALID_RAW_ARGUMENTS/);
  await assert.rejects(pool.execute('bio_capabilities',{}, {...exec('owner'),agent:undefined}),/BIO_SESSION_REQUIRED/);
  assert.equal(created,0);await pool.close();
});

test('gateway intent transport preserves the strict scientific contract',async()=>{
  const original=JSON.stringify(Bio.toolNames.map(name=>wireParameters(name)));
  const check=sdk.Compile(wireParameters('bio_plan'));
  const intent=waterIntent('water-draft');intent.workflowId='wire-draft';const encoded={intentJson:JSON.stringify(intent)};
  assert.equal(check.Check(encoded),true);assert.deepEqual(decodeWireArguments('bio_plan',encoded),{intent});
  assert.equal(check.Check({intent}),false);assert.equal(check.Check({...encoded,grant:true}),false);
  assert.throws(()=>decodeWireArguments('bio_plan',{intentJson:'invalid'}),/BIO_INTENT_JSON_INVALID/);
  const pool=new SessionProjects({...options,projectsRoot:join(root,'wire')});
  try{
    await assert.rejects(pool.execute('bio_plan',decodeWireArguments('bio_plan',{intentJson:'{}'}),exec('invalid')),/INVALID_RAW_ARGUMENTS/);
    assert.equal(existsSync(projectForSession(pool.root,'invalid')),false);
    const result=await pool.execute('bio_plan',decodeWireArguments('bio_plan',encoded),exec('wire'));
    assert.equal(result.ok,true);assert.equal(result.result.state,'DRAFT');
  }finally{await pool.close();}
  assert.equal(JSON.stringify(Bio.toolNames.map(name=>wireParameters(name))),original);
});

test('customer tool cards retain evidence without showing a full protocol or false success',()=>{
  const args={intentJson:JSON.stringify({workflowId:'customer-plan',privateDetail:'must stay in canonical evidence'})};
  assert.equal(presentCall('bio_plan',args).rawInput,'customer-plan');
  assert.equal(JSON.stringify(presentCall('bio_plan',args)).includes('privateDetail'),false);
  assert.doesNotThrow(()=>presentCall('bio_plan',{intentJson:'{"workflowId"'}));
  const canonical={ok:true,result:{state:'DRAFT'}};
  const result={isError:false,content:[{type:'text',text:JSON.stringify(canonical)}]};
  assert.match(presentResult('bio_plan',args,result).title,/草稿已保存/);
  assert.deepEqual(JSON.parse(result.content[0].text),canonical);
  const refused={isError:false,content:[{type:'text',text:JSON.stringify({ok:false,error:{code:'GRANT_REQUIRED'}})}]};
  assert.equal(presentResult('bio_submit',{},refused).title,'操作未完成（GRANT_REQUIRED）');
  assert.match(presentResult('bio_plan',{}, {...result,isError:true}).title,/未完成/);
  assert.match(presentResult('bio_plan',{}, {isError:false,content:[]}).title,/需核对/);
});

test('native UI slot renders durable and running Bio cards and opens the original inspector',()=>{
  let definition;const rows=new Map();
  const React={createElement:(type,props,...children)=>({type,props,children})};
  const source=readFileSync(new URL('../ui-cards/client.source.js',import.meta.url),'utf8').replace('__BIO_CARDS_PACKAGE_ID__','"unit-test-cards"');
  runInNewContext(source,{window:{__ModuleLoader__:{load:module=>{definition=module.factory(name=>{assert.equal(name,'react');return React;});}}}});
  definition.apply({slots:{inject:(_name,callback)=>callback(),register:(options,render)=>rows.set(options.key||options.id,render)}});
  assert.deepEqual([...rows.keys()].sort(),[...toolNames,'bio-workflow'].sort());
  const action=rows.get('bio-workflow');
  assert.equal(action({sessionId:'material',useSession:selector=>selector({blank:false}),useSessions:selector=>selector({byId:{material:{agentPreset:'md-sim-assistant'}}})}),null);
  assert.equal(typeof action({sessionId:'bio',useSession:selector=>selector({blank:false}),useSessions:selector=>selector({byId:{bio:{agentPreset:'bio'}}})}).type,'function');
  let inspected=false;const inspect=()=>{inspected=true;};
  const running=rows.get('bio_plan')({block:{callId:'pending',callView:presentCall('bio_plan',{})},inspect});
  assert.equal(running.props['aria-busy'],true);assert.equal(running.props['aria-label'],'拟定模拟方案');
  const settled=rows.get('bio_plan')({block:{kind:'tool',callView:presentCall('bio_plan',{intentJson:'{"workflowId":"customer-plan"}'}),resultView:{card:'generic',title:'方案草稿已保存（待审阅）'},isError:false},inspect});
  assert.match(settled.props['aria-label'],/草稿已保存.*customer-plan/);assert.equal(settled.props['aria-busy'],false);
  settled.props.onClick();assert.equal(inspected,true);
  const failed=rows.get('bio_submit')({block:{kind:'tool',callView:presentCall('bio_submit',{}),resultView:{card:'generic',title:'操作未完成（GRANT_REQUIRED）'},isError:false},inspect});
  assert.equal(failed.children[0].children[0],'!');
});

test('real bridge isolates sessions and reuses their durable input state',async()=>{
  const pool=new SessionProjects(options);
  try{
    const a=await pool.execute('bio_capabilities',{},exec('a'));assert.equal(a.ok,true);
    const b=await pool.execute('bio_inputs',{offset:0,limit:100},exec('b'));assert.equal(b.ok,true);
    assert.notEqual((await pool.get('a')).project,(await pool.get('b')).project);
    assert.equal((await pool.get('a')).bridge,(await pool.get('a')).bridge);
  }finally{await pool.close();}
});

test('owner changes and symbolic project paths fail closed',async()=>{
  const pool=new SessionProjects({...options,projectsRoot:join(root,'owner')});
  const record=await pool.get('changed');await pool.close();
  writeFileSync(join(record.project,'web-session-owner.json'),'{}\n');
  const reopened=new SessionProjects({...options,projectsRoot:join(root,'owner')});
  await assert.rejects(reopened.get('changed'),/BIO_SESSION_OWNER_CHANGED/);await reopened.close();
  const linkedRoot=join(root,'linked');symlinkSync(options.projectsRoot,linkedRoot);
  assert.throws(()=>new SessionProjects({...options,projectsRoot:linkedRoot}),/BIO_PROJECT_ROOT_SYMLINK/);
});

test('model-created DRAFT cannot submit without a real human grant',async()=>{
  const pool=new SessionProjects({...options,projectsRoot:join(root,'draft')});
  try{
    const intent=waterIntent('water-draft');
    const planned=await pool.execute('bio_plan',{intent},exec('draft'));assert.equal(planned.ok,true);assert.equal(planned.result.state,'DRAFT');
    const submitted=await pool.execute('bio_submit',{workflowId:intent.workflowId,revision:1},exec('draft'));
    assert.equal(submitted.ok,false);assert.equal(submitted.error.code,'GRANT_REQUIRED');
    const read=await pool.execute('bio_get',{workflowId:intent.workflowId},exec('draft'));assert.equal(read.result.state,'DRAFT');
  }finally{await pool.close();}
});

test('Cordis scoped catalog exposes exactly Bio tools and suppresses global shell/prompt',async()=>{
  const ctx=new Context();const prompt=ctx.plugin(SystemPrompt,{persona:'GLOBAL CODING PERSONA'});await prompt.await();
  const tools=ctx.plugin(Tools,{mode:'native'});await tools.await();const agents=ctx.plugin(Agents,{});await agents.await();
  let humanRpc,disposed=false;
  ctx.provide('connection',{rpc:{handle:(path,handler,options)=>{humanRpc={path,handler,options};return ()=>{disposed=true;};}}});
  ctx.tools.register({name:'bash',description:'forbidden',parameters:{type:'object'},output:{schema:{},render:()=>[]},execute:async()=>{throw new Error('must not execute');}});
  const preset={};const scope=createScope(ctx,preset);const agent={id:'scoped',session:{id:'scoped'}};const joined=createScope(ctx,agent,{parent:preset});
  let unregister;
  try{
    const agentContext=joined.ctx.plugin({name:'scoped-agent-context',inject:['tools','agents'],apply:context=>{agent.ctx=context;}});
    await agentContext.await();unregister=ctx.agents.register(agent);
    const plugin=scope.ctx.plugin({name:'scoped-bio-test',inject:Bio.inject,apply:ctx=>Bio.installBio(ctx,{projectsRoot:join(root,'scope'),python},sdk.Compile)});await plugin.await();
    assert.equal(humanRpc.path,'/bio-workflow');assert.equal(humanRpc.options.authority,'trusted-host');
    assert.deepEqual(ctx.tools.schemas(agent).map(tool=>tool.name).sort(),[...toolNames].sort());
    assert.deepEqual(ctx.tools.schemas({}).map(tool=>tool.name),['bash']);
    ctx.tools.register({name:'late_write',description:'forbidden late addition',parameters:{type:'object'},output:{schema:{},render:()=>[]},execute:async()=>{throw new Error('must not execute');}});
    assert.deepEqual(ctx.tools.schemas(agent).map(tool=>tool.name).sort(),[...toolNames].sort());
    const assembled=await ctx.systemPrompt.assemble({scope:agent,agent});const text=renderPrompt(assembled);
    assert.equal(text.includes('GLOBAL CODING PERSONA'),false);assert.equal(text.includes('生物分子模拟'),true);
    const denied=await ctx.tools.execute({name:'bash',arguments:{},...exec('scoped'),agent});assert.equal(denied.isError,true);
    const result=await ctx.tools.execute({name:'bio_inputs',arguments:{offset:0,limit:100},...exec('scoped'),agent});assert.equal(result.isError,false);assert.equal(result.value.ok,true);
    const humanContext=await humanRpc.handler('context',{sessionId:agent.id},new AbortController().signal);
    assert.equal(humanContext.ok,true,JSON.stringify(humanContext));assert.equal(humanContext.value.identityVerified,true);
    const humanInputs=await humanRpc.handler('inputs',{sessionId:agent.id},new AbortController().signal);
    assert.equal(humanInputs.ok,true,JSON.stringify(humanInputs));assert.deepEqual(humanInputs.value.items,[]);
  }finally{await unregister?.();await joined.dispose();await scope.dispose();await agents.dispose();await tools.dispose();await prompt.dispose();}
  assert.equal(disposed,true);
});

test('human panel checks real agent scope and denies foreign or revoked sessions before host actions',async()=>{
  const ctx=new Context(),prompt=ctx.plugin(SystemPrompt,{persona:'GLOBAL CODING PERSONA'});await prompt.await();
  const tools=ctx.plugin(Tools,{mode:'native'});await tools.await();const agents=ctx.plugin(Agents,{});await agents.await();
  const preset={},scope=createScope(ctx,preset),agent={id:'human-scope',session:{id:'human-scope'}};
  const joined=createScope(ctx,agent,{parent:preset}),material={id:'material-scope',session:{id:'material-scope'}};
  const materialScope=createScope(ctx,material);let unregister,unregisterMaterial;
  try{
    const context=joined.ctx.plugin({name:'human-agent-context',inject:['tools','agents'],apply:value=>{agent.ctx=value;}});
    const materialContext=materialScope.ctx.plugin({name:'material-agent-context',inject:['tools','agents'],apply:value=>{material.ctx=value;}});
    await context.await();await materialContext.await();
    unregister=ctx.agents.register(agent);unregisterMaterial=ctx.agents.register(material);
    const plugin=scope.ctx.plugin({name:'human-scope-bio',inject:Bio.inject,apply:value=>Bio.installBio(value,
      {projectsRoot:join(root,'human-scope'),python,humanApi:false},sdk.Compile)});await plugin.await();
    assert.deepEqual(agent.ctx.tools.schemas(agent).map(tool=>tool.name).sort(),[...toolNames].sort());
    assert.deepEqual(agent.ctx.tools.schemas(),[]);
    assert.deepEqual(material.ctx.tools.schemas(material),[]);
    // Real Cordis/Tools/Agents; project and host operations are sentinels, with no compute bridge.
    const calls=[],record={bridge:{control:async(name,args)=>{calls.push({name,args});return {ok:true,result:{identityVerified:true}};}}};
    let gets=0;
    const projects={guard:async()=>{},get:async()=>{gets++;return record;}};
    const api=new HumanWorkflowApi(ctx,projects,{tenantId:'local',toolNames});
    assert.deepEqual(await api.dispatch('context',{sessionId:agent.id}),{identityVerified:true});
    assert.equal(gets,1);assert.deepEqual(calls,[{name:'target',args:{}}]);
    for(const sessionId of ['missing',material.id])await assert.rejects(api.dispatch('context',{sessionId}),/BIO_SESSION_ACCESS_DENIED/);
    agent.session.id='mismatch';await assert.rejects(api.dispatch('context',{sessionId:agent.id}),/BIO_SESSION_ACCESS_DENIED/);agent.session.id=agent.id;
    assert.equal(gets,1);assert.equal(calls.length,1);
    projects.get=async()=>{gets++;await unregister();unregister=ctx.agents.register({...agent});return record;};
    await assert.rejects(api.dispatch('approve',{sessionId:agent.id,workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64)}),/BIO_SESSION_ACCESS_DENIED/);
    assert.equal(calls.length,1);
    await unregister();unregister=ctx.agents.register(agent);
    projects.get=async()=>{gets++;await plugin.dispose();return record;};
    await assert.rejects(api.dispatch('approve',{sessionId:agent.id,workflowId:'workflow',revision:1,reviewSha256:'a'.repeat(64)}),/BIO_SESSION_ACCESS_DENIED/);
    assert.equal(gets,3);assert.equal(calls.length,1);
    projects.get=async()=>{gets++;return record;};
    await assert.rejects(api.dispatch('context',{sessionId:agent.id}),/BIO_SESSION_ACCESS_DENIED/);
    assert.equal(gets,3);
  }finally{await unregisterMaterial?.();await unregister?.();await materialScope.dispose();await joined.dispose();await scope.dispose();await agents.dispose();await tools.dispose();await prompt.dispose();}
});

test('closed host refuses new project/bridge creation',async()=>{
  const pool=new SessionProjects({...options,projectsRoot:join(root,'closed')});await pool.close();
  await assert.rejects(pool.get('new'),/BIO_PLUGIN_CLOSED/);assert.equal(existsSync(projectForSession(join(root,'closed'),'new')),false);
});

test('opt-in display catalog is scoped while original human Bio qualification remains valid',async()=>{
  const ctx=new Context(),prompt=ctx.plugin(SystemPrompt,{});await prompt.await();const tools=ctx.plugin(Tools,{mode:'native'});await tools.await();const agents=ctx.plugin(Agents,{});await agents.await();
  const channels=new Map();ctx.provide('connection',{rpc:{handle:(path,handler)=>{channels.set(path,handler);return ()=>{};}}});
  const preset={},scope=createScope(ctx,preset),agent={id:'display-scope',session:{id:'display-scope'}};const joined=createScope(ctx,agent,{parent:preset});let unregister;
  try{
    await joined.ctx.plugin({name:'display-agent-context',inject:['tools','agents'],apply:context=>{agent.ctx=context;}}).await();unregister=ctx.agents.register(agent);
    await scope.ctx.plugin({name:'display-bio',inject:Bio.inject,apply:context=>Bio.installBio(context,{projectsRoot:join(root,'display-scope'),python,previewEnabled:true,viewEnabled:true},sdk.Compile)}).await();
    assert.deepEqual(agent.ctx.tools.schemas(agent).map(t=>t.name).sort(),[...toolNames,'bio_view','bio_observe'].sort());
    const human=await channels.get('/bio-workflow')('inputs',{sessionId:agent.id},new AbortController().signal);assert.equal(human.ok,true,JSON.stringify(human));
    const observed=await ctx.tools.execute({name:'bio_observe',arguments:{viewportId:'active'},...exec(agent.id),agent});assert.equal(observed.isError,true);assert.match(JSON.stringify(observed),/VIEWPORT_OFFLINE/);
    const displayRuntime=new ViewRuntime(ctx,{guard:async()=>{}},{toolNames});
    const pendingCheck=await displayRuntime.guard(agent.id,'bio_view');
    const restore=agent.ctx.tools.restrict({deny:['bio_view']});
    try{
      assert.throws(pendingCheck,/BIO_SESSION_ACCESS_DENIED/);await assert.rejects(displayRuntime.guard(agent.id,'bio_view'),/BIO_SESSION_ACCESS_DENIED/);
      await displayRuntime.guard(agent.id,'bio_observe');await displayRuntime.guard(agent.id,'view:attach');
      const manual=await channels.get('/bio-workflow')('inputs',{sessionId:agent.id},new AbortController().signal);assert.equal(manual.ok,true,JSON.stringify(manual));
      assert.ok(toolNames.every(name=>agent.ctx.tools.schemas(agent).some(t=>t.name===name)));
    }finally{restore();}
  }finally{await unregister?.();await joined.dispose();await scope.dispose();await agents.dispose();await tools.dispose();await prompt.dispose();}
});

test('standalone compiler preserves Bio planning and grant refusal without loading the model SDK',async()=>{
  const Compile=await loadCompiler(compilerRoot());
  assert.equal(Compile(wireParameters('bio_capabilities')).Check({grant:true}),false);
  const ctx=new Context(),prompt=ctx.plugin(SystemPrompt,{persona:'GLOBAL CODING PERSONA'});await prompt.await();
  const tools=ctx.plugin(Tools,{mode:'native'});await tools.await();const agents=ctx.plugin(Agents,{});await agents.await();
  const preset={},scope=createScope(ctx,preset),agent={id:'server-adapter',session:{id:'server-adapter'}};
  const joined=createScope(ctx,agent,{parent:preset});
  try{
    const plugin=scope.ctx.plugin({name:'standalone-bio-test',inject:Bio.inject,apply:ctx=>Bio.installBio(ctx,{projectsRoot:join(root,'server-adapter'),python},Compile)});await plugin.await();
    assert.deepEqual(ctx.tools.schemas(agent).map(tool=>tool.name).sort(),[...toolNames].sort());
    const intent=waterIntent('water-draft');intent.workflowId='standalone-server-draft';
    const planned=await ctx.tools.execute({name:'bio_plan',arguments:{intentJson:JSON.stringify(intent)},...exec(agent.id),agent});
    assert.equal(planned.value.ok,true);assert.equal(planned.value.result.state,'DRAFT');
    const submitted=await ctx.tools.execute({name:'bio_submit',arguments:{workflowId:intent.workflowId,revision:1},...exec(agent.id),agent});
    assert.equal(submitted.value.ok,false);assert.equal(submitted.value.error.code,'GRANT_REQUIRED');
  }finally{await joined.dispose();await scope.dispose();await agents.dispose();await tools.dispose();await prompt.dispose();}
});

test('internal injection uses the same real owned SessionProjects as native manual API',async()=>{
  const ctx=new Context(),prompt=ctx.plugin(SystemPrompt,{});await prompt.await();const tools=ctx.plugin(Tools,{mode:'native'});await tools.await();const agents=ctx.plugin(Agents,{});await agents.await();
  const preset={},scope=createScope(ctx,preset),agent={id:'injected',session:{id:'injected'}},joined=createScope(ctx,agent,{parent:preset});let unregister;
  const projects=new SessionProjects({...options,projectsRoot:join(root,'injected')});
  try{
    await joined.ctx.plugin({inject:['tools','agents'],apply:value=>{agent.ctx=value;}}).await();unregister=ctx.agents.register(agent);
    await scope.ctx.plugin({inject:Bio.inject,apply:value=>Bio.installBio(value,{projectsRoot:join(root,'unused'),python,humanApi:false},sdk.Compile,{projects})}).await();
    const api=new HumanWorkflowApi(ctx,projects,{tenantId:'local',toolNames});
    const result=await ctx.tools.execute({name:'bio_inputs',arguments:{offset:0,limit:100},...exec(agent.id),agent});assert.equal(result.value.ok,true);
    assert.equal(projects.records.size,1);assert.deepEqual((await api.dispatch('inputs',{sessionId:agent.id})).items,[]);
    await assert.rejects(api.dispatch('inputs',{sessionId:'foreign'}),/BIO_SESSION_ACCESS_DENIED/);
  }finally{await unregister?.();await joined.dispose();await scope.dispose();await projects.close();await agents.dispose();await tools.dispose();await prompt.dispose();}
});
console.log('Offline evidence retained at',root);
