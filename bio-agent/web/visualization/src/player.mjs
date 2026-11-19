import {previewLimits as L} from '../../runtime/visualization/contracts.mjs';
import {nextFrameInRange} from './frame-binding.mjs';
export function createPreviewPlayer({renderer,transport,onState=()=>{}}){
  let manifest,epoch=0,controller=new AbortController(),disposed=false,loopToken=0,finished=Promise.resolve(),renderTail=Promise.resolve();
  const state={ready:false,playing:false,buffering:false,frameIndex:0,sourceFrameNumber:null,timePs:null,
    sourceRevision:0,visualRevision:0,frameRevision:0,cameraRevision:0,decodedWindowBytes:0,errorCode:null};
  const emit=()=>onState({...state});
  const valid=(token)=>{if(disposed||token!==epoch||controller.signal.aborted)throw new Error('BIO_REQUEST_ABORTED');};
  function mutate(token,method,...args){const next=renderTail.catch(()=>{}).then(async()=>{valid(token);const value=await renderer[method](...args);valid(token);return value;});renderTail=next;return next;}
  async function draw(index,token=epoch){
    const frame=manifest.frames[index];if(!frame)throw new Error('PREVIEW_FRAME_OUT_OF_RANGE');
    const segment=manifest.files.find(f=>f.fileId===frame.segmentId);
    const decoded=segment.previewFrameCount*manifest.atomCount*12;
    if(decoded>L.windowBytes||segment.previewFrameCount>L.windowFrames)throw new Error('PREVIEW_MEMORY_BUDGET_EXCEEDED');
    state.buffering=!(transport.hasFile?.(manifest,frame.segmentId));if(state.buffering)emit();const bytes=await transport.getFile(manifest,frame.segmentId,controller.signal);valid(token);
    await mutate(token,'setFrame',bytes,frame.segmentFrameIndex,{...frame,fileId:frame.segmentId,frameCount:segment.previewFrameCount});valid(token);
    state.frameIndex=index;state.sourceFrameNumber=frame.sourceFrameIndex+1;state.timePs=frame.timePs;
    state.decodedWindowBytes=decoded;state.buffering=false;state.ready=true;emit();
    const next=manifest.frames[segment.previewFrameStart+segment.previewFrameCount];
    if(next&&segment.previewFrameStart+segment.previewFrameCount-index<=40)void transport.getFile(manifest,next.segmentId,controller.signal,'prefetch').catch(()=>{});
  }
  const api={state,renderer,transport,render:(method,...args)=>mutate(epoch,method,...args),get manifest(){return manifest;},get finished(){return finished;},
    async openBox(description){api.pause();const token=++epoch;manifest=undefined;state.ready=false;state.sourceSha256=description.source.sourceSha256;state.sourceRevision++;state.visualRevision++;state.frameIndex=0;state.sourceFrameNumber=1;state.timePs=description.timeStartPs;state.sourceFrameCount=description.sourceFrameCount;state.decodedWindowBytes=0;await mutate(token,'openBox',description);valid(token);state.ready=renderer.observe().ready;emit();return {...state};},
    async open(value){
      api.pause();controller.abort();controller=new AbortController();const token=++epoch;
      state.ready=false;state.sourceRevision++;state.visualRevision++;state.sourceSha256=value.source.sourceSha256;state.errorCode=null;manifest=value;emit();
      await transport.beginView?.(value,controller.signal);valid(token);
      const [topology,map]=await Promise.all([transport.getFile(value,value.topologyFile,controller.signal),transport.getFile(value,value.atomMapFile,controller.signal)]);valid(token);
      await mutate(token,'open',value,topology,map);valid(token);await draw(0,token);return {...state};
    },
    async seekTime(timePs){
      if(!manifest||!Number.isFinite(timePs)||timePs<0)throw new Error('INVALID_VIEW_COMMAND');
      api.pause();state.frameRevision++;const token=++epoch;
      let best=0;for(let i=1;i<manifest.frames.length;i++)if(Math.abs(manifest.frames[i].timePs-timePs)<Math.abs(manifest.frames[best].timePs-timePs))best=i;
      await draw(best,token);return {requestedTimePs:timePs,actualTimePs:state.timePs,...state};
    },
    play(config){
      if(!manifest||manifest.frames.length===1)throw new Error('PREVIEW_STATIC_STRUCTURE');
      if(!Number.isInteger(config.startFrame)||!Number.isInteger(config.endFrame)||config.startFrame<0||config.endFrame>=manifest.frames.length||config.startFrame>=config.endFrame||!Number.isInteger(config.fps)||config.fps<L.minFps||config.fps>L.maxFps||![1,-1].includes(config.direction)||typeof config.loop!=='boolean')throw new Error('INVALID_VIEW_COMMAND');
      api.pause();state.frameRevision++;state.playing=true;const token=++loopToken;const viewEpoch=epoch;emit();
      finished=(async()=>{let current=state.frameIndex>=config.startFrame&&state.frameIndex<=config.endFrame?state.frameIndex-config.direction:null;
        while(state.playing&&token===loopToken){const next=nextFrameInRange(config,current);if(next===null)break;
          const started=performance.now();await draw(next,viewEpoch);current=next;
          await new Promise(resolve=>setTimeout(resolve,Math.max(0,1000/config.fps-(performance.now()-started))));}
        if(token===loopToken){state.playing=false;emit();}
      })().catch(e=>{if(token===loopToken){state.playing=false;state.buffering=false;state.errorCode=e.message;emit();}});
      return {...state};
    },
    pause(){if(state.playing){state.frameRevision++;state.playing=false;}loopToken++;const token=++epoch;controller.abort();controller=new AbortController();emit();return renderTail.catch(()=>{}).then(()=>{if(!disposed&&token===epoch&&!state.playing){const actual=renderer.observe();const index=manifest?.frames.findIndex(f=>f.sourceFrameIndex===actual.sourceFrameIndex);if(actual.ready&&actual.sourceSha256===state.sourceSha256&&index>=0){state.frameIndex=index;state.sourceFrameNumber=actual.sourceFrameIndex+1;state.timePs=actual.timePs;state.buffering=false;emit();}}return {...state};});},
    dispose(){api.pause();disposed=true;epoch++;controller.abort();transport.dispose?.();renderer.dispose();}
  };return api;
}
