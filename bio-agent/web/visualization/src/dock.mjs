import {createMolstarRenderer} from './renderer.mjs';
import {createPreviewTransport} from './transport.mjs';
import {createPreviewPlayer} from './player.mjs';
import {ViewCommandHandler} from '../../runtime/visualization/view-api.mjs';
import {createLocalConnectorClient} from './local-connector.mjs';
import {createDirectProvider} from './direct-provider.mjs';
import {loadPreviewChoices,loadCompletedStages,resolvePreviewChoice} from './source-catalog.mjs';
import {bindFloatingPanel} from './floating-panel.mjs';
import {defaultAppearance,validateAppearance} from '../../runtime/visualization/appearance.mjs';
import {styleTemplates,resolveStyleTemplate,matchStyleTemplate} from '../../runtime/visualization/style-templates.mjs';
import {createComputeProgressPanel} from './compute-progress-panel.mjs';
export async function createDock({sessionId,viewportId='main',clientId=crypto.randomUUID(),humanApi,onClose=()=>{},onObservation=()=>{},signal}){
  if(signal?.aborted)throw new Error('BIO_REQUEST_ABORTED');
  const element=document.createElement('aside');element.className='bio-viz-dock';element.setAttribute('aria-label','分子结构与轨迹');
  element.innerHTML='<header data-move tabindex="0" aria-label="移动预览窗口" title="拖动标题栏移动；方向键微调；双击复位"><strong>结构与轨迹</strong><button data-reset style="margin-left:auto" title="恢复窗口位置和大小">复位</button><button data-close>收起</button></header><div class="bio-viz-controls bio-viz-picker"><select data-source aria-label="结构或任务结果"></select><button data-more hidden>更多任务</button><select data-stage aria-label="已完成阶段" hidden></select><button data-open disabled>打开</button><button data-refresh>刷新清单</button></div><p class="bio-viz-note bio-viz-status" data-note role="status" aria-live="polite"></p><div class="bio-viz-content"><div class="bio-viz-canvas"></div><details class="bio-viz-settings"><summary>更多设置</summary><p class="bio-viz-note" data-source-info></p><div class="bio-viz-controls"><select data-atoms aria-label="预览原子集合"><option value="overview">蛋白骨架与已识别配体</option><option value="solute-heavy">溶质重原子</option><option value="all">全部原子</option></select><select data-fps aria-label="播放速度"><option>1</option><option>5</option><option selected>10</option><option>20</option></select><span>帧/秒</span><input data-start type="number" min="1" value="1" aria-label="详细窗口起始源帧" style="width:76px"><input data-end type="number" min="1" value="64" aria-label="详细窗口结束源帧" style="width:76px"><button data-detail>查看详细窗口</button><button data-overview>返回概览</button><button data-cancel hidden>取消生成</button></div></details></div><div class="bio-viz-controls bio-viz-playback"><button data-play disabled>播放</button><button data-pause>暂停</button><select data-representation aria-label="结构表示"><option value="cartoon">骨架</option><option value="sticks">球棍</option><option value="spheres">球形</option><option value="lines">线条</option></select><button data-focus>居中</button><input class="bio-viz-time" data-time type="range" min="0" max="0" value="0" aria-label="轨迹时间"><span data-readout>等待结构</span></div><button data-resize class="bio-viz-resize" aria-label="调整预览窗口大小" title="拖动调整宽高，比例不限；方向键微调">◢</button>';
  document.body.append(element);const find=n=>element.querySelector('[data-'+n+']');
  const pbcControls=document.createElement('span');pbcControls.className='bio-viz-pbc';
  pbcControls.innerHTML='<button data-pbc-raw aria-pressed="true" title="显示原始坐标" disabled>原始</button><button data-pbc-unwrap aria-pressed="false" title="恢复完整分子并连续展开轨迹" disabled>Unwrap</button>';
  element.querySelector('.bio-viz-playback').insertBefore(pbcControls,find('focus'));
  const templateControls=document.createElement('label');templateControls.className='bio-viz-template';templateControls.innerHTML='<span>风格模板</span><select data-style-template aria-label="风格模板" title="一键切换表示方式、配色和质感"></select>';
  element.querySelector('.bio-viz-playback').insertBefore(templateControls,find('play'));
  for(const [group,label]of [['bio','生物与分子'],['materials','材料 · LAMMPS']]){const options=document.createElement('optgroup');options.label=label;for(const p of styleTemplates.filter(p=>p.group===group))options.append(new Option(p.label,p.id));find('style-template').append(options);}
  const custom=new Option('自定义','custom');custom.disabled=true;find('style-template').append(custom);
  const appearanceControls=document.createElement('div');appearanceControls.className='bio-viz-appearance';
  appearanceControls.innerHTML='<select data-color-scheme aria-label="分子配色" title="配色"><option value="auto">自动配色</option><option value="element">元素色</option><option value="chain">链色</option><option value="residue">残基色</option><option value="rainbow">序列彩虹</option><option value="uniform">统一色</option></select><select data-material aria-label="材质质感" title="材质质感"><option value="matte">哑光</option><option value="glossy">亮面</option><option value="metal">金属感</option></select><input data-uniform-color type="color" value="#5f9fd8" aria-label="统一颜色" title="统一颜色" hidden>';
  element.querySelector('.bio-viz-playback').insertBefore(appearanceControls,find('time'));find('representation').value='cartoon';
  function setAppearanceControls(value,representation='cartoon'){const v=validateAppearance(value);find('color-scheme').value=v.colorScheme;find('material').value=v.material;find('uniform-color').value='#'+v.uniformColor.toString(16).padStart(6,'0');find('uniform-color').hidden=v.colorScheme!=='uniform';find('style-template').value=matchStyleTemplate(representation,v);}
  function readAppearance(){return validateAppearance({colorScheme:find('color-scheme').value,uniformColor:Number.parseInt(find('uniform-color').value.slice(1),16),material:find('material').value});}
  setAppearanceControls(defaultAppearance());
  for(const name of ['style-template','color-scheme','material','uniform-color'])find(name).disabled=true;
  const connection=document.createElement('div');connection.className='bio-viz-controls bio-viz-connection';
  connection.innerHTML='<input data-port type="number" min="1" max="65535" value="18773" aria-label="本地连接器端口"><input data-code type="password" autocomplete="off" aria-label="本地配对码" placeholder="本地配对码"><button data-connect>连接本地</button><button data-disconnect disabled>断开</button><span data-transport>未连接</span>';
  element.querySelector('.bio-viz-content').prepend(connection);
  let currentJob=null,closed=false,overview=null,overviewRequest=null,selectedSource=null,currentSource=null,currentResolved=null,catalog=[],nextOffset=null,selectionEpoch=0,loadEpoch=0,loading=new AbortController(),player,currentRequest=null,renderer;
  let connector=null,direct=null,catalogEpoch=0,catalogLoading=new AbortController(),controlsTail=Promise.resolve(),seekEpoch=0,pendingSeekIndex=null;
  let appearanceEpoch=0,pendingAppearance=0,pendingPbc=null,currentOperation=null;
  const progressPanel=createComputeProgressPanel({humanApi,signal});element.insertBefore(progressPanel.element,element.querySelector('.bio-viz-content'));
  element.querySelector('.bio-viz-content').insertBefore(progressPanel.body,element.querySelector('.bio-viz-canvas'));
  const stopPanel=bindFloatingPanel(element);
  const transport=createPreviewTransport({sessionId,viewportId,clientId,onTransport:value=>{if(!closed)find('transport').textContent='已连接';}});
  signal?.addEventListener('abort',close,{once:true});
  try{renderer=await createMolstarRenderer(element.querySelector('.bio-viz-canvas'),observation=>{if(!closed)onObservation({sessionId,viewportId,...player?.state,renderer:observation});});}
  catch(error){close();throw error;}
  if(closed){renderer.dispose();throw new Error('BIO_REQUEST_ABORTED');}
  player=createPreviewPlayer({renderer,transport,onState:state=>{
    if(closed)return;find('play').disabled=!state.ready||(player.manifest?.frames.length||0)<2;find('time').max=String(Math.max(0,(player.manifest?.frames.length||1)-1));find('time').value=String(pendingSeekIndex??state.frameIndex);
    const box=renderer.observe().boxOnly===true;find('representation').disabled=box;find('detail').disabled=!player.manifest;find('overview').disabled=!overview;find('time').disabled=box||(player.manifest?.frames.length||0)<2;
    syncAppearanceControls();syncPbcControls();
    find('readout').textContent=state.ready?`帧 ${state.sourceFrameNumber} / ${player.manifest?.sourceFrameCount??state.sourceFrameCount??1} · ${state.timePs.toFixed(3)} ps${state.buffering?' · 缓冲中':''}`:state.buffering?'正在缓冲…':'等待结构';
    if(state.errorCode)note(state.errorCode,true);onObservation({sessionId,viewportId,...state,renderer:renderer.observe()});
  }});
  function syncAppearanceControls(){
    const actual=renderer.observe();if(!pendingAppearance)setAppearanceControls(actual.appearance??defaultAppearance(),actual.representation??'cartoon');
    for(const name of ['style-template','color-scheme','uniform-color'])find(name).disabled=!player.state.ready||actual.boxOnly===true;
    find('material').disabled=!player.state.ready||actual.boxOnly===true||actual.representation==='lines';
  }
  function syncPbcControls(){
    const unwrapped=player?.manifest?.processing?.mode==='unwrap',busy=pendingPbc!==null&&pendingPbc===loadEpoch;
    const disabled=!player?.state.ready||!player?.manifest||renderer?.observe().boxOnly===true||busy;
    find('pbc-raw').setAttribute('aria-pressed',String(!unwrapped));find('pbc-unwrap').setAttribute('aria-pressed',String(unwrapped));
    find('pbc-raw').disabled=disabled||!unwrapped;find('pbc-unwrap').disabled=disabled||unwrapped;
  }
  function note(message,error=false){message=({CONNECTOR_UNAVAILABLE:'请先连接本地。',DIRECT_PAIR_DENIED:'配对码已失效，请重新连接。',LOCAL_NETWORK_DENIED:'请允许浏览器访问本地网络，再重新连接。',DIRECT_NOT_CONFIGURED:'尚未配置超算直连。',SSH_NETWORK_UNAVAILABLE:'超算连接已断开，请检查 SSH 或 VPN。',PBC_PYMOL_UNAVAILABLE:'计算端尚未配置 PyMOL。',PBC_CELL_REQUIRED:'轨迹缺少有效的周期盒。',PBC_TOPOLOGY_REQUIRED:'缺少可靠的分子键连接，无法恢复完整分子。',PBC_RUNTIME_CHANGED:'PyMOL 环境已变化，请重试。',PBC_TRAJECTORY_GAP:'轨迹存在缺帧，无法保证连续展开。',PBC_NATIVE_FAILED:'PyMOL 处理失败。',PBC_NATIVE_PROTOCOL:'PyMOL 返回的数据无效。',PREVIEW_CANCELED:'已取消。'})[message]||message;find('note').textContent=message;find('note').classList.toggle('bio-viz-error',error);}
  function requireConnection(){if(!connector?.pairingId){find('connect').focus();find('connect').scrollIntoView({block:'nearest'});throw new Error('CONNECTOR_UNAVAILABLE');}}
  const act=fn=>async()=>{try{await fn();}catch(error){if(!closed&&error.message!=='BIO_REQUEST_ABORTED')note(error.message,true);}};
  find('connect').onclick=act(async()=>{
    if(connector)throw new Error('请先断开现有本地连接');find('connect').disabled=true;
    const candidate=createLocalConnectorClient({port:Number(find('port').value),sessionId,clientId});
    try{await candidate.pair(find('code').value,signal);if(closed){candidate.dispose();return;}
      connector=candidate;direct=createDirectProvider({api:transport.api,connector,clientId});await player.pause();transport.setProvider(direct);
      find('code').value='';connection.classList.toggle('bio-viz-paired',true);find('disconnect').disabled=false;find('transport').textContent='已连接';note('');
    }catch(error){candidate.dispose();throw error;}finally{if(!closed)find('connect').disabled=!!connector;}
  });
  find('disconnect').onclick=act(async()=>{supersede();await player.pause();transport.setProvider(null);connector?.dispose();connector=null;direct=null;connection.classList.toggle('bio-viz-paired',false);find('code').value='';find('connect').disabled=false;find('disconnect').disabled=true;find('transport').textContent='未连接';note('');});
  function releaseOperation(operation=currentOperation){
    if(!operation)return;if(currentOperation===operation)currentOperation=null;
    if(!operation.done&&!operation.released){operation.released=true;void transport.api.cancelOperation({operationId:operation.operationId}).catch(()=>{});}
  }
  function supersede(){releaseOperation();loading.abort();loading=new AbortController();currentJob=null;pendingPbc=null;appearanceEpoch++;find('cancel').hidden=true;find('open').textContent='打开';return ++loadEpoch;}
  function check(token){if(closed||token!==loadEpoch||loading.signal.aborted)throw new Error('BIO_REQUEST_ABORTED');}
  const loadSignal=()=>signal?AbortSignal.any([signal,loading.signal]):loading.signal;
  async function ready(job,token,operationSignal,operation){check(token);currentJob=job.jobId;find('cancel').hidden=false;
    while(!closed&&['QUEUED','RUNNING'].includes(job.state)){check(token);note(job.state==='QUEUED'?'预览排队中…':'正在生成预览…');await new Promise(resolve=>setTimeout(resolve,500));check(token);job=await transport.api('status',{jobId:job.jobId},operationSignal);}
    if(operation)operation.done=['READY','FAILED','CANCELED'].includes(job.state);
    check(token);currentJob=null;find('cancel').hidden=true;if(job.state!=='READY')throw new Error(job.errorCode||'PREVIEW_NOT_READY');return job.manifest;
  }
  async function derive(action,args,mode,token,operationSignal){
    const operation=mode==='unwrap'?{operationId:crypto.randomUUID(),done:false,released:false}:null;
    if(operation)currentOperation=operation;
    try{const job=await transport.api(action,{...args,...(operation?{operationId:operation.operationId}:{})},operationSignal);check(token);return await ready(job,token,operationSignal,operation);}
    finally{releaseOperation(operation);}
  }
  function sourceChoice(source){return catalog.find(c=>source.kind==='input'?(c.previewSourceId===source.sourceId||c.input?.id===source.sourceId)&&c.input?.sha256===source.sourceSha256:c.workflowId===source.workflowId&&c.revision===source.revision);}
  function caption(source,manifest,resolved=currentResolved){
    const choice=sourceChoice(source),files=resolved?` · 结构 ${resolved.structureName}${resolved.trajectoryNames.length?' · 轨迹 '+resolved.trajectoryNames.join('、'):''}`:'';
    find('source-info').textContent=`当前：${choice?.label??source.sourceId}${source.stageId?' · '+source.stageId:''}${files} · ${manifest?.frames[0]?.timePs??0}–${manifest?.frames.at(-1)?.timePs??0} ps`;
    void progressPanel.setSource(source);
  }
  const render=(method,...args)=>player.render?player.render(method,...args):renderer[method](...args);
  async function loadSource(source,token,operationSignal,resolved=null){
    check(token);requireConnection();note('加载中…');const atomSet=find('atoms').value;await player.pause();check(token);const description=await transport.api('describe',{source},operationSignal);check(token);
    if(description.overviewReason==='WATER_BOX_ONLY'&&atomSet==='overview'){
      await player.openBox(description);check(token);currentSource=source;currentResolved=resolved;currentRequest=null;overview=null;overviewRequest=null;caption(source,null);
      note(description.boxVectorsAngstrom?'当前显示周期盒，可选择“全部原子”查看水分子。':'未找到周期盒，请选择“全部原子”。');return;
    }
    const request={source,profile:'overview',atomSet,startFrame:0,endFrame:description.sourceFrameCount-1,stride:1};
    const job=await transport.api('prepare',{request},operationSignal);check(token);const manifest=await ready(job,token,operationSignal);check(token);
    await player.open(manifest);check(token);
    currentSource=source;currentResolved=resolved;currentRequest=request;overview=manifest;overviewRequest=request;caption(source,manifest);
    await render('focus');check(token);element.querySelector('.bio-viz-canvas').scrollIntoView({block:'nearest'});
    note(description.gapCount?'轨迹时间不连续。':'');
  }
  async function openSource(source){const token=supersede();return loadSource(source,token,loadSignal());}
  const handler=new ViewCommandHandler({sessionId,viewportId,player,renderer,openSource});
  async function setPbc(mode){
    if(!player.manifest||!currentRequest)throw new Error('VIEWPORT_NOT_READY');
    if((player.manifest.processing?.mode??'raw')===mode)return;
    const oldManifest=player.manifest,request={...currentRequest},token=supersede(),operationSignal=loadSignal();
    if(mode==='unwrap')request.pbc='unwrap';else delete request.pbc;
    pendingPbc=token;syncPbcControls();let replacing=false,before;
    try{
      await player.pause();check(token);note(mode==='unwrap'?'正在展开…':'加载中…');
      const manifest=await derive('prepare',{request},mode,token,operationSignal);check(token);
      await controlsTail.catch(()=>{});check(token);await player.pause();check(token);
      const time=player.state.timePs;let target=manifest.frames[0];
      for(const frame of manifest.frames)if(Math.abs(frame.timePs-time)<Math.abs(target.timePs-time))target=frame;
      // Verify and cache the required files while the previous scene is still intact.
      await Promise.all([manifest.topologyFile,manifest.atomMapFile,target.segmentId].map(id=>transport.getFile(manifest,id,operationSignal)));check(token);
      before=handler.observe();replacing=true;await player.open(manifest);check(token);
      await player.seekTime(before.timePs);check(token);
      if(before.selection?.length){await render('select',before.selection);check(token);}
      currentRequest=request;if(request.profile==='overview'){overview=manifest;overviewRequest=request;}
      caption(currentSource,manifest);note('');
    }catch(error){
      if(replacing&&!closed&&token===loadEpoch){
        try{await player.open(oldManifest);check(token);await player.seekTime(before.timePs);check(token);if(before.selection?.length)await render('select',before.selection);}
        catch(rollback){if(rollback.message!=='BIO_REQUEST_ABORTED')throw new Error('PREVIEW_RESTORE_FAILED');}
      }
      throw error;
    }finally{if(pendingPbc===token)pendingPbc=null;if(!closed)syncPbcControls();}
  }
  const invoke=(kind,payload={},appearanceToken=null)=>{
    const sourceBinding=handler.observe().binding,token=kind==='seekTime'?++seekEpoch:null;
    const run=()=>{
      if(closed)throw new Error('BIO_REQUEST_ABORTED');if(token!==null&&token!==seekEpoch)return;
      if(appearanceToken!==null&&appearanceToken!==appearanceEpoch)return;
      const expected=handler.observe().binding;
      if(sourceBinding.sourceSha256!==expected.sourceSha256||sourceBinding.sourceRevision!==expected.sourceRevision)throw new Error('BIO_REQUEST_ABORTED');
      return handler.execute({operationId:crypto.randomUUID(),expected,kind,payload});
    };
    const next=controlsTail.catch(()=>{}).then(run);controlsTail=next;return next;
  };
  function showChoices(){
    const selected=find('source').value;
    const groups=['input','workflow'].map(kind=>{const group=document.createElement('optgroup');group.label=kind==='input'?'输入结构':'任务阶段结果';for(const c of catalog.filter(c=>c.kind===kind))group.append(new Option(c.label,c.key));return group;});
    find('source').replaceChildren(new Option('选择结构或轨迹',''),...groups);find('source').value=catalog.some(c=>c.key===selected)?selected:'';find('more').hidden=nextOffset===null;
  }
  async function choose(){
    const epoch=++selectionEpoch,token=supersede(),choice=catalog.find(c=>c.key===find('source').value),operationSignal=loadSignal();selectedSource=null;find('open').disabled=true;find('stage').hidden=true;
    await player.pause();check(token);if(epoch!==selectionEpoch)throw new Error('BIO_REQUEST_ABORTED');
    if(!choice){if(!currentSource)find('source-info').textContent='';return;}
    if(!currentSource)find('source-info').textContent='';
    if(choice.kind==='input'){selectedSource=choice;find('open').disabled=false;return;}
    const stages=await loadCompletedStages(humanApi,choice.workflowId,choice.revision,{signal:operationSignal});check(token);if(epoch!==selectionEpoch)throw new Error('BIO_REQUEST_ABORTED');
    selectedSource=choice;find('stage').replaceChildren(new Option('请选择阶段',''),...stages.map(s=>new Option(s.label,s.id)));find('stage').value=stages.length===1?stages[0].id:'';
    find('stage').hidden=stages.length<2;find('open').disabled=!find('stage').value;if(!stages.length)note('该任务暂无结果。');
  }
  find('source').onchange=act(choose);
  find('stage').onchange=()=>{selectionEpoch++;supersede();find('open').disabled=!find('stage').value;};
  async function refreshCatalog(append=false){
    if(append&&nextOffset===null)return;
    const token=++catalogEpoch,epoch=++selectionEpoch,stageId=find('stage').value;
    catalogLoading.abort();catalogLoading=new AbortController();supersede();
    const operationSignal=signal?AbortSignal.any([signal,catalogLoading.signal]):catalogLoading.signal;
    const current=()=>!closed&&token===catalogEpoch&&!operationSignal.aborted;
    const checkCatalog=()=>{if(!current()||epoch!==selectionEpoch)throw new Error('BIO_REQUEST_ABORTED');};
    for(const name of ['refresh','more','source','stage','open'])find(name).disabled=true;
    try{
      const page=await loadPreviewChoices(humanApi,{offset:append?nextOffset:0,signal:operationSignal});checkCatalog();
      const keys=new Set(catalog.map(c=>c.key));catalog=append?[...catalog,...page.choices.filter(c=>!keys.has(c.key))]:page.choices;
      nextOffset=page.nextOffset;showChoices();selectedSource=null;find('stage').hidden=true;
      const choice=catalog.find(c=>c.key===find('source').value);
      if(choice?.kind==='workflow'){
        const stages=await loadCompletedStages(humanApi,choice.workflowId,choice.revision,{signal:operationSignal});checkCatalog();
        find('stage').replaceChildren(new Option('请选择阶段',''),...stages.map(s=>new Option(s.label,s.id)));
        find('stage').value=stages.some(s=>s.id===stageId)?stageId:stages.length===1?stages[0].id:'';
        find('stage').hidden=stages.length<2;selectedSource=choice;
      }else if(choice)selectedSource=choice;
      if(!catalog.length)note('暂无结构或轨迹，请先上传文件。');
      else note('');
    }finally{
      if(current()){
        for(const name of ['refresh','more','source','stage'])find(name).disabled=false;
        find('open').disabled=!selectedSource||selectedSource.kind==='workflow'&&!find('stage').value;
      }
    }
  }
  find('refresh').onclick=act(()=>refreshCatalog());find('more').onclick=act(()=>refreshCatalog(true));
  find('open').onclick=act(async()=>{
    if(!selectedSource)throw new Error('PREVIEW_SOURCE_REQUIRED');requireConnection();const epoch=selectionEpoch,token=supersede(),choice=selectedSource,stageId=find('stage').value,operationSignal=loadSignal();
    find('open').disabled=true;find('open').textContent='加载中…';note('加载中…');
    try{const resolved=await resolvePreviewChoice(humanApi,choice,stageId,{signal:operationSignal});check(token);if(epoch!==selectionEpoch)throw new Error('BIO_REQUEST_ABORTED');await loadSource(resolved.source,token,operationSignal,resolved);}
    finally{if(!closed&&token===loadEpoch){find('open').textContent='打开';find('open').disabled=!selectedSource||selectedSource.kind==='workflow'&&!find('stage').value;}}
  });
  find('play').onclick=act(()=>invoke('play',{startFrame:0,endFrame:player.manifest.frames.length-1,fps:Number(find('fps').value),direction:1,loop:true}));
  find('pbc-raw').onclick=act(()=>setPbc('raw'));find('pbc-unwrap').onclick=act(()=>setPbc('unwrap'));
  find('pause').onclick=act(()=>invoke('pause'));find('time').oninput=act(async()=>{
    const index=Number(find('time').value),token=seekEpoch+1;pendingSeekIndex=index;
    try{await invoke('seekTime',{timePs:player.manifest.frames[index].timePs});}
    finally{if(token===seekEpoch){pendingSeekIndex=null;if(!closed)find('time').value=String(player.state.frameIndex);}}
  });
  const applyAppearanceChange=async()=>{
    const token=++appearanceEpoch,representation=find('representation').value,appearance=readAppearance();pendingAppearance++;find('uniform-color').hidden=appearance.colorScheme!=='uniform';
    try{await invoke('setRepresentation',{representation,appearance},token);}
    finally{pendingAppearance--;if(!closed){syncAppearanceControls();if(token===appearanceEpoch)find('representation').value=renderer.observe().representation??representation;}}
  },changeAppearance=act(applyAppearanceChange);
  find('style-template').onchange=act(async()=>{if(find('style-template').value==='custom')return;const p=resolveStyleTemplate(find('style-template').value);find('representation').value=p.representation;setAppearanceControls(p.appearance,p.representation);await applyAppearanceChange();});
  for(const name of ['representation','color-scheme','material'])find(name).onchange=changeAppearance;find('uniform-color').oninput=changeAppearance;
  find('focus').onclick=act(()=>invoke('focus'));find('cancel').onclick=act(async()=>{if(currentOperation)releaseOperation();else if(currentJob)await transport.api('cancel',{jobId:currentJob});});
  find('detail').onclick=act(async()=>{const start=Number(find('start').value)-1,end=Number(find('end').value)-1;
    if(!Number.isInteger(start)||start<0||!Number.isInteger(end)||end<start||end-start>=64)throw new Error('请选择最多 64 帧的详细窗口');
    if(!player.manifest)throw new Error('VIEWPORT_NOT_READY');const token=supersede(),operationSignal=loadSignal(),request=currentRequest,source=currentSource,previewId=player.manifest.previewId;
    await player.pause();check(token);const m=await derive('window',{previewId,start,end,stride:1},request?.pbc,token,operationSignal);check(token);
    await player.open(m);check(token);currentRequest={...request,profile:'detail',startFrame:start,endFrame:end,stride:1};caption(source,m);note('');});
  find('overview').onclick=act(async()=>{if(!overview)return;const token=supersede(),source=currentSource,mode=currentRequest?.pbc??'raw';let manifest=overview,request={...overviewRequest};
    await player.pause();check(token);
    if((manifest.processing?.mode??'raw')!==mode){if(mode==='unwrap')request.pbc='unwrap';else delete request.pbc;manifest=await derive('prepare',{request},mode,token,loadSignal());check(token);}
    await player.open(manifest);check(token);currentRequest=request;overview=manifest;overviewRequest=request;caption(source,manifest);});
  function close(){if(closed)return;closed=true;supersede();catalogEpoch++;catalogLoading.abort();progressPanel.dispose();stopPanel();signal?.removeEventListener('abort',close);if(player)player.dispose();else{transport.dispose();renderer?.dispose();}connector?.dispose();element.remove();onClose();}
  find('close').onclick=close;
  try{const page=await loadPreviewChoices(humanApi,{signal:loadSignal()});if(closed)throw new Error('BIO_REQUEST_ABORTED');catalog=page.choices;nextOffset=page.nextOffset;showChoices();
    if(!catalog.length)note('暂无结构或轨迹，请先上传文件。');
  }catch(error){if(closed)throw error;note(error.message,true);}
  syncPbcControls();
  return {element,player,renderer,transport,handler,clientId,openSource,close,showError:message=>note(message,true),
    metadata(){const o=handler.observe();if(!o.ready)return null;return {schemaVersion:'bio.view-state.v2',sessionId,viewportId,sourceSha256:o.binding.sourceSha256,source:currentSource,previewRequest:currentRequest,previewId:player.manifest?.previewId??null,timePs:o.timePs,sourceFrameIndex:o.sourceFrameIndex,representation:o.representation,appearance:o.appearance??defaultAppearance(),selection:o.selection,camera:o.camera};},
    async restore(state){
      const token=supersede(),operationSignal=loadSignal();selectionEpoch++;
      try{await player.pause();check(token);await transport.api('describe',{source:state.source},operationSignal);check(token);
        const choice=sourceChoice(state.source);
        if(choice){
          let stages=[];if(choice.kind==='workflow'){stages=await loadCompletedStages(humanApi,choice.workflowId,choice.revision,{signal:operationSignal});check(token);}
          find('source').value=choice.key;selectedSource=choice;find('stage').replaceChildren(new Option('请选择阶段',''),...stages.map(s=>new Option(s.label,s.id)));find('stage').value=state.source.stageId??'';find('stage').hidden=stages.length<2;find('open').disabled=choice.kind==='workflow'&&!stages.some(s=>s.id===state.source.stageId);
        }
        find('atoms').value=state.previewRequest?.atomSet??'overview';find('representation').value=state.representation;
        if(state.previewRequest?.profile==='detail'){find('start').value=String(state.previewRequest.startFrame+1);find('end').value=String(state.previewRequest.endFrame+1);}
        if(state.previewRequest){
          const request=state.previewRequest;if(JSON.stringify(request.source)!==JSON.stringify(state.source))throw new Error('PREVIEW_SOURCE_CHANGED');
          const m=await derive('prepare',{request},request.pbc,token,operationSignal);check(token);await player.open(m);check(token);
          currentSource=state.source;currentResolved=null;currentRequest=request;overview=request.profile==='overview'?m:null;overviewRequest=overview?request:null;caption(state.source,m);
          await player.seekTime(state.timePs);check(token);
        }else await loadSource(state.source,token,operationSignal);
        check(token);if(!renderer.observe().boxOnly){const appearance=validateAppearance(state.appearance??defaultAppearance());await render('setRepresentation',state.representation,appearance);check(token);setAppearanceControls(appearance,state.representation);await render('select',state.selection);check(token);}if(state.camera){await render('restoreCamera',state.camera);check(token);}note('');
      }catch(error){if(!closed&&error.message!=='BIO_REQUEST_ABORTED')note('恢复失败：'+error.message,true);}
    },observe:()=>handler.observe()};
}
