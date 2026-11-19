window.__ModuleLoader__.load({id:__BIO_CARDS_PACKAGE_ID__,factory:require=>{
  const React=require('react');
  const names=['bio_capabilities','bio_inputs','bio_inspect','bio_plan','bio_get','bio_list','bio_submit','bio_cancel','bio_continue','bio_artifacts','bio_report'];
  // These selectors belong to the pinned rc.6 settings packages. Keep upstream assets intact.
  if(typeof document!=='undefined'&&!document.querySelector('style[data-bio-web-settings]')){
    const style=document.createElement('style');style.dataset.bioWebSettings='responsive';
    style.textContent=`@media(max-width:680px){
      [role="dialog"].VOzbGW_panel{flex-direction:column;width:calc(100vw - 24px);max-width:calc(100vw - 24px);height:calc(100dvh - 24px)}
      .VOzbGW_panel .VOzbGW_nav{width:auto;padding:12px;gap:0;border-bottom:1px solid var(--dsw-alias-border-l2)}
      .VOzbGW_panel .VOzbGW_navTitle{display:none}
      .VOzbGW_panel .VOzbGW_navList{flex-direction:row;flex-wrap:wrap}
      .VOzbGW_panel .VOzbGW_navCell{flex:1 1 100px;height:34px;padding:6px 10px}
      .VOzbGW_panel .VOzbGW_content{min-height:0}
      .VOzbGW_panel .VOzbGW_header{height:44px;padding:8px 12px}
      .VOzbGW_panel .VOzbGW_options{padding:0 12px 16px;overflow-x:hidden}
      .VOzbGW_panel .zGbnIq_modelRow{grid-template-columns:minmax(0,1fr) auto}
      .VOzbGW_panel .zGbnIq_modelRow>input{grid-column:1 / -1;min-width:0}
    }`;
    document.head.appendChild(style);
  }
  function BioToolCard({block,inspect}){
    const done='kind' in block;
    const callView=block.callView;
    const resultView=done?block.resultView:undefined;
    const title=resultView?.title??callView?.title??'处理模拟请求';
    const failed=done&&(block.isError||title.startsWith('操作未完成'));
    const detail=callView?.rawInput;
    const label=title+(typeof detail==='string'?' · '+detail:'');
    return React.createElement('button',{type:'button',onClick:inspect,'aria-label':label,'aria-busy':!done,
      style:{display:'flex',gap:'8px',alignItems:'center',width:'100%',padding:'8px 10px',border:'1px solid var(--dsw-alias-border-l2)',borderRadius:'6px',background:'transparent',color:'inherit',font:'inherit',textAlign:'left',cursor:'pointer'}},
      React.createElement('span',{'aria-hidden':true},!done?'…':failed?'!':'›'),
      React.createElement('span',{style:{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}},label),
      React.createElement('span',{style:{marginLeft:'auto',fontSize:'12px',opacity:.65}},'详情'));
  }
  const h=React.createElement;
  const states={DRAFT:'待审阅',READY:'已批准',RUNNING:'运行中',COMPLETED:'已完成',FAILED:'失败',CANCELED:'已取消',CANCEL_PENDING:'正在取消',WAITING:'状态待核对',PAUSED:'已暂停',NEEDS_REVIEW:'需要重新审阅',UNKNOWN:'状态待核对',PENDING:'等待',CLAIMED:'准备执行',ACCEPTED:'已接受',BLOCKED:'受阻'};
  const errors={BIO_BRIDGE_UNAVAILABLE:'计算端暂时无法连接，请稍后刷新。',BIO_SESSION_ACCESS_DENIED:'当前会话没有 Bio 任务权限。',
    REVIEW_CHANGED:'方案或计算环境已改变，请重新审阅。',GRANT_CHANGED:'输入、环境或授权已改变，请重新生成方案并审阅。',
    TOOL_RESPONSE_UNKNOWN:'上次操作的结果尚未确认。请先恢复并核对远端任务。',GPU_NOT_AUTHORIZED:'该计算目标没有授权使用此 GPU。',
    ARTIFACT_CHANGED:'结果文件已改变，下载未完成。',INPUT_CONFLICT:'同名输入已存在且内容不同，请另存为新文件名。',
    SUBMISSION_SOURCE_REQUIRED:'此方案尚未提交，请先检查方案状态。',RESOURCE_POLICY_EXCEEDED:'所选资源超过计算目标配额。'};
  errors.LARGE_DOWNLOAD_NEEDS_FILE_PICKER='浏览器不支持直接保存大文件，请通过计算端已有的文件传输方式取回。';
  errors.BIO_PLATFORM_ACCESS_DENIED='原平台没有授权此操作，请检查当前账号的计算节点权限。';
  const errorText=error=>errors[error.message]||`操作未完成：${/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'连接或响应异常'}。`;
  const hash=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
  const encode=bytes=>{let value='';for(const n of bytes)value+=String.fromCharCode(n);return btoa(value);};
  const decode=value=>Uint8Array.from(atob(value),n=>n.charCodeAt(0));
  const size=value=>value<1048576?`${(value/1024).toFixed(1)} KB`:`${(value/1048576).toFixed(1)} MB`;
  function installPanelStyle(){
    if(document.querySelector('style[data-bio-task-panel]'))return;
    const style=document.createElement('style');style.dataset.bioTaskPanel='1';
    style.textContent=`.bio-task-shade{position:fixed;inset:0;z-index:1000;background:#0007;display:flex;align-items:center;justify-content:center;padding:12px}
      .bio-task-panel{background:var(--dsw-alias-bg-layer-1,#fff);color:var(--dsw-alias-label-primary,#0f1115);width:900px;max-width:100%;box-sizing:border-box;max-height:94dvh;overflow:auto;overflow-wrap:anywhere;border-radius:12px;box-shadow:0 12px 50px #0004;padding:20px;text-align:left;color-scheme:inherit}
      .bio-task-panel button,.bio-task-panel select,.bio-task-panel input{font:inherit;color:inherit;min-width:0;max-width:100%;box-sizing:border-box;padding:6px 10px;border:1px solid var(--dsw-alias-border-l2,#ccc);border-radius:6px;background:var(--dsw-alias-bg-layer-2,#f5f6f7)}
      .bio-task-panel input[type="file"]{width:100%}
      .bio-task-panel button{cursor:pointer}.bio-task-panel button:disabled{opacity:.45;cursor:default}
      .bio-task-panel section{border-top:1px solid var(--dsw-alias-border-l2,#ddd);margin-top:16px;padding-top:12px}
      .bio-task-panel h3{font-size:16px;margin:0 0 10px}.bio-task-panel p{margin:8px 0;line-height:1.5}
      .bio-task-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.bio-task-row>*{min-width:0;max-width:100%}.bio-task-list{display:grid;gap:6px;margin:10px 0}
      .bio-task-review{white-space:pre-wrap;overflow-wrap:anywhere;max-height:40dvh;overflow:auto;font-size:13px;line-height:1.55;padding:12px;border:1px solid var(--dsw-alias-border-l2,#ddd);border-radius:6px}
      .bio-task-error{color:var(--dsw-alias-state-error-primary,#b13630)}.bio-task-note{font-size:13px;opacity:.8}.bio-task-progress{font-size:13px;overflow-wrap:anywhere}
      @media(max-width:680px){.bio-task-panel{padding:14px}.bio-task-panel select{max-width:100%}.bio-task-review{max-height:32dvh}}`;
    document.head.appendChild(style);
  }
  function BioPanel({ctx,sessionId,close}){
    const [target,setTarget]=React.useState(null),[inputs,setInputs]=React.useState([]),[workflows,setWorkflows]=React.useState([]);
    const [workflow,setWorkflow]=React.useState(null),[review,setReview]=React.useState(null),[checked,setChecked]=React.useState(false);
    const [artifacts,setArtifacts]=React.useState([]),[stage,setStage]=React.useState(''),[next,setNext]=React.useState(null);
    const [artifactNext,setArtifactNext]=React.useState(null);
    const [busy,setBusy]=React.useState(false),[message,setMessage]=React.useState(''),[error,setError]=React.useState('');
    const [threads,setThreads]=React.useState(1),[platform,setPlatform]=React.useState('CPU'),[device,setDevice]=React.useState(0),[precision,setPrecision]=React.useState('mixed');
    const cancel=React.useRef(null),panel=React.useRef(null),file=React.useRef(null),running=React.useRef(false);
    const api=async(action,args={})=>{
      const result=await ctx.connection.rpc.call('/bio-workflow',action,{sessionId,...args},cancel.current?.signal);
      if(!result.ok)throw new Error(result.error.message||'BIO_OPERATION_FAILED');return result.value;
    };
    const job=async fn=>{
      if(running.current)return;running.current=true;setBusy(true);setError('');setMessage('');
      cancel.current=new AbortController();
      try{await fn();}catch(e){if(!cancel.current.signal.aborted)setError(errorText(e));}
      finally{running.current=false;setBusy(false);}
    };
    const loadWorkflow=async id=>{
      const value=await api('get',{workflowId:id});setWorkflow(value);setReview(null);setChecked(false);setArtifacts([]);setArtifactNext(null);
      const ready=value.stages.filter(s=>s.state==='COMPLETED');setStage(ready.at(-1)?.id||'');return value;
    };
    const loadList=async offset=>{
      const page=await api('list',{offset});setWorkflows(previous=>offset?[...previous,...page.items]:page.items);setNext(page.nextOffset);
    };
    const loadArtifacts=async offset=>{
      const page=await api('artifacts',{workflowId:workflow.workflowId,stageId:stage,offset});
      setArtifacts(previous=>offset?[...previous,...page.items]:page.items);setArtifactNext(page.nextOffset);
    };
    const refresh=async()=>{
      const info=await api('context');setTarget(info.target||info);
      if(info.resourcePreference){setThreads(info.resourcePreference.cpuThreads);setPlatform(info.resourcePreference.execution.platform);
      setDevice(info.resourcePreference.execution.deviceIndex??(info.target.resourcePolicy.gpuDevices[0]??0));setPrecision(info.resourcePreference.execution.precision||'mixed');}
      else setDevice(info.target.resourcePolicy.gpuDevices[0]??0);
      setInputs((await api('inputs')).items);await loadList(0);if(workflow)await loadWorkflow(workflow.workflowId);
    };
    React.useEffect(()=>{
      installPanelStyle();const previous=document.activeElement;panel.current?.focus();
      const keyboard=event=>{
        if(event.key==='Escape'){event.preventDefault();close();}
        if(event.key==='Tab'){
          const focus=[...panel.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]')];
          if(!focus.length){event.preventDefault();return;}
          if(event.shiftKey&&document.activeElement===focus[0]){event.preventDefault();focus.at(-1).focus();}
          if(!event.shiftKey&&document.activeElement===focus.at(-1)){event.preventDefault();focus[0].focus();}
        }
      };
      document.addEventListener('keydown',keyboard);void job(refresh);
      return()=>{cancel.current?.abort();document.removeEventListener('keydown',keyboard);previous?.focus();};
    },[sessionId]);
    React.useEffect(()=>{
      if(!workflow||!['READY','RUNNING','WAITING','CANCEL_PENDING'].includes(workflow.state))return;
      const timer=setInterval(()=>{
        if(!running.current)void job(async()=>setWorkflow(await api('get',{workflowId:workflow.workflowId})));
      },5000);
      return()=>clearInterval(timer);
    },[workflow?.workflowId,workflow?.state]);
    const upload=async source=>{
      if(!source)return;
      if(!/\.(pdb|ent)$/i.test(source.name)||source.size<1||source.size>64*1048576)throw new Error('STRUCTURE_FILENAME_INVALID');
      setMessage('读取结构文件…');const bytes=new Uint8Array(await source.arrayBuffer()),digest=await hash(bytes);
      const name=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,122}\.(pdb|ent)$/i.test(source.name)?source.name:`structure-${digest.slice(0,12)}.pdb`;
      const transfer=await api('upload-begin',{name,byteSize:bytes.length,sha256:digest});
      for(let offset=0;offset<bytes.length;offset+=transfer.chunkBytes){
        if(cancel.current.signal.aborted)return;
        const chunk=bytes.subarray(offset,Math.min(offset+transfer.chunkBytes,bytes.length));
        const received=await api('upload-chunk',{uploadId:transfer.uploadId,offset,data:encode(chunk)});
        if(received.offset!==offset+chunk.length)throw new Error('UPLOAD_OFFSET_MISMATCH');
        setMessage(`上传到计算端：${Math.round(received.offset/bytes.length*100)}%`);
      }
      const result=await api('upload-finish',{uploadId:transfer.uploadId});setInputs((await api('inputs')).items);
      setMessage(`已导入 ${result.path}。回到对话说明你想研究什么，Agent 会检查结构并生成方案。`);
    };
    const download=async(artifact,handle)=>{
      const transfer=await api('download-begin',{workflowId:workflow.workflowId,stageId:stage,path:artifact.path,sha256:artifact.sha256});
      if(!handle&&transfer.byteSize>128*1048576)throw new Error('LARGE_DOWNLOAD_NEEDS_FILE_PICKER');
      let writer;const chunks=[];let offset=0;
      try{
        if(handle)writer=await handle.createWritable();
        while(offset<transfer.byteSize){
          if(cancel.current.signal.aborted)throw new Error('BIO_REQUEST_ABORTED');
          const part=await api('download-chunk',{downloadId:transfer.downloadId,offset,limit:transfer.chunkBytes});
          const bytes=decode(part.data);
          if(part.offset!==offset||part.nextOffset!==offset+bytes.length||!bytes.length||await hash(bytes)!==part.chunkSha256)throw new Error('ARTIFACT_CHANGED');
          if(writer)await writer.write(bytes);else chunks.push(bytes);
          offset=part.nextOffset;setMessage(`下载 ${transfer.name}：${Math.round(offset/transfer.byteSize*100)}%`);
        }
        if(offset!==transfer.byteSize)throw new Error('ARTIFACT_CHANGED');
        const verified=await api('download-finish',{downloadId:transfer.downloadId});
        if(!verified.verified||verified.sha256!==transfer.sha256||verified.byteSize!==offset)throw new Error('ARTIFACT_CHANGED');
        if(writer){await writer.close();writer=null;}
        else{
          const blob=new Blob(chunks);if(await hash(await blob.arrayBuffer())!==transfer.sha256)throw new Error('ARTIFACT_CHANGED');
          const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=transfer.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
        }
        setMessage(`${transfer.name} 下载完成，已校验文件完整性。`);
      }finally{if(writer)await writer.abort();}
    };
    const chooseDownload=async artifact=>{
      // The picker must be opened by the click gesture, before an asynchronous RPC.
      if(running.current)return;
      try{
        const handle=window.showSaveFilePicker?await window.showSaveFilePicker({suggestedName:artifact.path.split('/').at(-1)}):null;
        await job(()=>download(artifact,handle));
      }catch(e){if(e.name!=='AbortError')setError(errorText(e));}
    };
    const button=(label,fn,disabled=false,key)=>h('button',{key,type:'button',disabled:busy||disabled,onClick:()=>job(fn)},label);
    return h('div',{className:'bio-task-shade',onClick:e=>{if(e.target===e.currentTarget)close();}},
      h('div',{className:'bio-task-panel',role:'dialog','aria-modal':true,'aria-labelledby':'bio-task-title',tabIndex:-1,ref:panel},
        h('div',{className:'bio-task-row'},h('h3',{id:'bio-task-title',style:{marginRight:'auto'}},'Bio 任务'),button('刷新',refresh),h('button',{type:'button',onClick:close},'关闭')),
        target?h('p',{className:'bio-task-note'},`计算目标：${target.targetId} · ${target.nodeId} · 版本 ${target.targetRevision}。构建、拓扑、运行和分析在该计算端执行。`):h('p',null,'正在连接计算端…'),
        error&&h('p',{className:'bio-task-error',role:'alert'},error),message&&h('p',{'aria-live':'polite'},message),
        h('section',null,h('h3',null,'结构输入'),h('p',{className:'bio-task-note'},'上传标准蛋白或小肽 PDB / ENT，单文件最多 64 MB。原始文件保留；科学适用性由 Agent 检查。'),
          h('input',{ref:file,type:'file',accept:'.pdb,.ent',disabled:busy,onChange:e=>{const source=e.target.files[0];e.target.value='';void job(()=>upload(source));}}),
          h('div',{className:'bio-task-list'},...inputs.map(input=>h('div',{key:input.path},`${input.path} · ${size(input.byteSize)}`)))),
        target&&h('section',null,h('h3',null,'后续方案的计算资源'),h('div',{className:'bio-task-row'},
          h('label',null,'CPU 线程 ',h('input',{type:'number',min:1,max:Math.min(64,target.resourcePolicy.cpuThreads),value:threads,disabled:busy,onChange:e=>setThreads(Number(e.target.value)),style:{width:80}})),
          h('select',{'aria-label':'动力学平台',disabled:busy,value:platform,onChange:e=>setPlatform(e.target.value)},h('option',{value:'CPU'},'CPU'),target.resourcePolicy.gpuDevices.length>0&&h('option',{value:'CUDA'},'CUDA GPU')),
          platform==='CUDA'&&h('select',{'aria-label':'GPU 编号',disabled:busy,value:device,onChange:e=>setDevice(Number(e.target.value))},...target.resourcePolicy.gpuDevices.map(id=>h('option',{key:id,value:id},`GPU ${id}`))),
          platform==='CUDA'&&h('select',{'aria-label':'计算精度',disabled:busy,value:precision,onChange:e=>setPrecision(e.target.value)},...['mixed','double','single'].map(value=>h('option',{key:value,value},value))),
          button('保存资源偏好',async()=>{await api('resource-preference',{cpuThreads:threads,execution:platform==='CPU'?{platform}:{platform,deviceIndex:device,precision}});setMessage('已保存。请在对话中生成新方案；现有方案和授权保持原配置。');})),
          h('p',{className:'bio-task-note'},'资源偏好用于新草案。CPU 总线程、并发数和 GPU 使用仍受计算端配额限制。')),
        h('section',null,h('h3',null,'方案与任务'),
          h('div',{className:'bio-task-list'},...workflows.map(item=>button(`${item.workflowId} · v${item.revision} · ${states[item.state]||item.state}`,()=>loadWorkflow(item.workflowId),false,item.workflowId))),
          !workflows.length&&h('p',null,'还没有方案。上传结构后，在对话中说明研究目标并让 Agent 生成方案。'),next!==null&&button('更多方案',()=>loadList(next)),
          workflow&&h('div',null,h('p',null,`${workflow.workflowId} / v${workflow.revision}：${states[workflow.state]||workflow.state}${workflow.resourceWaiting?' · 等待计算资源':''}`),
            ...workflow.stages.map(s=>h('p',{key:s.id,className:'bio-task-progress'},`${s.id}：${states[s.state]||s.state}`)),
            ...workflow.attempts.filter(a=>Number.isFinite(a.progress?.stageStep)).map(a=>h('p',{key:a.attemptId,className:'bio-task-progress'},`${a.stageId}：已完成 ${a.progress.stageStep} / ${a.progress.totalSteps} 步`)),
            h('div',{className:'bio-task-row'},button('查看完整方案',async()=>{setReview(await api('review',{workflowId:workflow.workflowId,revision:workflow.revision}));setChecked(false);}),
              button('提交计算',async()=>{await api('submit',{workflowId:workflow.workflowId,revision:workflow.revision});await loadWorkflow(workflow.workflowId);await loadList(0);setMessage('已提交。任务状态以计算端记录为准。');},workflow.state!=='READY'),
              button('取消任务',async()=>{await api('cancel',{workflowId:workflow.workflowId});await loadWorkflow(workflow.workflowId);await loadList(0);},!['READY','RUNNING','WAITING','UNKNOWN'].includes(workflow.state)),
              button('恢复并核对任务',async()=>{const recovered=await api('recover',{workflowId:workflow.workflowId});await loadWorkflow(workflow.workflowId);await loadList(0);setMessage(recovered.explicitSubmitRequired?'已确认没有未决提交。需要开始计算时，请点击“提交计算”。':'已核对远端状态；后续阶段仍受原授权和预算约束。');}))),
          review&&h('div',null,h('pre',{className:'bio-task-review',tabIndex:0},review.summaryText||review.text),
            review.summaryText&&h('details',null,h('summary',null,'完整参数与来源'),h('pre',{className:'bio-task-review',tabIndex:0},review.text)),
            h('p',null,h('label',null,h('input',{type:'checkbox',checked,disabled:busy,onChange:e=>setChecked(e.target.checked)}),' 我已核对完整方案、输入、计算目标和资源预算。')),
            button('批准此版本',async()=>{await api('approve',{workflowId:review.workflowId,revision:review.revision,reviewSha256:review.reviewSha256});await loadWorkflow(review.workflowId);await loadList(0);setMessage('已批准此版本。需要开始计算时，请另行点击“提交计算”。');},!checked||review.state!=='DRAFT'))),
        workflow&&h('section',null,h('h3',null,'计算结果'),h('div',{className:'bio-task-row'},
          h('select',{'aria-label':'已完成阶段',value:stage,disabled:busy,onChange:e=>{setStage(e.target.value);setArtifacts([]);setArtifactNext(null);}},h('option',{value:''},'选择已完成阶段'),...workflow.stages.filter(s=>s.state==='COMPLETED').map(s=>h('option',{key:s.id,value:s.id},s.id))),
          button('查看产物',()=>loadArtifacts(0),!stage)),
          h('div',{className:'bio-task-list'},...artifacts.map(a=>h('div',{key:a.path,className:'bio-task-row'},h('span',{style:{overflowWrap:'anywhere',flex:1}},`${a.path} · ${size(a.byteSize)}`),h('button',{type:'button',disabled:busy,onClick:()=>chooseDownload(a)},'下载')))),
          artifactNext!==null&&button('更多产物',()=>loadArtifacts(artifactNext)),
          h('p',{className:'bio-task-note'},'文件保留在计算端。大轨迹需浏览器支持直接保存到文件；不支持时请通过计算端已有的文件传输方式取回。'))));
  }
  function BioAction({ctx,sessionId}){
    const [open,setOpen]=React.useState(false);
    const dock=React.useRef(null),lifecycle=React.useRef(null),[loading,setLoading]=React.useState(false),[vizError,setVizError]=React.useState('');
    React.useEffect(()=>setOpen(false),[sessionId]);
    function release(operation){if(!operation||operation.disposed)return;operation.disposed=true;operation.controller.abort();operation.detach?.();operation.dock?.close();if(dock.current===operation.dock)dock.current=null;}
    React.useEffect(()=>{const life={sessionId,disposed:false,operation:null};lifecycle.current=life;setLoading(false);setVizError('');return ()=>{life.disposed=true;release(life.operation);if(lifecycle.current===life)lifecycle.current=null;};},[sessionId]);
    const visualize=async()=>{
      const life=lifecycle.current;if(!life||life.sessionId!==sessionId||life.disposed)return;
      if(dock.current){release(life.operation);return;}
      const operation={controller:new AbortController(),disposed:false,dock:null,detach:null};life.operation=operation;
      const current=()=>lifecycle.current===life&&!life.disposed&&!operation.disposed&&!operation.controller.signal.aborted;
      const check=()=>{if(!current())throw new Error('BIO_REQUEST_ABORTED');};
      setLoading(true);setVizError('');
      try{
        check();
        const response=await fetch('/v1/bio/visualization/manifest',{credentials:'same-origin',signal:operation.controller.signal});check();
        if(!response.ok)throw new Error('PREVIEW_NOT_ENABLED');const assets=await response.json();check();
        for(const name of assets.styles){if(!/^[a-zA-Z0-9_.-]+\.css$/.test(name))throw new Error('PREVIEW_ASSET_MANIFEST_INVALID');if(!document.querySelector('link[data-bio-asset="'+name+'"]')){const link=document.createElement('link');link.rel='stylesheet';link.href='/v1/bio/visualization/assets/'+name;link.dataset.bioAsset=name;document.head.append(link);}}
        if(!/^[a-zA-Z0-9_.-]+\.js$/.test(assets.entry))throw new Error('PREVIEW_ASSET_MANIFEST_INVALID');
        const module=await import('/v1/bio/visualization/assets/'+assets.entry);check();
        const viewKey='bio-viewport:'+sessionId;let viewportId=sessionStorage.getItem(viewKey);if(!viewportId){viewportId='view-'+crypto.randomUUID();sessionStorage.setItem(viewKey,viewportId);}
        const created=await module.createDock({sessionId,viewportId,clientId:crypto.randomUUID(),signal:operation.controller.signal,humanApi:async(action,args={},requestSignal)=>{check();if(requestSignal?.aborted)throw new Error('BIO_REQUEST_ABORTED');const rpcSignal=requestSignal?AbortSignal.any([operation.controller.signal,requestSignal]):operation.controller.signal;const reply=await ctx.connection.rpc.call('/bio-workflow',action,{sessionId,...args},rpcSignal);check();if(requestSignal?.aborted)throw new Error('BIO_REQUEST_ABORTED');if(!reply.ok)throw new Error(reply.error.message);return reply.value;},onClose:()=>{release(operation);if(lifecycle.current===life&&!life.disposed)setLoading(false);}});
        operation.dock=created;if(!current()){created.close();throw new Error('BIO_REQUEST_ABORTED');}dock.current=created;
      }catch(error){if(current()){release(operation);setVizError(error.message==='PREVIEW_NOT_ENABLED'?'此部署尚未启用 3D 预览。':errorText(error));}}finally{if(lifecycle.current===life&&!life.disposed&&life.operation===operation)setLoading(false);}
    };
    return h(React.Fragment,null,h('button',{type:'button',onClick:()=>setOpen(true),title:'结构输入、方案审批和计算结果',style:{font:'inherit',padding:'5px 10px',border:'1px solid var(--dsw-alias-border-l2)',borderRadius:6,background:'transparent',color:'inherit',cursor:'pointer'}},'Bio 任务'),
      h('button',{type:'button',disabled:loading,onClick:visualize,style:{font:'inherit',padding:'5px 10px',border:'1px solid var(--dsw-alias-border-l2)',borderRadius:6,background:'transparent',color:'inherit',cursor:'pointer'}},loading?'加载预览…':'结构与轨迹'),
      vizError&&h('span',{role:'status',style:{fontSize:12}},vizError),
      open&&h(BioPanel,{key:sessionId,ctx,sessionId,close:()=>setOpen(false)}));
  }
  return {inject:['slots','connection'],apply(ctx){
    for(const key of names)ctx.slots.inject('tool.call.toolview',()=>ctx.slots.register({name:'tool.call.toolview',key},BioToolCard));
    ctx.slots.inject('conversation.session.header.actions',()=>ctx.slots.register({name:'conversation.session.header.actions',id:'bio-workflow',order:25},
      props=>{
        const preset=props.useSessions(state=>state.byId[props.sessionId]?.agentPreset),home=props.useSession(state=>state.blank&&state.composerPhase==='blank');
        return preset==='bio'&&!home?h(BioAction,{ctx,sessionId:props.sessionId}):null;
      }));
  }};
}});
