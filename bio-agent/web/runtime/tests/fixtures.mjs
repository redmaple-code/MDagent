import {readFileSync} from 'node:fs';

export function waterIntent(caseId){
  const ids={'water-draft':'online-water-draft','approval-boundary':'online-approval-boundary'};
  if(!Object.hasOwn(ids,caseId))throw new Error('INVALID_OFFLINE_CASE');
  const intent=JSON.parse(readFileSync(new URL('../../../validation/chat-cases/intents/water.json',import.meta.url),'utf8'));
  return {...intent,workflowId:ids[caseId],title:'Water DRAFT engineering fixture',
    researchGoal:'Offline protocol and permission check; no scientific execution.'};
}
