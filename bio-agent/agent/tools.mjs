import {readFileSync} from 'node:fs';
import {effectTools,effectWorkflow} from './client-receipts.mjs';
const contracts=JSON.parse(readFileSync(new URL('./contracts.json',import.meta.url),'utf8'));
export {contracts};
export function createTools(bridge,{uncertain=new Set()}={}){
  return Object.entries(contracts.tools).map(([name,contract])=>({name,label:name,description:contract.description,parameters:contract.parameters,
    async execute(_id,args,signal){
      const workflow=effectWorkflow(args);
      const failed=()=>({ok:false,error:{code:'TOOL_RESPONSE_UNKNOWN',message:'原副作用响应尚未确认；请由操作者 /status 查询，再用 /recover 明确协调。'}});
      let response;
      try{
        if(effectTools.has(name)&&uncertain.has(workflow))response=failed();
        else response=await bridge.call(name,args,{signal});
        if(response.ok&&effectTools.has(name)){
          if(signal?.aborted||(name==='bio_submit'&&['UNKNOWN','FAILED','PAUSED','CANCELED','CANCEL_PENDING'].includes(response.result.state)))response=failed();
          else{
            const confirmation=name==='bio_submit'
              ?await bridge.control('activate',{workflowId:args.workflowId,revision:args.revision,sourceRequestId:response.requestId})
              :await bridge.control('acknowledge',{sourceRequestId:response.requestId});
            if(!confirmation.ok)response=failed();
          }
        }
      }catch{
        response=effectTools.has(name)?failed():{ok:false,error:{code:'TOOL_FAILED',message:'工具未完成，请查询状态。'}};
      }
      if(effectTools.has(name)&&response.error?.code==='TOOL_RESPONSE_UNKNOWN')uncertain.add(workflow);
      return {content:[{type:'text',text:JSON.stringify(response)}],details:response,isError:!response.ok};
    }}));
}
