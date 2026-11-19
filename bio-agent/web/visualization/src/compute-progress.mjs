const fail=code=>{throw new Error(code);};
const id=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$/.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const active=new Set(['CLAIMED','ACCEPTED','RUNNING','UNKNOWN','CANCEL_PENDING']);
const terminal=new Set(['COMPLETED','FAILED','CANCELED']);
const states=new Set([...active,...terminal,'DRAFT','PENDING','READY','WAITING','BLOCKED','SKIPPED','PAUSED','NEEDS_REVIEW']);
const kindLabels={prepare:'建模与准备',minimize:'能量最小化',equilibrate:'平衡',production:'生产模拟',validate:'结果校验',analysis:'结果分析'};

export function describeProgress(snapshot,binding){
  if(snapshot?.workflowId!==binding?.workflowId||snapshot?.revision!==binding?.revision)fail('PROGRESS_SOURCE_CHANGED');
  if(snapshot.schemaVersion!=='bio.compute-progress.v1'||!states.has(snapshot.state)||!integer(snapshot.observedAtEpochMs)
    ||!Array.isArray(snapshot.stages)||snapshot.stages.length<1||snapshot.stages.length>128)fail('PROGRESS_INVALID');
  const ids=new Set();
  for(const stage of snapshot.stages){
    if(!id(stage.id)||ids.has(stage.id)||!states.has(stage.state)||typeof stage.kind!=='string'
      ||stage.attemptId!==null&&!id(stage.attemptId)||stage.attemptState!==null&&!states.has(stage.attemptState)
      ||stage.totalSteps!==null&&(!integer(stage.totalSteps)||stage.totalSteps===0))fail('PROGRESS_INVALID');
    ids.add(stage.id);
    const p=stage.progress;
    if(p!==null&&(!p||!stage.attemptId||!['equilibrate','production'].includes(stage.kind)
      ||!integer(p.stageStep)||!integer(p.totalSteps)||p.totalSteps!==stage.totalSteps||p.stageStep>p.totalSteps
      ||typeof p.elapsedSeconds!=='number'||!Number.isFinite(p.elapsedSeconds)||p.elapsedSeconds<0||!integer(p.publishedAtEpochMs)))fail('PROGRESS_INVALID');
  }
  if(binding.viewStageId&&!ids.has(binding.viewStageId))fail('PROGRESS_SOURCE_CHANGED');
  if(snapshot.currentStageId!==undefined&&!ids.has(snapshot.currentStageId))fail('PROGRESS_INVALID');
  const completedStages=snapshot.stages.filter(s=>s.state==='COMPLETED').length;
  if(snapshot.state==='COMPLETED'&&completedStages!==snapshot.stages.length)fail('PROGRESS_INVALID');
  const stage=snapshot.stages.find(s=>s.id===snapshot.currentStageId)??snapshot.stages.find(s=>active.has(s.state)||active.has(s.attemptState))
    ??snapshot.stages.find(s=>s.state!=='COMPLETED'&&s.state!=='SKIPPED')??snapshot.stages.at(-1);
  const p=stage.progress,percent=p?100*p.stageStep/p.totalSteps:null;
  let status='running';
  if(snapshot.state==='COMPLETED')status='completed';
  else if(snapshot.state==='PAUSED')status='paused';
  else if(snapshot.state==='NEEDS_REVIEW')status='review';
  else if(snapshot.state==='FAILED'||stage.state==='FAILED')status='failed';
  else if(snapshot.state==='CANCELED')status='canceled';
  else if(snapshot.state==='CANCEL_PENDING'||stage.attemptState==='CANCEL_PENDING')status='canceling';
  else if(snapshot.state==='UNKNOWN'||stage.attemptState==='UNKNOWN')status='unknown';
  else if(snapshot.state==='DRAFT')status='draft';
  else if(snapshot.resourceWaiting||['PENDING','READY','WAITING','BLOCKED'].includes(stage.state))status='queued';
  else if(percent===100)status='validating';
  return {status,percent,step:p?.stageStep??null,total:stage.totalSteps,stageId:stage.id,stageKind:stage.kind,
    stageLabel:kindLabels[stage.kind]??stage.kind,stageIndex:snapshot.stages.indexOf(stage)+1,totalStages:snapshot.stages.length,
    completedStages,attemptId:stage.attemptId,elapsedSeconds:p?.elapsedSeconds??null,publishedAtEpochMs:p?.publishedAtEpochMs??null,
    viewStageId:binding.viewStageId??null};
}

