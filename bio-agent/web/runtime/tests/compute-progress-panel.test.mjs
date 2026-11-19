import test from 'node:test';import assert from 'node:assert/strict';
const ui=await import('../../visualization/src/compute-progress-panel.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
function documentFixture(){
  const nodes=new Map();
  const make=()=>({value:'',children:[],style:{},classList:{toggle(){}},attributes:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(key,value){this.attributes[key]=value;},removeAttribute(key){delete this.attributes[key];},querySelector(selector){if(!nodes.has(selector))nodes.set(selector,make());return nodes.get(selector);}});
  return {document:{createElement:()=>make()},find:name=>nodes.get('[data-progress-'+name+']')};
}
const value={schemaVersion:'bio.compute-progress.v1',workflowId:'md',revision:1,state:'RUNNING',observedAtEpochMs:Date.now(),resourceWaiting:false,stages:[{id:'prod',kind:'production',state:'RUNNING',attemptId:'attempt',attemptState:'RUNNING',totalSteps:1000,progress:{stageStep:400,totalSteps:1000,elapsedSeconds:40,publishedAtEpochMs:Date.now()}}]};
test('active tasks without accepted results are selectable for input-structure progress',async()=>{
  assert.equal(typeof ui.createComputeProgressPanel,'function','progress task controls must exist');
  const f=documentFixture(),requests=[];
  const panel=ui.createComputeProgressPanel({...f,humanApi:async(action,args)=>{requests.push({action,args});return action==='list'?{items:[{workflowId:'md',revision:1,state:'RUNNING'}],nextOffset:null}:value;}});
  try{
    assert.equal(requests.length,0);await panel.setSource({kind:'input',sourceId:'md.pdb'});
    panel.element.open=true;await panel.element.ontoggle();
    assert.ok(f.find('task').children.some(o=>o.value==='md:1'));
    f.find('task').value='md:1';await f.find('task').onchange();
    assert.equal(f.find('bar').attributes['aria-valuenow'],'40');assert.match(f.find('steps').textContent,/400.*1,000/);
    assert.equal(requests.filter(r=>r.action==='progress').length,1);
  }finally{panel.dispose();}
});
test('accepted source association is automatic, explicit input association is cleared for another source',async()=>{
  assert.equal(typeof ui.createComputeProgressPanel,'function');const f=documentFixture();let calls=0;
  const panel=ui.createComputeProgressPanel({...f,humanApi:async()=>{calls++;return value;}});
  try{
    await panel.setSource({kind:'accepted',sourceId:'result',workflowId:'md',revision:1,stageId:'prod'});
    assert.equal(f.find('task').disabled,true);assert.equal(f.find('task').value,'md:1');assert.equal(calls,1);
    await panel.setSource({kind:'input',sourceId:'another'});
    assert.equal(f.find('task').disabled,false);assert.equal(f.find('task').value,'');assert.equal(f.find('bar').hidden,true);
  }finally{panel.dispose();}
});
test('accepted workflow completion fills a labelled workflow bar without inventing MD steps',async()=>{
  const f=documentFixture(),complete={...value,state:'COMPLETED',stages:value.stages.map(s=>({...s,state:'COMPLETED',attemptState:'COMPLETED',progress:null}))};
  const panel=ui.createComputeProgressPanel({...f,humanApi:async()=>complete});
  try{
    await panel.setSource({kind:'accepted',workflowId:'md',revision:1,stageId:'prod'});
    assert.equal(f.find('bar').attributes['aria-valuenow'],'100');assert.equal(f.find('bar').attributes['aria-label'],'工作流阶段完成度');
    assert.doesNotMatch(f.find('steps').textContent,/1,000.*1,000/);
  }finally{panel.dispose();}
});
test('summary never rounds an incomplete MD observation up to 100 percent',async()=>{
  for(const [state,step,label] of [['RUNNING',999999,'计算中 · 99.9%'],['FAILED',999999,'计算失败 · 99.9%'],['RUNNING',1000000,'校验中 · 100%']]){
    const f=documentFixture(),observation={...value,state,stages:[
      {id:'prep',kind:'prepare',state:'COMPLETED',attemptId:'prep1',attemptState:'COMPLETED',totalSteps:null,progress:null},
      {...value.stages[0],state,attemptState:state,totalSteps:1000000,progress:{...value.stages[0].progress,stageStep:step,totalSteps:1000000}},
    ]};
    const panel=ui.createComputeProgressPanel({...f,humanApi:async()=>observation});
    try{
      await panel.setSource({kind:'accepted',workflowId:'md',revision:1,stageId:'prep',attemptId:'prep1'});
      assert.equal(f.find('summary').textContent,label);
      assert.equal(f.find('bar').attributes['aria-valuenow'],step===1000000?'100':'99.9999');
    }finally{panel.dispose();}
  }
});
