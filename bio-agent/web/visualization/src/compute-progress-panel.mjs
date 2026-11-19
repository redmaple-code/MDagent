import {createComputeProgress} from './compute-progress.mjs';

const labels={running:'计算中',validating:'校验中',completed:'已完成',failed:'计算失败',canceled:'已取消',canceling:'正在取消',paused:'已暂停',review:'需要复核',unknown:'状态待确认',queued:'等待中',draft:'待批准'};
const errorLabels={STALE_REVISION:'任务已更新，请重新关联',PROGRESS_SOURCE_CHANGED:'任务已更新，请重新关联',BIO_SESSION_ACCESS_DENIED:'无法访问此任务',BIO_PLATFORM_ACCESS_DENIED:'无法访问此任务'};
const duration=seconds=>seconds<60?Math.ceil(seconds)+' 秒':seconds<3600?Math.ceil(seconds/60)+' 分钟':(seconds/3600).toFixed(1)+' 小时';
const number=value=>value.toLocaleString('en-US');
const percentage=value=>(value<100?Math.min(value,99.9):value).toFixed(1).replace(/\.0$/,'');

export function createComputeProgressPanel({humanApi,signal,document=globalThis.document}){
  const element=document.createElement('details');element.className='bio-viz-progress';
  element.innerHTML='<summary><span>计算进度</span><span data-progress-summary>未关联</span><span class="bio-viz-progress-mini" data-progress-mini hidden><i></i></span></summary><div class="bio-viz-progress-body"><div class="bio-viz-progress-controls"><label>关联任务 <select data-progress-task aria-label="关联计算任务"></select></label><button data-progress-refresh>刷新</button><button data-progress-more hidden>更多</button></div><p data-progress-stage></p><div class="bio-viz-progress-bar" data-progress-bar role="progressbar" aria-label="当前阶段计算进度" hidden><span data-progress-fill></span></div><p data-progress-steps></p><p class="bio-viz-progress-detail" data-progress-detail></p><p class="bio-viz-progress-detail" data-progress-message role="status" aria-live="polite"></p></div>';
  const fields=new Map(['summary','mini','task','refresh','more','stage','bar','fill','steps','detail','message'].map(name=>[name,element.querySelector('[data-progress-'+name+']')]));
  const find=name=>fields.get(name),body=element.querySelector('.bio-viz-progress-body');body.hidden=true;body.remove?.();
  let closed=false,source=null,sourceKey=null,catalog=[],nextOffset=null,listEpoch=0,listAbort=null,loaded=false,listError=null;
  const option=(text,value)=>{const o=document.createElement('option');o.textContent=text;o.value=value;return o;};
  function taskOptions(){
    const binding=monitor.state.binding,key=binding?binding.workflowId+':'+binding.revision:'';
    const options=catalog.map(t=>option(`${t.workflowId} · v${t.revision}`,t.workflowId+':'+t.revision));
    if(binding&&!catalog.some(t=>t.workflowId+':'+t.revision===key))options.push(option(`${binding.workflowId} · v${binding.revision}`,key));
    find('task').replaceChildren(option('请选择计算任务',''),...options);find('task').value=key;
    find('task').disabled=source?.kind==='accepted';find('more').hidden=nextOffset===null;
  }
  function draw(state){
    if(closed)return;taskOptions();
    const {binding,view,snapshot,error,etaSeconds}=state;
    const workflowComplete=view?.status==='completed',percent=workflowComplete?100:view?.percent??null,status=view?labels[view.status]:'正在读取';
    find('summary').textContent=binding?`${status}${percent===null?'':' · '+percentage(percent)+'%'}`:'未关联';
    find('mini').hidden=percent===null;find('mini').style.setProperty?.('--bio-progress',`${percent??0}%`);
    find('bar').hidden=percent===null;
    find('bar').setAttribute('aria-label',workflowComplete?'工作流阶段完成度':'当前阶段计算进度');
    if(percent===null)find('bar').removeAttribute('aria-valuenow');
    else{find('bar').setAttribute('aria-valuemin','0');find('bar').setAttribute('aria-valuemax','100');find('bar').setAttribute('aria-valuenow',String(percent));}
    find('fill').style.width=(percent??0)+'%';
    element.setAttribute('data-progress-status',error?'unavailable':view?.status??'unbound');
    find('stage').textContent=view?`${view.stageLabel} · 已完成 ${view.completedStages}/${view.totalStages} 阶段`:binding?`${binding.workflowId} · v${binding.revision}`:'';
    find('steps').textContent=view?.step!==null&&view?.step!==undefined?`${number(view.step)} / ${number(view.total)} 步`:'';
    const age=view?.publishedAtEpochMs!==null&&view?.publishedAtEpochMs!==undefined?Math.max(0,(snapshot.observedAtEpochMs-view.publishedAtEpochMs+Date.now()-state.receivedAt)/1000):null;
    const details=[];
    if(binding?.viewStageId&&view&&binding.viewStageId!==view.stageId)details.push(`查看：${binding.viewStageId}`);
    if(view?.elapsedSeconds!==null&&view?.elapsedSeconds!==undefined)details.push('用时 '+duration(view.elapsedSeconds));
    if(age!==null&&view.status!=='completed')details.push(duration(age)+'前更新');
    if(etaSeconds!==null)details.push('剩余约 '+duration(etaSeconds));
    find('detail').textContent=details.join(' · ')||'';
    find('message').textContent=error?(errorLabels[error]??'连接中断，正在重试。'):listError?'清单加载失败，请刷新。':state.retained?'进度尚未更新。':age>30&&view?.status==='running'?'进度更新延迟。':'';
    if(error)find('summary').textContent=(view?'上次进度':'进度暂不可用')+(percent===null?'':' · '+percentage(percent)+'%');
  }
  const monitor=createComputeProgress({humanApi,signal,onChange:draw});
  async function refreshTasks(append=false){
    if(closed||typeof humanApi!=='function'||append&&nextOffset===null)return;
    const epoch=++listEpoch,offset=append?nextOffset:0;listAbort?.abort();listAbort=new AbortController();
    const operationSignal=signal?AbortSignal.any([signal,listAbort.signal]):listAbort.signal;
    find('refresh').disabled=true;
    try{
      const page=await humanApi('list',{offset},operationSignal);
      if(closed||epoch!==listEpoch||operationSignal.aborted)return;
      if(!Array.isArray(page.items)||page.items.length>100||page.items.some(t=>typeof t.workflowId!=='string'||!Number.isSafeInteger(t.revision)||t.revision<1)
        ||page.nextOffset!=null&&(!Number.isSafeInteger(page.nextOffset)||page.nextOffset<=offset))throw new Error('PROGRESS_LIST_INVALID');
      const merged=append?[...catalog,...page.items]:page.items;
      catalog=[...new Map(merged.map(t=>[t.workflowId+':'+t.revision,t])).values()];nextOffset=page.nextOffset??null;loaded=true;listError=null;
    }catch(error){if(!closed&&epoch===listEpoch&&!operationSignal.aborted)listError=error.message;}
    finally{if(!closed&&epoch===listEpoch){find('refresh').disabled=false;draw(monitor.state);}}
  }
  element.ontoggle=async()=>{body.hidden=!element.open;if(element.open&&!loaded&&source?.kind!=='accepted')await refreshTasks();};
  find('task').onchange=async()=>{
    const task=catalog.find(t=>t.workflowId+':'+t.revision===find('task').value);
    if(source?.kind==='accepted')return;
    await monitor.bind(task?{workflowId:task.workflowId,revision:task.revision}:null);
  };
  find('refresh').onclick=async()=>{await Promise.all([refreshTasks(),monitor.refresh()]);};
  find('more').onclick=()=>refreshTasks(true);
  draw(monitor.state);
  function dispose(){if(closed)return;closed=true;listEpoch++;listAbort?.abort();monitor.dispose();signal?.removeEventListener('abort',dispose);}
  signal?.addEventListener('abort',dispose,{once:true});if(signal?.aborted)dispose();
  return {element,body,dispose,async setSource(value){
    const key=JSON.stringify(value);if(closed||key===sourceKey)return;sourceKey=key;source=value;
    await monitor.setSource(value);
  }};
}