export function createComputeProgress({humanApi,onChange=()=>{},schedule=setTimeout,cancel=clearTimeout,now=Date.now,intervalMs=5000,signal}){
  let state={binding:null,snapshot:null,view:null,error:null,receivedAt:null,etaSeconds:null,retained:false};
  let epoch=0,closed=false,timer=null,etaTimer=null,abort=null,pending=null,last=null,rates=[];
  const emit=()=>{if(!closed)onChange(state);};
  const clearEtaTimer=()=>{if(etaTimer!==null)cancel(etaTimer);etaTimer=null;};
  const stop=()=>{epoch++;if(timer!==null)cancel(timer);timer=null;clearEtaTimer();abort?.abort();abort=null;pending=null;last=null;rates=[];};
  function eta(view,observedAt){
    const observation={key:[state.binding.workflowId,state.binding.revision,view.stageId,view.attemptId].join(':'),step:view.step,elapsed:view.elapsedSeconds,published:view.publishedAtEpochMs};
    if(view.status!=='running'||observation.step===null||Math.abs(observedAt-view.publishedAtEpochMs)>30000){last=null;rates=[];return null;}
    if(last?.key!==observation.key||last&&observation.step<last.step||last&&observation.elapsed<last.elapsed)rates=[];
    else if(last&&observation.published>last.published){
      const elapsed=observation.elapsed-last.elapsed,steps=observation.step-last.step;
      if(elapsed>0&&steps>0){rates.push(steps/elapsed);rates=rates.slice(-3);}else rates=[];
    }
    if(!last||observation.published>=last.published)last=observation;
    if(rates.length<3||Math.max(...rates)/Math.min(...rates)>2)return null;
    return Math.ceil((view.total-view.step)/(rates.reduce((a,b)=>a+b,0)/rates.length));
  }
  function refresh(){
    if(closed||!state.binding)return Promise.resolve();
    if(pending)return pending;
    if(timer!==null)cancel(timer);timer=null;
    const token=epoch,binding=state.binding,controller=abort;
    const current=()=>!closed&&token===epoch&&!controller.signal.aborted;
    pending=(async()=>{
      try{
        let value=await humanApi('progress',{workflowId:binding.workflowId,revision:binding.revision},controller.signal);
        if(!current())return;
        describeProgress(value,binding);
        let retained=false;
        value={...value,stages:value.stages.map(stage=>{
          const previous=state.snapshot?.stages.find(s=>s.id===stage.id&&s.attemptId===stage.attemptId&&s.totalSteps===stage.totalSteps);
          if(!stage.progress&&previous?.progress){retained=true;return {...stage,progress:previous.progress};}
          if(stage.progress&&previous?.progress&&stage.progress.publishedAtEpochMs<previous.progress.publishedAtEpochMs){retained=true;return {...stage,progress:previous.progress};}
          return stage;
        })};
        const view=describeProgress(value,binding);
        clearEtaTimer();
        state={...state,snapshot:value,view,error:null,receivedAt:now(),retained,etaSeconds:retained?null:eta(view,value.observedAtEpochMs)};
        if(state.etaSeconds!==null)etaTimer=schedule(()=>{
          if(!current()||state.snapshot!==value)return;
          etaTimer=null;last=null;rates=[];state={...state,etaSeconds:null};emit();
        },Math.max(0,30000-Math.max(0,value.observedAtEpochMs-view.publishedAtEpochMs)));
        emit();
      }catch(error){
        if(!current())return;
        clearEtaTimer();last=null;rates=[];
        const code=/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:'PROGRESS_UNAVAILABLE';
        state={...state,error:code,etaSeconds:null};emit();
      }finally{
        if(current()){
          pending=null;
          if(!terminal.has(state.snapshot?.state)&&!['STALE_REVISION','PROGRESS_SOURCE_CHANGED','BIO_SESSION_ACCESS_DENIED','BIO_PLATFORM_ACCESS_DENIED'].includes(state.error))timer=schedule(()=>refresh(),intervalMs);
        }
      }
    })();
    return pending;
  }
  function bind(binding){
    if(closed)return Promise.resolve();
    if(binding&&(!id(binding.workflowId)||!Number.isSafeInteger(binding.revision)||binding.revision<1))fail('PROGRESS_SOURCE_CHANGED');
    stop();state={binding:binding?{...binding}:null,snapshot:null,view:null,error:null,receivedAt:null,etaSeconds:null,retained:false};
    if(binding)abort=new AbortController();emit();return binding?refresh():Promise.resolve();
  }
  function dispose(){if(closed)return;stop();closed=true;signal?.removeEventListener('abort',dispose);}
  signal?.addEventListener('abort',dispose,{once:true});if(signal?.aborted)dispose();
  return {bind,refresh,dispose,get state(){return state;},setSource(source){return bind(source?.kind==='accepted'?{workflowId:source.workflowId,revision:source.revision,viewStageId:source.stageId,viewAttemptId:source.attemptId}:null);}};
}
