const finite=(value,fallback)=>Number.isFinite(value)?value:fallback;
const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
function limits(viewport){
  const width=Math.max(1,finite(viewport.width,1)),height=Math.max(1,finite(viewport.height,1));
  const margin=Math.min(8,(Math.min(width,height)-1)/2);
  return {width,height,margin};
}
export function clampPanelRect(rect,viewport){
  const {width,height,margin}=limits(viewport),maxWidth=width-2*margin,maxHeight=height-2*margin;
  const w=clamp(finite(rect.width,280),Math.min(280,maxWidth),maxWidth),h=clamp(finite(rect.height,360),Math.min(360,maxHeight),maxHeight);
  return {left:clamp(finite(rect.left,margin),margin,width-margin-w),top:clamp(finite(rect.top,margin),margin,height-margin-h),width:w,height:h};
}
export function adjustPanelRect(rect,gesture,viewport){
  const start=clampPanelRect(rect,viewport),dx=finite(gesture.dx,0),dy=finite(gesture.dy,0);
  if(gesture.mode==='move')return clampPanelRect({...start,left:start.left+dx,top:start.top+dy},viewport);
  if(gesture.mode!=='resize')throw new Error('INVALID_PANEL_GESTURE');
  const {width,height,margin}=limits(viewport),maxWidth=width-margin-start.left,maxHeight=height-margin-start.top;
  return {...start,width:clamp(start.width+dx,Math.min(280,maxWidth),maxWidth),height:clamp(start.height+dy,Math.min(360,maxHeight),maxHeight)};
}
export function bindFloatingPanel(element,{view=window}={}){
  const header=element.querySelector('[data-move]'),handle=element.querySelector('[data-resize]'),resetButton=element.querySelector('[data-reset]');
  let active=null,disposed=false,floating=false;
  const viewport=()=>({width:view.innerWidth,height:view.innerHeight});
  const rect=()=>{const r=element.getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height};};
  const apply=value=>{if(disposed)return;floating=true;Object.assign(element.style,{left:value.left+'px',top:value.top+'px',width:value.width+'px',height:value.height+'px',right:'auto',bottom:'auto',maxWidth:'none',minWidth:'0',boxSizing:'border-box'});};
  const control=event=>!!event.target.closest('button,input,select,a,summary');
  function finish(event){
    if(!active||event&&event.pointerId!==active.pointerId)return;
    const previous=active;active=null;try{previous.node.releasePointerCapture(previous.pointerId);}catch{}
  }
  function start(mode,node,event){
    if(disposed||active||event.button!==0||event.isPrimary===false||mode==='move'&&control(event))return;
    event.preventDefault();const initial=clampPanelRect(rect(),viewport());apply(initial);
    active={mode,node,pointerId:event.pointerId,x:event.clientX,y:event.clientY,initial};
    try{node.setPointerCapture(event.pointerId);}catch{}
  }
  const moveStart=event=>start('move',header,event),resizeStart=event=>start('resize',handle,event);
  const move=event=>{if(!active||disposed||event.pointerId!==active.pointerId)return;event.preventDefault();apply(adjustPanelRect(active.initial,{mode:active.mode,dx:event.clientX-active.x,dy:event.clientY-active.y},viewport()));};
  function fit(){
    if(disposed)return;finish();const current=rect(),bounded=clampPanelRect(current,viewport());
    if(floating||Object.keys(bounded).some(k=>Math.abs(bounded[k]-current[k])>.5))apply(bounded);
  }
  function reset(){
    if(disposed)return;finish();for(const key of ['left','top','width','height','right','bottom','maxWidth','minWidth','boxSizing'])element.style[key]='';floating=false;fit();
  }
  const doubleClick=event=>{if(!control(event))reset();};
  function key(mode,event){
    if(disposed||event.altKey||event.ctrlKey||event.metaKey||mode==='move'&&control(event))return;
    const steps={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},step=steps[event.key];if(!step)return;
    event.preventDefault();finish();const distance=event.shiftKey?25:10;apply(adjustPanelRect(rect(),{mode,dx:step[0]*distance,dy:step[1]*distance},viewport()));
  }
  const moveKey=event=>key('move',event),resizeKey=event=>key('resize',event);
  header.addEventListener('pointerdown',moveStart);handle.addEventListener('pointerdown',resizeStart);
  header.addEventListener('keydown',moveKey);handle.addEventListener('keydown',resizeKey);header.addEventListener('dblclick',doubleClick);resetButton.addEventListener('click',reset);
  for(const node of [header,handle])node.addEventListener('lostpointercapture',finish);
  view.addEventListener('pointermove',move,{passive:false});view.addEventListener('pointerup',finish);view.addEventListener('pointercancel',finish);view.addEventListener('resize',fit);fit();
  return ()=>{
    if(disposed)return;finish();disposed=true;header.removeEventListener('pointerdown',moveStart);handle.removeEventListener('pointerdown',resizeStart);
    header.removeEventListener('keydown',moveKey);handle.removeEventListener('keydown',resizeKey);header.removeEventListener('dblclick',doubleClick);resetButton.removeEventListener('click',reset);
    for(const node of [header,handle])node.removeEventListener('lostpointercapture',finish);
    view.removeEventListener('pointermove',move);view.removeEventListener('pointerup',finish);view.removeEventListener('pointercancel',finish);view.removeEventListener('resize',fit);
  };
}
