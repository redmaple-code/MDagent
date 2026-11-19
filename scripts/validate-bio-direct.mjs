import {readFileSync} from 'node:fs';import {pathToFileURL} from 'node:url';
const layers=['LOCAL_COMPONENTS','LOCAL_STDIO','BROWSER_LOOPBACK','REAL_SSH','REAL_LINK','SCIENTIFIC'];
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function assessDirectView(view,{durationSeconds=180}={}){
  const functional=!!(view?.finished&&!view.error&&!view.playbackError&&view.renderer?.ready&&hash(view.renderer.sourceSha256)&&Number.isInteger(view.renderer.sourceFrameIndex)&&Number.isFinite(view.renderer.timePs)&&view.actualLastFrameSeen&&view.cachedDurationMs>=durationSeconds*1000-1000&&view.cachedFrameAdvances>=2&&view.cachedUniqueFrames>=2&&Number.isFinite(view.playStartMs)&&view.playStartMs>=0);
  const performance=functional&&Number.isFinite(view.warmStructureMs)&&view.warmStructureMs<=4000&&view.playStartMs<=6000&&Number.isFinite(view.stallPercent)&&view.stallPercent<=1;
  return {functional:functional?'PASS':'FAIL',performance:performance?'PASS':'FAIL'};
}
export function validateDirectEvidence(receipt){
  const checks={},errors=[];let pending=false;
  if(receipt?.schemaVersion!=='bio.direct-acceptance.v1'||!['auto','relay','direct-only'].includes(receipt.mode)||!['sourceSha256','manifestSha256','fileSha256','buildSha256'].every(k=>hash(receipt[k])))errors.push('IDENTITY_EVIDENCE_MISSING');
  const values=[receipt?.coordinateBytes?.direct,receipt?.coordinateBytes?.relay,receipt?.coordinateBytes?.cloudDirect,receipt?.controlBytes,receipt?.assetBytes,receipt?.totalDownloadedBytes];
  if(!values.every(v=>Number.isSafeInteger(v)&&v>=0)||receipt.totalDownloadedBytes>536870912||receipt.totalDownloadedBytes<values.slice(0,2).reduce((a,b)=>a+b,0)+receipt.controlBytes+receipt.assetBytes)errors.push('DOWNLOAD_BUDGET_INVALID');
  if(receipt?.mode==='direct-only'&&receipt.coordinateBytes?.relay!==0||receipt?.coordinateBytes?.cloudDirect!==0)errors.push('DIRECT_ROUTE_INVALID');
  if(receipt?.originalHashesUnchanged!==true)errors.push('ORIGINAL_HASH_EVIDENCE_MISSING');
  for(const name of layers){const item=receipt?.layers?.[name];checks[name]=item?.status||'PENDING';if(!['PASS','FAIL','PENDING'].includes(checks[name]))checks[name]='FAIL';
    if(checks[name]==='PASS'&&(!Array.isArray(item.evidence)||!item.evidence.length))checks[name]='FAIL';
    if(checks[name]==='PASS'&&name==='BROWSER_LOOPBACK'&&(!item.actualFrameReadback||!item.playbackProgressVerified||item.performanceStatus!=='PASS'||!item.browserVersion||!item.osVersion))checks[name]='FAIL';
    if(checks[name]==='PASS'&&name==='REAL_SSH'&&(!item.actualSshRouteVerified||!item.hostIdentityVerified||!item.sameRegisteredTarget))checks[name]='FAIL';
    if(checks[name]==='PASS'&&name==='REAL_LINK'&&(!item.httpsBrowserReadback||!item.bandwidthMeasured||!item.fullPermitChainMeasured||!item.scenarios?.every(v=>[1,3,5].includes(v.viewers)&&v.durationSeconds>=180)||new Set(item.scenarios?.map(v=>v.viewers)).size!==3||checks.REAL_SSH!=='PASS'))checks[name]='FAIL';
    if(checks[name]==='PASS'&&name==='SCIENTIFIC'&&(!item.productionMdAuthorized||!item.independentScientificAcceptance))checks[name]='FAIL';
    if(checks[name]==='PENDING')pending=true;if(checks[name]==='FAIL')errors.push(name+'_EVIDENCE_INVALID');
  }
  return {status:errors.length?'FAIL':pending?'WITH_PENDING':'PASS',checks,errors};
}
if(process.argv[1]&&pathToFileURL(process.argv[1]).href===import.meta.url){const result=validateDirectEvidence(JSON.parse(readFileSync(process.argv[2],'utf8')));process.stdout.write(JSON.stringify(result)+'\n');if(result.status==='FAIL')process.exitCode=1;}
