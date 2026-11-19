import test from 'node:test';import assert from 'node:assert/strict';
const theme=await import('../../visualization/src/theme.mjs').catch(e=>{if(e.code!=='ERR_MODULE_NOT_FOUND')throw e;return {};});
function fixture(){
  let background='rgb(255, 255, 255)',callback,disconnected=0;const colors=[],observed=[],listeners=new Map();
  const body={},documentElement={},document={body,documentElement};
  class MutationObserver{constructor(fn){callback=fn;}observe(target,options){observed.push({target,options});}disconnect(){disconnected++;}}
  const media={addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
  return {colors,observed,body,documentElement,listeners,env:{document,MutationObserver,getComputedStyle:()=>({backgroundColor:background}),matchMedia:()=>media},set:value=>background=value,change:()=>callback(),disconnected:()=>disconnected};
}
test('renderer background follows the current DSH light/dark palette without duplicate updates',()=>{
  assert.equal(typeof theme.bindCanvasTheme,'function');const f=fixture(),close=theme.bindCanvasTheme({},color=>f.colors.push(color),f.env);
  assert.deepEqual(f.colors,[0xffffff]);f.set('rgb(21, 21, 23)');f.change();f.change();assert.deepEqual(f.colors,[0xffffff,0x151517]);
  assert.ok(f.observed.some(o=>o.target===f.body&&o.options.attributeFilter.includes('data-ds-dark-theme')));close();
});
test('system theme changes update an open renderer and disposal rejects queued notifications',()=>{
  assert.equal(typeof theme.bindCanvasTheme,'function');const f=fixture(),close=theme.bindCanvasTheme({},color=>f.colors.push(color),f.env);
  f.set('rgb(44 44 46)');f.listeners.get('change')();assert.equal(f.colors.at(-1),0x2c2c2e);
  close();f.set('rgb(255,255,255)');f.change();assert.equal(f.colors.length,2);assert.equal(f.disconnected(),1);assert.equal(f.listeners.size,0);
});
test('an invalid computed color does not replace a valid renderer background',()=>{
  assert.equal(typeof theme.bindCanvasTheme,'function');const f=fixture(),close=theme.bindCanvasTheme({},color=>f.colors.push(color),f.env);
  for(const value of ['transparent','rgb(999,0,0)','not-a-color']){f.set(value);f.change();}assert.deepEqual(f.colors,[0xffffff]);close();
});
