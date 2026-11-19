import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {runInNewContext} from 'node:vm';
const settle=()=>new Promise(r=>setImmediate(r));
function fixture(stage){
  let definition,hold,closed=0,detached=0,loadingSession,creates=0,viewRpcCalls=0,modelCalls=0,errors=0,sessionCreates=0,dockOptions;const refs=[],states=[],effects=[],rpcSignals=[];let index=0;
  const pause=name=>name===stage?new Promise(r=>hold=r):Promise.resolve();
  const module={createDock:async options=>{dockOptions=options;creates++;loadingSession=options.sessionId;await pause('dock');return {close:()=>{closed++;options.onClose();},showError(){errors++;}};},bindView:async()=>{viewRpcCalls++;await pause('bind');return ()=>detached++;}};
  const React={createElement:(type,props,...children)=>({type,props,children}),Fragment:'fragment',useRef:value=>{const i=index++;return refs[i]??=({current:value});},useState:value=>{const i=index++;states[i]??=value;return [states[i],v=>{states[i]=v;}];},useEffect:(fn,deps)=>{const i=index++;if(effects[i]?.deps[0]!==deps[0]){effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()};}}};
  const source=readFileSync(new URL('../ui-cards/client.source.js',import.meta.url),'utf8').replace('__BIO_CARDS_PACKAGE_ID__','"test"').replace("import('/v1/bio/visualization/assets/'+assets.entry)",'loadModule()');
  const rows=new Map();runInNewContext(source,{window:{__ModuleLoader__:{load:m=>definition=m.factory(()=>React)}},React,AbortController,AbortSignal,crypto,sessionStorage:{getItem:()=> 'main'},fetch:async()=>{await pause('manifest');return {ok:true,json:async()=>({entry:'entry.js',styles:[]})};},loadModule:async()=>{await pause('module');return module;}});
  definition.apply({connection:{api:{sessions:{create:async args=>{assert.equal(args.cwd,'/engineering/workspace');sessionCreates++;return {result:{ok:true}};}}},rpc:{call:async()=>({ok:true,value:{}})}},slots:{inject:(_name,fn)=>fn(),register:(options,render)=>rows.set(options.key||options.id,render)}});
  const props=(id,blank=false,composerPhase=blank?'blank':'active')=>({sessionId:id,useSession:fn=>fn({blank,composerPhase}),useSessions:fn=>fn({byId:{[id]:{agentPreset:'bio',cwd:'/engineering/workspace'}}})});
  const render=id=>{index=0;const action=rows.get('bio-workflow')(props(id));const ctx={connection:{rpc:{call:async(_path,_action,_args,signal)=>{rpcSignals.push(signal);await pause('human-rpc');return {ok:true,value:{}};}}}};return action.type({...action.props,ctx});};
  const renderBlank=id=>{index=0;return rows.get('bio-workflow')(props(id,true));};
  const renderContextTask=id=>{index=0;return rows.get('bio-workflow')(props(id,true,'active'));};
  return {render,renderBlank,renderContextTask,rpcSignals,slots:()=>[...rows.keys()],humanApi:(...args)=>dockOptions.humanApi(...args),release:()=>hold(),stats:()=>({closed,detached,loadingSession,creates,viewRpcCalls,modelCalls,errors,sessionCreates}),unmount:()=>effects.forEach(e=>e?.cleanup?.())};
}
for(const end of ['request','session'])test('the native adapter forwards '+end+' cancellation to the actual RPC boundary',async()=>{
  const f=fixture('human-rpc'),action=f.render('manual');await action.children[1].props.onClick();const abort=new AbortController();
  const reading=f.humanApi('progress',{workflowId:'md',revision:1},abort.signal);const rejected=assert.rejects(reading,/BIO_REQUEST_ABORTED/);await settle();
  try{assert.equal(f.rpcSignals[0]?.aborted,false);if(end==='request')abort.abort();else f.unmount();assert.equal(f.rpcSignals[0].aborted,true);}
  finally{abort.abort();f.release();await rejected;f.unmount();}
});
test('the native human adapter rejects a late non-cancelable RPC after a source operation aborts',async()=>{
  const f=fixture('human-rpc'),action=f.render('manual');await action.children[1].props.onClick();const abort=new AbortController();
  const reading=f.humanApi('get',{workflowId:'md'},abort.signal);const rejected=assert.rejects(reading,/BIO_REQUEST_ABORTED/);await settle();abort.abort();f.release();await rejected;f.unmount();
});
test('late manifest, module, dock and attach results cannot survive a session switch',async()=>{
  for(const stage of ['manifest','module','dock']){
    const f=fixture(stage),old=f.render('old');const opening=old.children[1].props.onClick();await settle();f.render('new');f.release();await opening;
    const s=f.stats();if(stage==='manifest'||stage==='module')assert.equal(s.creates,0,stage);else assert.ok(s.closed>=1,stage);
    if(stage==='bind')assert.ok(s.detached>=1);f.unmount();
  }
});

test('manual_open_has_no_view_or_model_rpc',async()=>{
 const f=fixture(null),action=f.render('manual');await action.children[1].props.onClick();assert.equal(f.stats().viewRpcCalls,0);assert.equal(f.stats().modelCalls,0);assert.equal(f.stats().errors,0);f.unmount();
});
test('home and blank chats have no injected Bio or visualization action strip',()=>{
  const f=fixture(null);assert.equal(f.renderBlank('empty'),null);assert.equal(f.slots().includes('bio-empty-workflow'),false);assert.equal(f.stats().sessionCreates,0);assert.equal(f.stats().creates,0);f.unmount();
});
test('an existing context-only task exposes the same actions as the native active task header',()=>{
  const f=fixture(null);assert.equal(typeof f.renderContextTask('context-task')?.type,'function');f.unmount();
});
