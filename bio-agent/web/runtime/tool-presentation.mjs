const labels={
  bio_capabilities:['检查本地模拟能力','read'],
  bio_inputs:['查看已导入的结构','read'],
  bio_inspect:['检查结构文件','read'],
  bio_plan:['拟定模拟方案','edit'],
  bio_continue:['拟定已有计算的续跑方案','edit'],
  bio_get:['查看方案与计算状态','read'],
  bio_list:['查看本会话的方案','read'],
  bio_submit:['提交已审阅的计算','execute'],
  bio_cancel:['请求取消计算','execute'],
  bio_report:['查看已接受的分析报告','read'],
  bio_artifacts:['查看计算产物','read'],
};

const short=value=>typeof value==='string'?value.replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,100):undefined;
function intentOf(args){
  if(typeof args?.intentJson!=='string'||args.intentJson.length>65536)return undefined;
  try{return JSON.parse(args.intentJson);}catch{return undefined;}
}

export function presentCall(name,args){
  const [title,kind]=labels[name]??['处理模拟请求','other'];
  const workflow=short(args?.workflowId)??short(intentOf(args)?.workflowId);
  const input=workflow??short(args?.path);
  return {card:'generic',title,kind,...(input?{rawInput:input}:{})};
}

export function presentResult(name,_args,result){
  let value;
  try{value=JSON.parse(result?.content?.find(block=>block.type==='text')?.text);}catch{}
  if(result?.isError||value?.ok===false){
    const code=value?.error?.code;
    const suffix=typeof code==='string'&&/^[A-Z_]{1,64}$/.test(code)?`（${code}）`:'';
    return {card:'generic',title:'操作未完成'+suffix};
  }
  if(value?.ok!==true)return {card:'generic',title:'已返回结果，状态需核对'};
  if(['bio_plan','bio_continue'].includes(name)&&value.result?.state==='DRAFT')
    return {card:'generic',title:'方案草稿已保存（待审阅）'};
  // Keep canonical content in the expanded detail; a UI title cannot certify a calculation.
  return {card:'generic',title:labels[name]?.[0]??'已返回结果'};
}
