// Adapted from KSH frame-playback.ts and sceneOperationBinding.ts; see provenance.
export function nextFrameInRange(config,current){
  const first=config.direction===1?config.startFrame:config.endFrame;
  if(current===null)return first;
  const next=current+config.direction;
  return next>=config.startFrame&&next<=config.endFrame?next:config.loop?first:null;
}
export function bindingMatches(expected,actual,kind){
  const fields=['sessionId','viewportId','sourceSha256','sourceRevision','visualRevision','frameRevision'];
  if(kind==='focus')fields.push('cameraRevision');
  return fields.every(k=>expected[k]===actual[k]);
}
