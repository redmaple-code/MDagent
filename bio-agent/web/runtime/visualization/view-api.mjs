import {validateViewCommand} from './contracts.mjs';
import {bindingMatches} from '../../visualization/src/frame-binding.mjs';
import {defaultAppearance} from './appearance.mjs';
export class ViewCommandHandler{
  constructor({sessionId,viewportId,player,renderer,openSource}){Object.assign(this,{sessionId,viewportId,player,renderer,openSource});this.tail=Promise.resolve();}
  observe(){
    const actual=this.renderer.observe(),state=this.player.state;
    // Native orbit/zoom may update the snapshot without emitting stateChanged.
    const cameraIdentity=JSON.stringify(actual.camera??null);this.cameraRevision=Math.max(this.cameraRevision??0,actual.cameraRevision??0);
    if(this.cameraIdentity!==undefined&&this.cameraIdentity!==cameraIdentity)this.cameraRevision++;this.cameraIdentity=cameraIdentity;
    return {binding:{sessionId:this.sessionId,viewportId:this.viewportId,sourceSha256:state.sourceSha256??null,
      sourceRevision:state.sourceRevision??0,visualRevision:(state.visualRevision??0)+(actual.selectionRevision??0),frameRevision:state.frameRevision??0,cameraRevision:this.cameraRevision},
      previewFrameIndex:state.frameIndex??0,previewFrameCount:this.player.manifest?.frames.length??1,sourceFrameCount:this.player.manifest?.sourceFrameCount??state.sourceFrameCount??1,
      ready:actual.ready===true&&state.ready===true&&actual.sourceSha256===state.sourceSha256,
      playing:state.playing===true,buffering:state.buffering===true,sourceFrameIndex:actual.sourceFrameIndex??null,timePs:actual.timePs??null,
      selection:actual.selection??[],representation:actual.representation??'cartoon',appearance:actual.appearance??defaultAppearance(),camera:actual.camera??null};
  }
  async execute(command){
    validateViewCommand(command);
    const run=async()=>{
      const before=this.observe();
      if(command.kind==='observe'){if(command.expected.sessionId!==this.sessionId||command.expected.viewportId!==this.viewportId)throw new Error('VIEW_BINDING_CHANGED');return {operationId:command.operationId,observation:before};}
      if(!bindingMatches(command.expected,before.binding,command.kind))throw new Error('VIEW_BINDING_CHANGED');
      if(!['openSource','pause'].includes(command.kind)&&!before.ready)throw new Error('VIEWPORT_NOT_READY');
      const p=command.payload;let value;
      switch(command.kind){
        case 'openSource':if(!this.openSource)throw new Error('VIEW_OPEN_SOURCE_UNAVAILABLE');await this.openSource(p.source);break;
        case 'seekTime':value=await this.player.seekTime(p.timePs);break;
        case 'play':value=this.player.play(p);break;
        case 'pause':value=await this.player.pause();break;
        case 'setRepresentation':await (this.player.render?this.player.render('setRepresentation',p.representation,p.appearance):this.renderer.setRepresentation(p.representation,p.appearance));this.player.state.visualRevision++;break;
        case 'select':await (this.player.render?this.player.render('select',p.sourceAtomIndices):this.renderer.select(p.sourceAtomIndices));this.player.state.visualRevision++;break;
        case 'focus':await (this.player.render?this.player.render('focus'):this.renderer.focus());break;
      }
      const observation=this.observe();if(!observation.ready)throw new Error('VIEWPORT_NOT_READY');
      if(command.kind==='openSource'?observation.binding.sourceSha256!==p.source.sourceSha256:observation.binding.sourceSha256!==before.binding.sourceSha256)throw new Error('VIEW_BINDING_CHANGED');
      return {operationId:command.operationId,observation,...(command.kind==='seekTime'?{requestedTimePs:p.timePs,actualTimePs:value.actualTimePs}:{})};
    };
    const next=this.tail.catch(()=>{}).then(run);this.tail=next;return next;
  }
}
