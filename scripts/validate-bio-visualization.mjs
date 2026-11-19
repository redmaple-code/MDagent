import {createServer} from 'node:http';import {spawnSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync,existsSync,statSync,readdirSync} from 'node:fs';
import {join,isAbsolute} from 'node:path';import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
export class SharedEgress{
  constructor({bitsPerSecond=4000000,clock=()=>performance.now(),sleep=ms=>new Promise(r=>setTimeout(r,ms)),onSend=()=>{}}={}){Object.assign(this,{rate:bitsPerSecond/8,clock,sleep,onSend});this.queues=new Map();this.running=false;}
  async send(owner,bytes,kind,write){
    for(let start=0;start<bytes.length;start+=16384){const part=bytes.subarray(start,start+16384);await new Promise((resolve,reject)=>{if(!this.queues.has(owner))this.queues.set(owner,[]);this.queues.get(owner).push({part,kind,write,resolve,reject});if(!this.running)void this.pump();});}
  }
  async pump(){this.running=true;while(this.queues.size){const [owner,queue]=this.queues.entries().next().value;const item=queue.shift();this.queues.delete(owner);if(queue.length)this.queues.set(owner,queue);
    try{await this.sleep(item.part.length/this.rate*1000);await item.write(item.part);this.onSend({owner,kind:item.kind,bytes:item.part.length,sentAtMs:this.clock()});item.resolve();}catch(e){item.reject(e);}}
    this.running=false;
  }
}
export function assessView(view,{durationSeconds=180}={}){
  if(!view?.renderer?.ready||!(/^[a-f0-9]{64}$/).test(view.renderer.sourceSha256??'')||!Number.isInteger(view.renderer.sourceFrameIndex)||!Number.isFinite(view.renderer.timePs)||!view.finished||!view.actualLastFrameSeen||view.cachedDurationMs<durationSeconds*1000-1000)return {status:'NOT_ACCEPTED',reason:'ACTUAL_RENDERER_EVIDENCE_MISSING'};
  const pass=view.warmStructureMs<=4000&&view.playStartMs<=6000&&view.coldDisplayMs<=12000&&view.stallPercent<=1&&view.decodedMaxBytes<=33554432;
  return {status:pass?'PASS':'NOT_ACCEPTED',reason:pass?null:'PERFORMANCE_TARGET_NOT_MET'};
}
const root=fileURLToPath(new URL('../',import.meta.url));
export async function main(argv=process.argv.slice(2)){
  const values={};for(let i=0;i<argv.length;i+=2){if(!argv[i]?.startsWith('--')||argv[i+1]===undefined)throw new Error('INVALID_QA_ARGUMENTS');values[argv[i].slice(2)]=argv[i+1];}
  const out=values.out;if(!out||!isAbsolute(out)||existsSync(out))throw new Error('QA_REQUIRES_NEW_ABSOLUTE_OUTPUT');
  const shared=Number(values['shared-mbps']||5),rtt=Number(values['rtt-ms']||100),duration=Number(values['duration-seconds']||180),viewers=(values.viewers||'1,3,5').split(',').map(Number);
  if(shared!==5||rtt!==100||duration<1||duration>3600||viewers.some(v=>![1,3,5].includes(v)))throw new Error('INVALID_QA_ARGUMENTS');
  mkdirSync(out,{recursive:false,mode:0o700});
  const py=values.python||'/opt/anaconda3/envs/modeling/bin/python';
  const generation=spawnSync(py,['-B',join(root,'bio-agent/tests/preview_acceptance_fixture.py'),'--out',out,...(values['real-project']?['--real-project',values['real-project']]:[])],{encoding:'utf8',env:{...process.env,OMP_NUM_THREADS:'1',OPENBLAS_NUM_THREADS:'1'}});
  writeFileSync(join(out,'fixture-generation.log'),generation.stdout+generation.stderr);if(generation.status!==0)throw new Error('QA_FIXTURE_GENERATION_FAILED');
  const fixtures=JSON.parse(readFileSync(join(out,'fixtures.json')));const dist=join(root,'bio-agent/web/visualization/dist'),assets=JSON.parse(readFileSync(join(dist,'asset-manifest.json')));
  const network=[],views=new Map(),memory=[];const started=Date.now();
  const budget=new SharedEgress({bitsPerSecond:4000000,onSend:item=>network.push(item)});
  const data={...fixtures.benchmark.manifest,previewDirectory:fixtures.benchmark.previewDirectory};
  const options={durationSeconds:duration,viewers,sharedMbps:shared,rttMs:rtt};let finalized=false;
  function finalize(){
    const observations=viewers.flatMap(n=>Array.from({length:n},(_,i)=>n+'-'+i)).map(id=>{const v=views.get(id)||{};return {id,...v,...assessView(v,options)};});
    writeFileSync(join(out,'network.json'),JSON.stringify({sharedMbps:5,previewMbps:4,rttMs:100,oneGlobalScheduler:true,coldAssetBytes:network.filter(n=>n.kind==='asset').reduce((n,r)=>n+r.bytes,0),slices:network},null,2));
    writeFileSync(join(out,'views.json'),JSON.stringify(observations,null,2));writeFileSync(join(out,'memory.json'),JSON.stringify(memory,null,2));
    let bytes=0;const walk=p=>{for(const n of readdirSync(p)){const f=join(p,n);if(statSync(f).isDirectory())walk(f);else bytes+=statSync(f).size;}};walk(out);
    writeFileSync(join(out,'storage.json'),JSON.stringify({evidenceBytes:bytes,scientificOriginalsCopied:false,automaticDeletion:false}));
    writeFileSync(join(out,'manifest.json'),JSON.stringify({schemaVersion:'bio.preview-acceptance.v1',createdAt:new Date(started).toISOString(),options,engineeringFixture:true,realInput:fixtures.real.status,sourceSha256:data.source.sourceSha256,sourceLastFrame:data.sourceFrameCount-1,frontendEntry:assets.entry,frontendSha256:assets.assets.find(a=>a.assetId===assets.entry).sha256,scientificConvergenceAssessed:false,productionDeployment:false},null,2));
    const rows=observations.map(v=>`| ${v.id} | ${v.status} | ${Math.round(v.coldDisplayMs??0)} | ${Math.round(v.warmStructureMs??0)} | ${(v.stallPercent??0).toFixed(2)} |`).join('\n');
    writeFileSync(join(out,'summary.md'),`# MDagent browser preview QA\n\nOne shared 5 Mbps link, 100 ms RTT; preview and renderer assets paced at 4 Mbps. Browser engineering fixture: 300 residues plus a recognized small ligand. Each scenario: ${duration} s cached playback.\n\n| View | Status | Cold ms | Warm ms | Stall % |\n|---|---|---:|---:|---:|\n${rows}\n\nExisting MD source: ${fixtures.real.status}; original hashes unchanged: ${fixtures.real.originalHashesUnchanged??'unobserved'}. Scientific convergence and production/remote acceptance remain untested.\n\n100000-atom engineering pressure: maximum decoded window ${fixtures.pressure.decodedMaxBytes} bytes; original data kept, no production MD.\n`);
    finalized=true;
  }
  const parentHtml=`<!doctype html><html><meta charset="utf-8"><title>MDagent shared-bandwidth QA</title><body style="font:14px system-ui;background:#edf3f7"><h1>MDagent 5 Mbps 共享出口验收</h1><p>300 残基与小配体工程数据；每组 ${duration} 秒。结构与原始帧回读必须存在。</p><button id="start">开始 1 / 3 / 5 视图测试</button><p id="status">等待开始</p><div id="views" style="display:flex;flex-wrap:wrap;gap:10px"></div><script>
const groups=${JSON.stringify(viewers)},duration=${duration};let done=0;const results=[];
document.querySelector('#start').onclick=async()=>{document.querySelector('#start').disabled=true;for(const count of groups){done=0;document.querySelector('#views').replaceChildren();document.querySelector('#status').textContent='测试 '+count+' 个视图';for(let i=0;i<count;i++){const iframe=document.createElement('iframe');iframe.src='/viewer?owner='+count+'-'+i;iframe.style='width:'+Math.max(300,Math.floor((innerWidth-40)/count)-15)+'px;height:520px;border:1px solid #889';document.querySelector('#views').append(iframe);}await new Promise(resolve=>{const timer=setInterval(()=>{if(done===count){clearInterval(timer);resolve();}},200);});}await fetch('/finish',{method:'POST'});document.querySelector('#status').textContent='测试完成，证据已保存';};
window.addEventListener('message',e=>{if(e.origin!==location.origin)return;if(e.data?.finished){done++;results.push(e.data);} });
</script></body></html>`;
  const viewerHtml=owner=>`<!doctype html><html><meta charset="utf-8"><title>Viewport ${owner}</title><link rel="stylesheet" href="/assets/${assets.styles[0]}?owner=${owner}"><body style="margin:0;font:12px system-ui"><strong>视图 ${owner}</strong><div id="canvas" style="height:360px;position:relative;background:#111d2a"></div><pre id="state">冷资源加载…</pre><script type="module">
const start=0;let player,renderer,last=0,stall=0,cachedAt=0,peak=0,finished=false,stats={};
function observation(){return {...stats,finished,cachedDurationMs:cachedAt?performance.now()-cachedAt:0,stallPercent:cachedAt?stall/(performance.now()-cachedAt)*100:0,decodedMaxBytes:peak,renderer:renderer?.observe()};}
function display(){if(!renderer)return;const o=observation();document.querySelector('#state').textContent=JSON.stringify({sourceFrame:o.renderer.sourceFrameIndex,timePs:o.renderer.timePs,ready:o.renderer.ready,phase:finished?'完成':cachedAt?'缓存播放':'加载',stallPercent:o.stallPercent.toFixed(2)},null,2);fetch('/observe?owner=${owner}',{method:'POST',body:JSON.stringify(o)}).catch(()=>{});}
try{const module=await import('/assets/${assets.entry}?owner=${owner}');const warm=performance.now();renderer=await module.createMolstarRenderer(document.querySelector('#canvas'),()=>{});const m=await (await fetch('/manifest')).json();const transport=module.createPreviewTransport({sessionId:'engineering-${owner}',viewportId:'main',fetch:(url,opts)=>{const u=new URL(url,location.origin);u.searchParams.set('owner','${owner}');return fetch(u,opts);}});player=module.createPreviewPlayer({renderer,transport,onState:s=>{peak=Math.max(peak,s.decodedWindowBytes);if(cachedAt&&s.ready&&!s.buffering&&renderer.observe().sourceFrameIndex!==stats.lastFrame){const now=performance.now();if(last&&now-last>150)stall+=now-last-100;last=now;stats.lastFrame=renderer.observe().sourceFrameIndex;}}});await player.open(m);renderer.focus();stats.coldDisplayMs=performance.now()-start;stats.warmStructureMs=performance.now()-warm;const playAt=performance.now();player.play({startFrame:0,endFrame:m.frames.length-1,fps:10,direction:1,loop:true});await new Promise(r=>setTimeout(r,150));stats.playStartMs=performance.now()-playAt;
await Promise.all(m.files.filter(f=>f.kind==='xtc').map(f=>transport.getFile(m,f.fileId)));player.pause();await player.seekTime(m.frames.at(-1).timePs);stats.actualLastFrameSeen=renderer.observe().sourceFrameIndex===m.sourceFrameCount-1;
cachedAt=performance.now();player.play({startFrame:0,endFrame:m.frames.length-1,fps:10,direction:1,loop:true});const timer=setInterval(display,1000);await new Promise(r=>setTimeout(r,${duration}*1000));player.pause();await player.seekTime(m.frames.at(-1).timePs);finished=true;clearInterval(timer);display();await fetch('/observe?owner=${owner}',{method:'POST',body:JSON.stringify(observation())});parent.postMessage({finished:true,owner:'${owner}'},location.origin);
}catch(e){document.querySelector('#state').textContent='ERROR '+e.message;await fetch('/observe?owner=${owner}',{method:'POST',body:JSON.stringify({error:e.message,finished:true})});parent.postMessage({finished:true,owner:'${owner}'},location.origin);}
</script></body></html>`;
  const server=createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost'),owner=url.searchParams.get('owner')||'control';
    await new Promise(r=>setTimeout(r,rtt));res.setHeader('cache-control','no-store');
    if(url.pathname==='/observe'){let size=0,chunks=[];for await(const part of req){size+=part.length;if(size>32768)throw Error('QA_OBSERVATION_TOO_LARGE');chunks.push(part);}const value=JSON.parse(Buffer.concat(chunks));views.set(owner,value);memory.push({owner,at:Date.now(),decodedWindowBytes:value.decodedMaxBytes});res.end();return;}
    if(url.pathname==='/finish'){finalize();res.end();console.log(JSON.stringify({evidence:out,finished:true}));return;}
    if(url.pathname==='/'||url.pathname==='/viewer'){res.setHeader('content-type','text/html');await budget.send(owner,Buffer.from(url.pathname==='/'?parentHtml:viewerHtml(owner)),'page',part=>res.write(part));res.end();return;}
    if(url.pathname==='/manifest'){res.setHeader('content-type','application/json');const {previewDirectory,...manifest}=data;await budget.send(owner,Buffer.from(JSON.stringify(manifest)),'metadata',part=>res.write(part));res.end();return;}
    if(url.pathname.startsWith('/assets/')){const entry=assets.assets.find(a=>a.assetId===url.pathname.split('/').at(-1));if(!entry)throw Error('QA_ASSET_NOT_FOUND');res.setHeader('content-type',entry.assetId.endsWith('.js')?'text/javascript':'text/css');const encoding=req.headers['accept-encoding']?.includes('br')?'br':req.headers['accept-encoding']?.includes('gzip')?'gzip':null;if(encoding)res.setHeader('content-encoding',encoding);await budget.send(owner,readFileSync(join(dist,entry.path+(encoding?'.'+encoding:''))),'asset',part=>res.write(part));res.end();return;}
    const match=url.pathname.match(/^\/v1\/bio\/preview\/([^/]+)\/files\/([^/]+)$/);if(match&&match[1]===data.previewId){const entry=data.files.find(f=>f.fileId===match[2]);if(!entry)throw Error('QA_FILE_NOT_FOUND');const bytes=readFileSync(join(data.previewDirectory,entry.fileId));const offset=Number(url.searchParams.get('offset')),limit=Number(url.searchParams.get('limit'));const end=Math.min(offset+limit,bytes.length);res.setHeader('x-bio-source-sha256',data.source.sourceSha256);res.setHeader('x-bio-file-sha256',entry.sha256);res.setHeader('x-bio-next-offset',String(end));await budget.send(owner,bytes.subarray(offset,end),'preview',part=>res.write(part));res.end();return;}
    res.writeHead(404);res.end();
  }catch(error){if(!res.headersSent){res.writeHead(500);res.end(error.message);}else res.destroy();}});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));writeFileSync(join(out,'gateway.json'),JSON.stringify({url:'http://127.0.0.1:'+server.address().port,pid:process.pid,options}));
  console.log(JSON.stringify({url:'http://127.0.0.1:'+server.address().port,out,waitingForBrowser:true}));
  process.once('SIGINT',()=>{if(!finalized)finalize();server.close();});process.once('SIGTERM',()=>{if(!finalized)finalize();server.close();});return server;
}
if(process.argv[1]&&pathToFileURL(process.argv[1]).href===import.meta.url)await main();
