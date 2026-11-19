import test from 'node:test';import assert from 'node:assert/strict';
const panel=await import('../../visualization/src/floating-panel.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const rect={left:300,top:100,width:400,height:420},viewport={width:1000,height:800};
test('moving preserves size and clamps the window within the viewport',()=>{
  assert.equal(typeof panel.adjustPanelRect,'function');
  assert.deepEqual(panel.adjustPanelRect(rect,{mode:'move',dx:-800,dy:-500},viewport),{left:8,top:8,width:400,height:420});
  assert.deepEqual(panel.adjustPanelRect(rect,{mode:'move',dx:900,dy:900},viewport),{left:592,top:372,width:400,height:420});
});
test('corner resize changes width and height independently with a fixed origin',()=>{
  assert.equal(typeof panel.adjustPanelRect,'function');
  assert.deepEqual(panel.adjustPanelRect(rect,{mode:'resize',dx:150,dy:-30},viewport),{left:300,top:100,width:550,height:390});
  assert.deepEqual(panel.adjustPanelRect(rect,{mode:'resize',dx:1000,dy:1000},viewport),{left:300,top:100,width:692,height:692});
  assert.deepEqual(panel.adjustPanelRect(rect,{mode:'resize',dx:-1000,dy:-1000},viewport),{left:300,top:100,width:280,height:360});
});
test('viewport shrink keeps the title and resize handle on screen even below preferred minimums',()=>{
  assert.equal(typeof panel.clampPanelRect,'function');
  assert.deepEqual(panel.clampPanelRect(rect,{width:319,height:750}),{left:8,top:100,width:303,height:420});
  assert.deepEqual(panel.clampPanelRect(rect,{width:240,height:200}),{left:8,top:8,width:224,height:184});
});
function surface(){
  const listeners=new Map(),node=()=>({listeners:new Map(),addEventListener(type,fn){this.listeners.set(type,fn);},removeEventListener(type,fn){if(this.listeners.get(type)===fn)this.listeners.delete(type);},setAttribute(){},setPointerCapture(){},releasePointerCapture(){}});
  const header=node(),handle=node(),reset=node(),style={},element={style,querySelector:s=>s==='[data-move]'?header:s==='[data-resize]'?handle:reset,getBoundingClientRect:()=>({left:parseFloat(style.left)||300,top:parseFloat(style.top)||100,width:parseFloat(style.width)||400,height:parseFloat(style.height)||420})};
  const view={innerWidth:1000,innerHeight:800,addEventListener:(type,fn)=>listeners.set(type,fn),removeEventListener:(type,fn)=>{if(listeners.get(type)===fn)listeners.delete(type);}};
  const event=(extra={})=>({pointerId:1,button:0,isPrimary:true,clientX:310,clientY:110,target:{closest:()=>null},preventDefault(){},...extra});
  return {element,header,handle,reset,view,event,listeners};
}
test('header controls do not drag and pointer cancellation ends an active gesture',()=>{
  assert.equal(typeof panel.bindFloatingPanel,'function');const f=surface(),dispose=panel.bindFloatingPanel(f.element,{view:f.view});
  try{
    f.header.listeners.get('pointerdown')(f.event({target:{closest:()=>({tagName:'BUTTON'})}}));f.listeners.get('pointermove')(f.event({clientX:500,clientY:200}));assert.equal(f.element.getBoundingClientRect().left,300);
    f.header.listeners.get('pointerdown')(f.event());f.listeners.get('pointermove')(f.event({clientX:210,clientY:160}));assert.deepEqual(f.element.getBoundingClientRect(),{left:200,top:150,width:400,height:420});
    f.listeners.get('pointercancel')(f.event());f.listeners.get('pointermove')(f.event({clientX:700,clientY:200}));assert.equal(f.element.getBoundingClientRect().left,200);
  }finally{dispose();}assert.equal(f.listeners.size,0);assert.equal(f.header.listeners.size,0);
});
test('resize keyboard controls and reset alter geometry without touching the scene',()=>{
  assert.equal(typeof panel.bindFloatingPanel,'function');const f=surface(),dispose=panel.bindFloatingPanel(f.element,{view:f.view});
  try{
    f.handle.listeners.get('keydown')(f.event({key:'ArrowRight'}));f.handle.listeners.get('keydown')(f.event({key:'ArrowUp'}));assert.deepEqual(f.element.getBoundingClientRect(),{left:300,top:100,width:410,height:410});
    f.reset.listeners.get('click')(f.event());assert.deepEqual(f.element.getBoundingClientRect(),rect);
    f.header.listeners.get('pointerdown')(f.event());f.listeners.get('pointermove')(f.event({clientX:900,clientY:700}));f.listeners.get('pointerup')(f.event());f.view.innerWidth=319;f.view.innerHeight=750;f.listeners.get('resize')();assert.deepEqual(f.element.getBoundingClientRect(),{left:8,top:322,width:303,height:420});
  }finally{dispose();}
});
