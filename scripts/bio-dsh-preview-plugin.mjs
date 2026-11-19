/** Test-only native preset: actual Agent scopes, actual metadata and readonly stdio. */
import {spawn,spawnSync} from 'node:child_process';import {createHash} from 'node:crypto';
import {existsSync,readFileSync,writeFileSync,realpathSync} from 'node:fs';import {join,basename} from 'node:path';
import {SessionProjects,installBio,toolNames} from '../bio-agent/web/runtime/bio-runtime.mjs';
import {BridgeClient,scientificEnv} from '../bio-agent/agent/bridge-client.mjs';import {PreviewApi} from '../bio-agent/web/runtime/visualization/preview-api.mjs';
import {loadCompiler} from '../bio-agent/deploy/compiler.mjs';import {createConnectorServer} from '../bio-agent/local-connector/server.mjs';
import {loadConnectorConfig} from '../bio-agent/local-connector/config.mjs';import {createSshReader} from '../bio-agent/local-connector/ssh-reader.mjs';
import {installModelSelection} from '@deepseek-ai/dsh-agent';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
export const name='bio-native-preview-engineering';export const inject=['tools','systemPrompt','agents','webServer'];
const allowed=new Set(['connect','human:context','human:inputs','human:list','human:get','human:artifacts','bio_inputs','bio_list','bio_get','bio_artifacts',...'describe prepare status window cancel direct-open direct-permit direct-close'.split(' ').map(s=>'preview:'+s)]);
export async function engineeringGate(action){if(!allowed.has(action))throw new Error('ENGINEERING_READONLY');}
const hash=data=>createHash('sha256').update(data).digest('hex');
async function body(req){let size=0;const parts=[];for await(const part of req){size+=part.length;if(size>32768)throw new Error('NATIVE_BODY_LIMIT');parts.push(part);}return JSON.parse(Buffer.concat(parts));}
export function seedNativeTask(session,sessionId){
  const kind=sessionId==='session-native-structure'?'结构查看':'轨迹查看';
  // A closed fixture turn keeps DSH from reusing this demo as a blank new chat; no model step is entered.
  session.append('turn/start',{turn:0});
  session.append('user/message',createUserMessage({content:[{type:'text',text:`本地工程演示：${kind}任务。使用本任务页的“结构与轨迹”查看已登记结构和已完成阶段结果。数据由本地只读工程计算端提供；演示轨迹用于界面测试。真实超算连接和科学验收尚未配置，科学审批与计算提交保持禁用。`}],source:{kind:'plugin',plugin:name,form:'notice',summary:`本地工程演示 · ${kind}任务`}}),{surfaceOp:'append'});
  session.append('turn/end',{turn:0,reason:{kind:'aborted',reason:{kind:'hook',reason:'LOCAL_ENGINEERING_NO_MODEL'}}});
}
export async function apply(ctx,config){
  if(config.bootstrap){
    const handles=[];ctx.effect(()=>async()=>{await Promise.allSettled(handles.map(h=>h.dispose()));});
    void (async()=>{
      await ctx.get('loader')?.await();const selection=ctx.get('agentDefaultModel').currentSelection(),cwd=join(config.stateRoot,'dsh/workspace');
      const registry=ctx.get('workspaceRegistry'),workspace=await registry.resolveByPath(cwd)||await registry.create(cwd,'Bio');
      for(const sessionId of ['session-native-structure','session-native-trajectory']){
        const handle=await ctx.agents.create({sessionId,meta:{cwd,agentPreset:'bio'},agentOptions:{provider:selection.provider,model:selection.model},setup:async agentCtx=>{installModelSelection(agentCtx,{current:selection,assembled:undefined});await ctx.get('agentPresets').mount(agentCtx,'bio');}});
        seedNativeTask(handle.agent.session,sessionId);ctx.get('sessionTitle')?.rename(handle.agent.session,sessionId==='session-native-structure'?'蛋白结构查看（本地演示）':'结构与轨迹（本地演示）');
        handles.push(handle);await ctx.get('sessions').flush(handle.agent.session);await workspace.attachSession(sessionId);
      }
      writeFileSync(join(config.stateRoot,'native-sessions.json'),JSON.stringify({sessionIds:handles.map(h=>h.agent.id),modelRequests:0,realDshAgents:handles.every(h=>ctx.agents.get(h.agent.id)===h.agent)},null,2)+'\n',{mode:0o600});
    })().catch(error=>{console.error('NATIVE_BOOTSTRAP_FAILED',error.message);});return;
  }
  const {stateRoot,repoRoot,python,port,connectorPort}=config,bio=join(repoRoot,'bio-agent'),compute=join(stateRoot,'compute'),origin='http://127.0.0.1:'+port;
  const marker=JSON.parse(readFileSync(join(stateRoot,'.native-preview.json')));
  if(config.engineeringOnly!==true||marker.schemaVersion!=='bio.native-engineering.v1'||marker.repoRoot!==repoRoot||marker.port!==port||marker.connectorPort!==connectorPort||realpathSync(stateRoot)!==stateRoot||ctx.webServer.host!=='127.0.0.1')throw new Error('NATIVE_MARKER_REQUIRED');
  const executionTarget=JSON.parse(readFileSync(join(stateRoot,'target.json'))),Compile=await loadCompiler(config.compilerRoot),fixtures=new Map();let projects;
  projects=new SessionProjects({projectsRoot:compute,python,Compile,executionTarget,executionGate:async(action,id)=>{await engineeringGate(action);if(action==='connect'&&!projects.records.has(id)&&projects.records.size>=4)throw new Error('NATIVE_SESSION_LIMIT');},bridgeFactory:options=>{
    if(!existsSync(join(options.project,'native-fixture.json'))){
      const owner=JSON.parse(readFileSync(join(options.project,'web-session-owner.json')));
      const result=spawnSync(python,['-B',join(bio,'tests/direct_browser_fixture.py'),'--out',stateRoot,'--session-id',owner.sessionId],{encoding:'utf8',env:scientificEnv(python)});
      if(result.status!==0)throw new Error('NATIVE_FIXTURE_FAILED');writeFileSync(join(options.project,'native-fixture.json'),readFileSync(join(stateRoot,'fixture.json')),{flag:'wx',mode:0o600});
    }
    fixtures.set(basename(options.project),JSON.parse(readFileSync(join(options.project,'native-fixture.json'))));return new BridgeClient(options);
  }});
  await installBio(ctx,{projectsRoot:compute,python,executionTarget,previewEnabled:false,viewEnabled:false},Compile,{projects});
  const api=new PreviewApi(ctx,projects,{toolNames,directEnabled:true}),metrics={modelRequests:0,relayRequests:0,relayCoordinateBytes:0,observations:{}};
  ctx.on('agent/request',async(_payload,next)=>{metrics.modelRequests++;return next();});
  const connectorFile=join(stateRoot,'connector.json');writeFileSync(connectorFile,JSON.stringify({schemaVersion:'bio.local-connector.v1',allowedOrigin:origin,port:connectorPort,targets:[{targetId:executionTarget.targetId,hostKeyAlias:executionTarget.targetId,connection:JSON.parse(readFileSync(join(stateRoot,'connection.json')))}]}),{mode:0o600});
  const connector=createConnectorServer({config:loadConnectorConfig(connectorFile),readerFactory:options=>createSshReader({...options,spawnChild:(_exe,_argv,options)=>spawn(python,['-I','-B',join(bio,'run_biopreview_direct.py'),'--projects-root',compute,'--python',python],{...options,env:scientificEnv(python,options.env)})})});await connector.listen();
  const dist=join(bio,'web/visualization/dist'),assets=JSON.parse(readFileSync(join(dist,'asset-manifest.json')));
  const wrapper=`import {createDock as original} from '/v1/bio/visualization/assets/${assets.entry}';
export async function createDock(options){
 let started=0,lastAt=0,lastFrame=null,advances=0,unique=new Set(),stallMs=0,lastReport=0;
 const report=value=>fetch('/engineering/observe',{method:'POST',headers:{'content-type':'application/json','x-bio-session':options.sessionId},body:JSON.stringify(value)}).catch(()=>{});
 let dock;dock=await original({...options,onObservation:s=>{options.onObservation?.(s);const r=dock?.renderer.observe()||s.renderer,now=performance.now();if(s.playing&&r?.ready){if(!started){started=now;lastAt=now;lastFrame=r.sourceFrameIndex;}if(r.sourceFrameIndex!==lastFrame){stallMs+=Math.max(0,now-lastAt-150);lastAt=now;lastFrame=r.sourceFrameIndex;advances++;unique.add(lastFrame);}}
 const value={engineeringOnly:true,sessionId:options.sessionId,ready:!!r?.ready,playing:!!s.playing,frame:r?.sourceFrameIndex,timePs:r?.timePs,sourceSha256:r?.sourceSha256,cameraRevision:r?.cameraRevision,backgroundColor:r?.backgroundColor,representation:r?.representation,appearance:r?.appearance,representationStyles:r?.representationStyles,coordinateMode:r?.coordinateMode,previewId:dock?.player.manifest?.previewId,processing:dock?.player.manifest?.processing??null,cameraPose:r?.camera?{position:r.camera.position,target:r.camera.target,up:r.camera.up,radius:r.camera.radius}:null,elapsedMs:started?now-started:0,advances,uniqueFrames:unique.size,stallMs:stallMs+(started?Math.max(0,now-lastAt-150):0),errorCode:s.errorCode||null,build:'${assets.entry}'};
 document.querySelector('.bio-viz-dock')?.setAttribute('data-engineering-evidence',JSON.stringify(value));if(now-lastReport>1000||!s.playing){lastReport=now;report(value);}}});
 dock.element.querySelector('[data-port]').value='${connectorPort}';
 const response=await fetch('/engineering/pair',{method:'POST',headers:{'content-type':'application/json','x-bio-session':options.sessionId},body:'{}',signal:options.signal});if(response.ok){const {code}=await response.json();dock.element.querySelector('[data-code]').value=code;}
 const button=dock.element.querySelector('[data-connect]'),connect=button.onclick;
 button.onclick=async()=>{button.disabled=true;try{const response=await fetch('/engineering/pair',{method:'POST',headers:{'content-type':'application/json','x-bio-session':options.sessionId},body:'{}',signal:options.signal});if(!response.ok)throw new Error('NATIVE_PAIR_UNAVAILABLE');const {code}=await response.json();if(options.signal?.aborted)return;dock.element.querySelector('[data-code]').value=code;await connect();}catch(error){if(!options.signal?.aborted)dock.showError('连接失败，请重新打开窗口。');}finally{if(!options.signal?.aborted)button.disabled=!dock.element.querySelector('[data-disconnect]').disabled;}};
 return dock;
}`;
  const wrapperId='native-preview-engineering.js',wrapperBytes=Buffer.from(wrapper);writeFileSync(join(stateRoot,wrapperId),wrapperBytes,{mode:0o600});
  const saveMetrics=()=>writeFileSync(join(stateRoot,'experience.json'),JSON.stringify({...metrics,connector:connector.metrics,build:assets.entry,buildSha256:assets.assets.find(a=>a.assetId===assets.entry).sha256},null,2)+'\n',{mode:0o600});
  const trusted=req=>{if(req.headers.host!=='127.0.0.1:'+port||!['127.0.0.1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress))throw new Error('NATIVE_ORIGIN_REQUIRED');const requestOrigin=req.headers.origin||(req.headers.referer?new URL(req.headers.referer).origin:null);if(requestOrigin!==origin)throw new Error('NATIVE_ORIGIN_REQUIRED');};
  const scope=req=>{const sid=req.headers['x-bio-session'];api.scope(sid,ctx.agents.get(sid));return sid;};
  const handler=async(req,res)=>{const abort=new AbortController();req.once('aborted',()=>abort.abort());res.once('close',()=>{if(!res.writableEnded)abort.abort();});
    const json=value=>{res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(value));};
    try{trusted(req);const url=new URL(req.url,origin),path=url.pathname;
      if(path.startsWith('/v1/bio/visualization/')){
        if(!ctx.agents.list().some(a=>a.session?.id===a.id&&toolNames.every(n=>a.ctx.tools.schemas(a).some(t=>t.name===n))))throw new Error('BIO_SESSION_ACCESS_DENIED');
        if(req.method!=='GET'||url.search)throw new Error('NATIVE_ASSET_INVALID');
        if(path.endsWith('/manifest')){json({entry:wrapperId,styles:assets.styles});return;}
        const id=path.split('/').at(-1),record=assets.assets.find(a=>a.assetId===id);let bytes;
        if(id===wrapperId){bytes=readFileSync(join(stateRoot,id));if(hash(bytes)!==hash(wrapperBytes))throw new Error('NATIVE_ASSET_CHANGED');}
        else{if(!record||!/^[-a-zA-Z0-9_.]+\.(js|css)$/.test(id))throw new Error('NATIVE_ASSET_INVALID');bytes=readFileSync(join(dist,record.path));if(hash(bytes)!==record.sha256)throw new Error('NATIVE_ASSET_CHANGED');}
        res.writeHead(200,{'content-type':id.endsWith('.js')?'text/javascript':'text/css','cache-control':'no-store'});res.end(bytes);return;
      }
      const sid=scope(req);if(req.method==='GET'&&path.includes('/files/')){metrics.relayRequests++;throw new Error('PREVIEW_RELAY_DISABLED');}
      if(req.method!=='POST')throw new Error('NATIVE_REQUEST_INVALID');const value=await body(req);
      if(path==='/engineering/pair'){json({code:connector.pairing.issueCode()});return;}
      if(path==='/engineering/observe'){if(value.sessionId!==sid)throw new Error('BIO_SESSION_ACCESS_DENIED');metrics.observations[sid]=value;saveMetrics();json({recorded:true});return;}
      if(path.startsWith('/v1/bio/preview/')){if(url.search||Object.hasOwn(value,'sessionId')||Object.hasOwn(value,'viewportId'))throw new Error('INVALID_PREVIEW_REQUEST');json(await api.dispatch(path.split('/').at(-1),{...value,sessionId:sid,viewportId:req.headers['x-bio-viewport']},abort.signal));return;}
      throw new Error('NATIVE_ROUTE_NOT_FOUND');
    }catch(error){saveMetrics();if(!res.headersSent){res.writeHead(403,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify({error:/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'NATIVE_REQUEST_FAILED'}));}else res.destroy();}
  };
  for(const path of ['/v1/bio/preview','/v1/bio/visualization','/engineering'])ctx.effect(()=>ctx.webServer.register({kind:'prefix',path,handler}));
  const timer=setInterval(saveMetrics,1000);timer.unref();ctx.effect(()=>async()=>{clearInterval(timer);saveMetrics();await connector.close();});
}
