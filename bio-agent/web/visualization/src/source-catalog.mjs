import {validateSource} from '../../runtime/visualization/contracts.mjs';
const fail=code=>{throw new Error(code);};
const current=(value,id,revision)=>{if(value.workflowId!==id||value.revision!==revision)fail('PREVIEW_SOURCE_CHANGED');return value;};
const candidates=value=>value.stages.filter(stage=>stage.state==='COMPLETED'&&value.attempts.filter(a=>a.stageId===stage.id&&a.state==='COMPLETED').length===1);
const pageOffset=(value,offset)=>{if(value!==null&&value!==undefined&&(!Number.isSafeInteger(value)||value<=offset))fail('INVALID_PAGE');return value??null;};
async function previewInputId(input){
  const bytes=new TextEncoder().encode(JSON.stringify(['bio.preview-input.v1',input.id,input.path,input.sha256]));
  const digest=await crypto.subtle.digest('SHA-256',bytes);
  return 'input-file-'+Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
}
export async function loadPreviewChoices(humanApi,{offset=0,signal}={}){
  const [inputs,list]=await Promise.all([offset===0?humanApi('inputs',{},signal):{items:[]},humanApi('list',{offset},signal)]);
  const inputChoices=await Promise.all(inputs.items.filter(i=>i.role==='structure').map(async input=>({key:`input:${input.id}:${input.sha256}:${input.path}`,kind:'input',label:input.path,input,previewSourceId:await previewInputId(input)})));
  const workflowChoices=[];
  for(const w of list.items){
    if(w.state==='DRAFT')continue;
    if(w.state!=='COMPLETED'){
      const value=current(await humanApi('get',{workflowId:w.workflowId},signal),w.workflowId,w.revision);
      if(!candidates(value).length)continue;
    }
    workflowChoices.push({key:`workflow:${w.workflowId}:${w.revision}`,kind:'workflow',label:`${w.workflowId} · v${w.revision}`,workflowId:w.workflowId,revision:w.revision});
  }
  const choices=[...inputChoices,...workflowChoices];
  return {choices,nextOffset:pageOffset(list.nextOffset,offset)};
}
export async function loadCompletedStages(humanApi,workflowId,revision,{signal}={}){
  const value=current(await humanApi('get',{workflowId},signal),workflowId,revision);
  const result=[];
  for(const stage of candidates(value)){
    try{await resolvePreviewChoice(humanApi,{kind:'workflow',workflowId,revision},stage.id,{signal});result.push({id:stage.id,label:stage.id});}
    catch(error){if(!['RESULT_NOT_ACCEPTED','PREVIEW_TOPOLOGY_REQUIRED'].includes(error.message))throw error;}
  }
  return result;
}
export async function resolvePreviewChoice(humanApi,choice,stageId,{signal}={}){
  if(choice.kind==='input'){
    const input=choice.input;
    if(signal?.aborted)fail('BIO_REQUEST_ABORTED');const sourceId=await previewInputId(input);if(signal?.aborted)fail('BIO_REQUEST_ABORTED');
    return {source:validateSource({kind:'input',sourceId,sourceSha256:input.sha256,workflowId:null,revision:null,stageId:null,attemptId:null}),structureName:input.path,trajectoryNames:[]};
  }
  if(!stageId)fail('PREVIEW_STAGE_REQUIRED');
  const value=current(await humanApi('get',{workflowId:choice.workflowId},signal),choice.workflowId,choice.revision);
  if(!candidates(value).some(s=>s.id===stageId))fail('RESULT_NOT_ACCEPTED');
  const attempt=value.attempts.find(a=>a.stageId===stageId&&a.state==='COMPLETED');
  let offset=0,source,items=[];
  for(let pages=0;pages<100;pages++){
    const page=await humanApi('artifacts',{workflowId:choice.workflowId,stageId,offset},signal),ref=validateSource(page.sourceRef,'PREVIEW_SOURCE_CHANGED');
    if(ref.kind!=='accepted'||ref.workflowId!==choice.workflowId||ref.revision!==choice.revision||ref.stageId!==stageId||ref.attemptId!==attempt.attemptId||page.attemptId!==attempt.attemptId||attempt.acceptance&&ref.sourceSha256!==attempt.acceptance.manifestSha256||source&&JSON.stringify(source)!==JSON.stringify(ref))fail('PREVIEW_SOURCE_CHANGED');
    source=ref;items.push(...page.items);const next=pageOffset(page.nextOffset,offset);if(next===null){offset=null;break;}offset=next;
  }
  if(offset!==null)fail('PREVIEW_CATALOG_LIMIT');
  // Recheck the current revision/accepted attempt after the final manifest page.
  const checked=current(await humanApi('get',{workflowId:choice.workflowId},signal),choice.workflowId,choice.revision);
  if(!candidates(checked).some(s=>s.id===stageId)||!checked.attempts.some(a=>a.attemptId===source.attemptId&&(!a.acceptance||a.acceptance.manifestSha256===source.sourceSha256)))fail('PREVIEW_SOURCE_CHANGED');
  const names=items.filter(i=>['topology','trajectory'].includes(i.role));
  if(names.some(i=>typeof i.path!=='string'||i.path.startsWith('/')||i.path.split(/[\\/]/).includes('..')))fail('PREVIEW_SOURCE_CHANGED');
  const topology=names.filter(i=>i.role==='topology');if(topology.length!==1)fail('PREVIEW_TOPOLOGY_REQUIRED');
  return {source,structureName:topology[0].path,trajectoryNames:names.filter(i=>i.role==='trajectory').map(i=>i.path)};
}
