# -*- coding: utf-8 -*-
"""浏览器上手页(补审查 L5:登录入口缺失)。app.py 以 HTMLResponse 挂 /login.html 与 /app.html。
- 前缀自适应:页面从自身 pathname 推导 P('' 或 '/mdagent'),API/命令都拼 P,故 spike(无 nginx)与生产(nginx 剥 /mdagent)通用。
- 认证:登录 POST /v1/login 走 HttpOnly Cookie(credentials:include);仪表盘接口靠 Cookie(current_user 双认)。
- 纯静态 HTML/CSS/JS,无外部依赖。"""

LOGIN_HTML = r"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MD Exp · 登录</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#05060a;color:#e6edf3;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;padding:16px;overflow:hidden}
#bh{position:fixed;inset:0;width:100vw;height:100vh;z-index:-1;display:block}
.card{position:relative;width:100%;max-width:400px;background:rgba(18,23,32,.78);
 border:1px solid #30363d;border-radius:14px;padding:28px;backdrop-filter:blur(10px);
 box-shadow:0 8px 44px rgba(0,0,0,.65)}
h1{margin:0;font-size:26px;letter-spacing:1px}
.sub{margin:2px 0 20px;color:#8b949e;font-size:13px}
.tabs{display:flex;gap:8px;margin-bottom:18px}
.tabs button{flex:1;padding:9px;background:#0e1116;border:1px solid #30363d;color:#8b949e;border-radius:8px;cursor:pointer;font-size:14px}
.tabs button.on{background:#1f6feb;border-color:#1f6feb;color:#fff}
input,select{width:100%;padding:10px 12px;margin:7px 0;background:#0e1116;border:1px solid #30363d;
 border-radius:8px;color:#e6edf3;font-size:14px}
input:focus,select:focus{outline:none;border-color:#1f6feb}
.primary{width:100%;padding:11px;margin-top:12px;background:#238636;border:0;border-radius:8px;color:#fff;
 font-size:15px;cursor:pointer}
.primary:hover{background:#2ea043}
.msg{margin-top:12px;font-size:13px;min-height:18px}
.ok{color:#3fb950}.bad{color:#f85149}
label{font-size:12px;color:#8b949e;margin-top:6px;display:block}
</style></head><body>
<canvas id="bh"></canvas>
<div class="card">
 <h1>MD&nbsp;Exp</h1><div class="sub">智能来自信息维度</div>
 <div style="margin-top:14px"><a href="/manual" target="_blank" style="display:inline-block;padding:7px 18px;border-radius:22px;background:rgba(127,176,255,.12);border:1px solid rgba(127,176,255,.32);color:#9fc4ff;font-size:13px;letter-spacing:1px;text-decoration:none">📖 使用手册</a></div>
 <div class="tabs"><button id="t-login" class="on" onclick="tab('login')">登录</button>
  <button id="t-reg" onclick="tab('reg')">注册</button></div>
 <div id="p-login">
  <input id="lu" placeholder="用户名" autocomplete="username">
  <input id="lp" type="password" placeholder="密码" autocomplete="current-password" onkeydown="if(event.key==='Enter')login()">
  <button class="primary" onclick="login()">登录</button>
  <div id="lmsg" class="msg"></div>
 </div>
 <div id="p-reg" style="display:none">
  <input id="ru" placeholder="用户名(小写字母开头,3–16 位)">
  <input id="rp" type="password" placeholder="密码(≥8 位)">
  <input id="rinvite" placeholder="邀请码(向管理员索取)">
  <input id="rc" placeholder="联系方式(邮箱/微信,选填)">
  <input id="rreason" placeholder="用途简述(选填)">
  <p style="font-size:12px;color:#8b949e;margin:6px 0 0;line-height:1.5">审批通过后登录,在控制台「模型配置」里填你自己的 LLM Key 即可开始。</p>
  <button class="primary" onclick="reg()">提交申请</button>
  <div id="rmsg" class="msg"></div>
 </div>
</div>
<script>
const P = location.pathname.replace(/\/login\.html$/,'');   // '' 或 '/mdagent'
function tab(w){
 document.getElementById('t-login').classList.toggle('on',w==='login');
 document.getElementById('t-reg').classList.toggle('on',w==='reg');
 document.getElementById('p-login').style.display = w==='login'?'':'none';
 document.getElementById('p-reg').style.display   = w==='reg'?'':'none';
}
async function login(){
 const m=document.getElementById('lmsg'); m.className='msg'; m.textContent='登录中…';
 try{
  const r=await fetch(P+'/v1/login',{method:'POST',credentials:'include',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({username:document.getElementById('lu').value.trim(),password:document.getElementById('lp').value})});
  if(!r.ok){const d=await r.json().catch(()=>({}));m.className='msg bad';m.textContent=d.detail||('登录失败 '+r.status);return;}
  m.className='msg ok';m.textContent='登录成功,进入…';location.href=P+'/app.html';
 }catch(e){m.className='msg bad';m.textContent='网络错误:'+e.message;}
}
async function reg(){
 const m=document.getElementById('rmsg'); m.className='msg'; m.textContent='提交中…';
 try{
  const r=await fetch(P+'/v1/apply',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({username:document.getElementById('ru').value.trim(),password:document.getElementById('rp').value,
    contact:document.getElementById('rc').value.trim(),reason:document.getElementById('rreason').value.trim(),invite_code:document.getElementById('rinvite').value.trim()})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){m.className='msg bad';m.textContent=d.detail||('提交失败 '+r.status);return;}
  m.className='msg ok';m.textContent=(d.msg||'已受理')+'。管理员审批通过后即可登录。';
 }catch(e){m.className='msg bad';m.textContent='网络错误:'+e.message;}
}
/* —— 黑洞背景:星空绕黑洞公转 + 吸积盘 + 事件视界光子环（“智能来自信息维度”）—— */
(function(){
 const c=document.getElementById('bh');if(!c)return;const x=c.getContext('2d');
 let W,H,cx,cy,t=0;const stars=[];
 function resize(){W=c.width=innerWidth;H=c.height=innerHeight;cx=W/2;cy=H*0.42;}
 resize();addEventListener('resize',resize);
 for(let i=0;i<240;i++)stars.push({a:Math.random()*6.283,r:70+Math.random()*Math.max(W,H),s:0.2+Math.random()*0.9,tw:Math.random()*6.283});
 function frame(){
  t+=0.006;
  x.fillStyle='rgba(3,5,10,0.32)';x.fillRect(0,0,W,H);
  for(const st of stars){
   const ang=st.a+t*st.s*0.14, px=cx+Math.cos(ang)*st.r, py=cy+Math.sin(ang)*st.r*0.6;
   const tw=0.5+0.5*Math.sin(st.tw+t*3);
   x.fillStyle='rgba(180,205,255,'+((0.12+0.5*tw)*Math.min(1,140/st.r))+')';
   x.fillRect(px,py,1.5,1.5);
  }
  for(let k=0;k<3;k++){
   const rr=72+k*26, g=x.createLinearGradient(cx-rr,cy,cx+rr,cy);
   g.addColorStop(0,'rgba(90,160,255,0)');
   g.addColorStop(0.5,k%2?'rgba(255,150,60,0.5)':'rgba(120,180,255,0.46)');
   g.addColorStop(1,'rgba(90,160,255,0)');
   x.strokeStyle=g;x.lineWidth=3-k*0.5;
   x.beginPath();x.ellipse(cx,cy,rr,rr*0.32,t*(0.3-k*0.05),0,6.283);x.stroke();
  }
  const eh=46, ring=x.createRadialGradient(cx,cy,eh*0.7,cx,cy,eh*1.55);
  ring.addColorStop(0,'#000');ring.addColorStop(0.72,'#000');
  ring.addColorStop(0.9,'rgba(255,178,90,0.9)');ring.addColorStop(1,'rgba(255,178,90,0)');
  x.fillStyle=ring;x.beginPath();x.arc(cx,cy,eh*1.55,0,6.283);x.fill();
  x.fillStyle='#000';x.beginPath();x.arc(cx,cy,eh,0,6.283);x.fill();
  requestAnimationFrame(frame);
 }
 frame();
})();
</script></body></html>"""


APP_HTML = r"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MD Exp · 控制台</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0e1116;color:#e6edf3;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;justify-content:space-between;padding:14px 22px;border-bottom:1px solid #30363d;background:#161b22}
header .brand{font-size:19px;letter-spacing:1px}
header .who{color:#8b949e;font-size:13px}
header button{margin-left:12px;padding:6px 12px;background:#0e1116;border:1px solid #30363d;color:#e6edf3;border-radius:7px;cursor:pointer;font-size:13px}
main{max-width:860px;margin:0 auto;padding:22px 16px}
.row{display:flex;gap:16px;flex-wrap:wrap}
.panel{flex:1;min-width:280px;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px;margin-bottom:16px}
.panel h2{margin:0 0 12px;font-size:15px;color:#8b949e;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 6px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:500}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.on{background:#3fb950}.off{background:#6e7681}
.btn{padding:9px 16px;background:#1f6feb;border:0;border-radius:8px;color:#fff;cursor:pointer;font-size:14px}
.btn:hover{background:#388bfd}.btn:disabled{background:#30363d;color:#6e7681;cursor:not-allowed}
.go{display:inline-block;padding:12px 22px;background:#238636;color:#fff;border-radius:10px;text-decoration:none;font-size:16px}
.go.dis{background:#30363d;color:#6e7681;pointer-events:none}
.hint{color:#8b949e;font-size:12px;margin-top:8px}
.metric{display:inline-block;margin-right:24px}.metric b{font-size:22px;display:block}
#modal,#keymodal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;padding:16px}
#modal .box,#keymodal .box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:22px;max-width:640px;width:100%}
pre{background:#0e1116;border:1px solid #30363d;border-radius:8px;padding:12px;overflow:auto;font-size:12.5px;white-space:pre-wrap;word-break:break-all}
code{color:#79c0ff}
</style></head><body>
<header><div class="brand">MD&nbsp;Exp</div>
 <div><span class="who" id="who">…</span><span id="acct" style="margin-left:10px;font-size:13px"></span><button onclick="logout()">登出</button></div></header>
<main>
 <div class="panel" style="text-align:center">
  <a id="go" class="go dis" href="#">打开智能体 →</a>
  <div class="hint" id="gohint">先添加一台在线机器,才能开始跑模拟</div>
 </div>
 <div class="panel" id="llmpanel"><!--BYOK-KEYCFG-->
  <h2>模型配置 · 我的 LLM Key</h2>
  <div id="llmcfg"><span class="hint">加载中…</span></div>
  <div style="margin-top:12px"><button class="btn" onclick="openKey()">配置 / 更换 Key</button></div>
 </div>
 <div class="panel">
  <h2>我的机器</h2>
  <table><thead><tr><th>名称</th><th>系统</th><th>状态</th><th>最后在线</th></tr></thead>
   <tbody id="nodes"><tr><td colspan="4" style="color:#8b949e">加载中…</td></tr></tbody></table>
  <div style="margin-top:14px"><button class="btn" onclick="addMachine()">+ 添加机器</button></div>
 </div>
 <div class="panel">
  <h2>系统检测 · 运行前预检</h2>
  <div id="preflight"><span class="hint">检测 node01 上模拟工具是否齐全…</span></div>
 </div>
 <div class="panel">
  <h2>运行(发车的模拟)</h2>
  <table><thead><tr><th>Run</th><th>状态</th><th>Step</th><th>曲线点</th><th>起始</th></tr></thead>
   <tbody id="runs"><tr><td colspan="5" style="color:#8b949e">加载中…</td></tr></tbody></table>
  <div id="curve" style="margin-top:12px"></div>
 </div>
 <div class="panel">
  <h2>运行的程序 · node01 实时进程</h2>
  <table><thead><tr><th>标签</th><th>引擎</th><th>目录</th><th>PID</th><th>状态</th></tr></thead>
   <tbody id="procs"><tr><td colspan="5" style="color:#8b949e">加载中…</td></tr></tbody></table>
 </div>
 <div class="panel">
  <h2>用量</h2>
  <div id="usage"><span class="hint">加载中…</span></div>
 </div>
</main>
<div id="modal" onclick="if(event.target.id==='modal')closeModal()"><div class="box">
 <h2 style="margin-top:0">添加机器</h2>
 <p class="hint">在<b>目标机器</b>(要跑 LAMMPS 的那台,Linux)执行下面这行,10 分钟内有效、单次使用:</p>
 <pre id="cmd">…</pre>
 <p class="hint">装好后本机会自动连回;这里的"我的机器"会出现绿点。Windows 见文档。</p>
 <div style="text-align:right"><button class="btn" onclick="closeModal()">关闭</button></div>
</div></div>
<div id="keymodal" onclick="if(event.target.id==='keymodal')closeKey()"><div class="box">
 <h2 style="margin-top:0">配置 LLM Key</h2>
 <p class="hint">你的 Key 只用于调用大模型,平台不共享、可随时更换。选择服务商并粘贴你自己的 API Key:</p>
 <select id="kprov" style="width:100%;padding:10px 12px;margin:7px 0;background:#0e1116;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:14px">
  <option value="kimi-coding">Kimi · kimi-coding</option>
  <option value="bigmodel-anthropic">智谱 Anthropic · bigmodel-anthropic</option>
  <option value="zai-coding-cn">Z.ai · zai-coding-cn</option>
 </select>
 <input id="kkey" placeholder="粘贴你的 API Key" style="width:100%;padding:10px 12px;margin:7px 0;background:#0e1116;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:14px">
 <div id="kmsg" style="min-height:18px;font-size:13px;color:#8b949e"></div>
 <div style="text-align:right;margin-top:10px"><button class="btn" onclick="saveKey()">保存</button> <button class="btn" style="background:#30363d" onclick="closeKey()">取消</button></div>
</div></div>
<script>
const P = location.pathname.replace(/\/app\.html$/,'');   // '' 或 '/mdagent'
const ORIGIN = location.origin;
const AGENT_PORT = 8081;   // 「打开智能体」专用端口(nginx-agent-server.conf;改端口需两处同步 + 安全组放行)
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&#34;',"'":'&#39;'}[c]))}
async function api(method,path,body){
 const o={method,credentials:'include',headers:{'Content-Type':'application/json'}};
 if(body)o.body=JSON.stringify(body);
 const r=await fetch(P+path,o);
 if(r.status===401){location.href=P+'/login.html';throw new Error('未登录');}
 const d=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));
 return d;
}
let online=0;
async function load(){
 try{
  const nd=await api('GET','/v1/nodes');
  const rows=(nd.nodes||[]);
  online=rows.filter(n=>n.online).length;
  window.onlineNode=rows.find(n=>n.online)||null;
  document.getElementById('nodes').innerHTML = rows.length?rows.map(n=>{
   const os=(n.hello&&n.hello.platform)||n.platform||'?';
   const t=n.last_seen?new Date(n.last_seen*1000).toLocaleString():'—';
   return `<tr><td>${esc(n.name||n.node_id)}</td><td>${esc(os)}</td>`+
    `<td><span class="dot ${n.online?'on':'off'}"></span>${n.online?'在线':'离线'}</td><td>${esc(t)}</td></tr>`;
  }).join(''):'<tr><td colspan="4" style="color:#8b949e">还没有机器,点下方"添加机器"</td></tr>';
  const go=document.getElementById('go'),hint=document.getElementById('gohint');
  if(online>0){go.className='go';go.href=location.protocol+'//'+location.hostname+':'+AGENT_PORT+'/';hint.textContent='已有 '+online+' 台在线机器,可以开始';}
  else{go.className='go dis';hint.textContent='先添加一台在线机器,才能开始跑模拟';}
 }catch(e){document.getElementById('nodes').innerHTML='<tr><td colspan="4" class="hint">'+esc(e.message)+'</td></tr>';}
 try{
  const u=await api('GET','/v1/usage'); const t=(u.tokens&&u.tokens.total)||{};
  document.getElementById('who').textContent=u.username||'';
  document.getElementById('usage').innerHTML=
   `<span class="metric"><b>${t.calls||0}</b>累计调用</span>`+
   `<span class="metric"><b>${(t.in||0)+(t.out||0)}</b>tokens</span>`+
   `<span class="metric"><b>¥${(t.cost||0).toFixed(4)}</b>费用</span>`;
 }catch(e){document.getElementById('usage').innerHTML='<span class="hint">'+esc(e.message)+'</span>';}
 loadRuns(); loadProcs(); loadPreflight(); loadMe();
}
async function loadRuns(){
 try{
  const d=await api('GET','/v1/runs'); const rs=d.runs||[];
  document.getElementById('runs').innerHTML = rs.length?rs.map(r=>{
   const st={running:'跑中',exited:'已完成',lost:'失联'}[r.state]||r.state;
   const dot=r.state==='running'?'on':'off';
   const t=r.started?new Date(r.started*1000).toLocaleString():'—';
   return `<tr style="cursor:pointer" onclick="showRun('${esc(r.run_id)}')">`+
    `<td><code>${esc(String(r.run_id).slice(0,10))}</code></td>`+
    `<td><span class="dot ${dot}"></span>${esc(st)}${r.exit_code!=null?'('+esc(r.exit_code)+')':''}</td>`+
    `<td>${r.last_step>=0?r.last_step:'—'}</td><td>${r.points||0}</td><td>${t}</td></tr>`;
  }).join(''):'<tr><td colspan="5" style="color:#8b949e">还没有发车的模拟(经智能体 sim_exec 发射后出现)</td></tr>';
  const auto=rs.find(r=>r.state==='running')||rs[0];   // 图谱:自动画 running/最近 run 的温度曲线
  if(auto)showRun(auto.run_id);
 }catch(e){document.getElementById('runs').innerHTML='<tr><td colspan="5" class="hint">'+esc(e.message)+'</td></tr>';}
}
async function loadProcs(){
 const el=document.getElementById('procs'); const n=window.onlineNode;
 if(!n){el.innerHTML='<tr><td colspan="5" style="color:#8b949e">无在线机器</td></tr>';return;}
 try{
  const d=await api('GET','/v1/nodes/'+n.node_id+'/ps'); const ps=d.procs||[];
  el.innerHTML=ps.length?ps.map(p=>{
   const eng=String((p.argv&&p.argv[0])||'').split('/').pop();
   const alive=p.state==='running'||p.alive;
   return `<tr><td>${esc(p.label||'—')}</td><td><code>${esc(eng||'?')}</code></td>`+
    `<td>${esc(p.cwd||'—')}</td><td>${esc(p.pid||'—')}</td>`+
    `<td><span class="dot ${alive?'on':'off'}"></span>${esc(p.state||(alive?'running':'—'))}</td></tr>`;
  }).join(''):'<tr><td colspan="5" style="color:#8b949e">node01 上暂无运行的模拟</td></tr>';
 }catch(e){el.innerHTML='<tr><td colspan="5" class="hint">'+esc(e.message)+'</td></tr>';}
}
async function loadPreflight(){
 const el=document.getElementById('preflight'); const n=window.onlineNode;
 if(!n){el.innerHTML='<span class="hint">无在线机器,无法检测</span>';return;}
 try{
  const d=await api('GET','/v1/nodes/'+n.node_id+'/preflight');
  if(!d.online){el.innerHTML='<span class="hint">节点离线,无法检测</span>';return;}
  const sb=d.sandbox||{};
  const tools=Object.entries(d.tools||{}).map(([k,v])=>
    `<span style="margin-right:14px;white-space:nowrap"><span class="dot ${v.ok?'on':'off'}"></span>${esc(k)}</span>`).join('');
  const cands=(d.engine_candidates||[]).map(c=>`<code>${esc(c.path)}</code>`).join('<br>')
    ||'<span class="hint">未探到(可能都在 PATH,或需 node01 补装/授权)</span>';
  el.innerHTML=
   `<div style="margin-bottom:8px"><span class="dot ${sb.available?'on':'off'}"></span><b>沙箱</b> ${esc(sb.backend||'?')}/${esc(sb.enforcement||'?')}`+
   ` · <b>工作根</b> <code>${esc(d.root||'?')}</code> · <b>壳子</b> v${esc(d.version||'?')}</div>`+
   `<div style="margin-bottom:8px"><b>PATH 工具:</b> ${tools}</div>`+
   `<div style="margin-bottom:6px"><b>引擎全路径(发射用):</b><br>${cands}</div>`+
   `<div class="hint">${esc(d.note||'')}</div>`;
 }catch(e){el.innerHTML='<span class="hint">'+esc(e.message)+'</span>';}
}
async function showRun(rid){
 const box=document.getElementById('curve'); box.innerHTML='加载曲线…';
 try{
  const r=await api('GET','/v1/runs/'+rid); const th=r.thermo||[];
  if(!th.length){box.innerHTML='<span class="hint">暂无 thermo 数据(刚发车或未输出)</span>';return;}
  box.innerHTML='<div class="hint">Run '+esc(String(rid).slice(0,10))+' · 温度曲线(Temp vs Step,'+th.length+' 点)</div>'+curveSVG(th,'temp');
 }catch(e){box.innerHTML='<span class="hint">'+esc(e.message)+'</span>';}
}
function curveSVG(th,key){
 const pts=th.filter(p=>typeof p[key]==='number');
 if(pts.length<2)return '<span class="hint">曲线点太少</span>';
 const W=800,H=180,pad=32;
 const xs=pts.map(p=>p.step),ys=pts.map(p=>p[key]);
 const x0=xs.reduce((a,b)=>b<a?b:a),x1=xs.reduce((a,b)=>b>a?b:a);
 const y0=ys.reduce((a,b)=>b<a?b:a),y1=ys.reduce((a,b)=>b>a?b:a);
 const sx=s=>pad+(x1>x0?(s-x0)/(x1-x0):0.5)*(W-2*pad);
 const sy=v=>H-pad-(y1>y0?(v-y0)/(y1-y0):0.5)*(H-2*pad);
 const d=pts.map((p,i)=>(i?'L':'M')+sx(p.step).toFixed(1)+' '+sy(p[key]).toFixed(1)).join(' ');
 return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;background:#0e1116;border:1px solid #21262d;border-radius:8px">`+
  `<path d="${d}" fill="none" stroke="#1f6feb" stroke-width="1.5"/>`+
  `<text x="${pad}" y="15" fill="#8b949e" font-size="11">${key} ${y0.toFixed(2)}–${y1.toFixed(2)}</text>`+
  `<text x="${pad}" y="${H-10}" fill="#8b949e" font-size="11">step ${x0}</text>`+
  `<text x="${W-pad}" y="${H-10}" fill="#8b949e" font-size="11" text-anchor="end">step ${x1}</text></svg>`;
}
async function loadMe(){
 try{
  const me=await api('GET','/v1/me');
  window.myProvider=me.provider||'';window.myHasKey=!!me.has_key;
  const acct=document.getElementById('acct');
  if(acct){
   const tn=me.membership_expired?'会员已过期':({free:'免费',pro:'Pro 会员',vip:'VIP'}[me.membership]||'免费');
   const tc=me.membership_expired?'#f85149':({free:'#8b949e',pro:'#3fb950',vip:'#d29922'}[me.membership]||'#8b949e');
   let ex='';if(me.membership_expire){ex=' · 到期 '+new Date(me.membership_expire*1000).toLocaleDateString();}
   acct.innerHTML='<span style="color:'+tc+'">● '+tn+'</span> · 积分 <b>'+(me.points||0)+'</b>'+ex+(me.role==='admin'?(P===''?' · <a href="/v1/admin" style="color:#58a6ff;text-decoration:none">⚙ 管理台</a>':' · <span title="管理台仅内网可用:经 SSH 隧道把服务器 127.0.0.1:8090 转到本机后打开 /v1/admin" style="color:#8b949e;cursor:help">⚙ 管理台(内网)</span>'):'');
  }
  const el=document.getElementById('llmcfg');
  if(me.has_key){
   el.innerHTML='<span class="dot on"></span>已配置 · 服务商 <code>'+esc(me.provider||'?')+'</code>（可更换）';
  }else{
   el.innerHTML='<span class="dot off"></span><b style="color:#f85149">未配置 Key</b> — 智能体需要你自己的 LLM Key 才能运行,请点下方配置。';
   const go=document.getElementById('go'),hint=document.getElementById('gohint');
   go.className='go dis';hint.textContent='请先配置你的 LLM Key,智能体才能运行';
  }
 }catch(e){document.getElementById('llmcfg').innerHTML='<span class="hint">'+esc(e.message)+'</span>';}
}
function openKey(){
 if(window.myProvider){document.getElementById('kprov').value=window.myProvider;}
 document.getElementById('kmsg').textContent='';document.getElementById('kkey').value='';
 document.getElementById('keymodal').style.display='flex';
}
function closeKey(){document.getElementById('keymodal').style.display='none';}
async function saveKey(){
 const m=document.getElementById('kmsg');m.style.color='#8b949e';m.textContent='保存中…';
 const prov=document.getElementById('kprov').value,key=document.getElementById('kkey').value.trim();
 if(key.length<8){m.style.color='#f85149';m.textContent='Key 太短(至少 8 位)';return;}
 try{
  await api('POST','/v1/me/apikey',{provider:prov,api_key:key});
  m.style.color='#3fb950';m.textContent='已保存,正在生效…';
  setTimeout(function(){closeKey();load();},700);
 }catch(e){m.style.color='#f85149';m.textContent='保存失败:'+e.message;}
}
async function addMachine(){
 try{
  const d=await api('POST','/v1/nodes/enroll-code');
  const cmd=`curl -fsSL ${ORIGIN}${P}/get.sh | MDEXP_SERVER=${ORIGIN}${P} MDEXP_CODE=${d.code} sh`;
  document.getElementById('cmd').textContent=cmd;
  document.getElementById('modal').style.display='flex';
 }catch(e){alert('取注册码失败:'+e.message);}
}
function closeModal(){document.getElementById('modal').style.display='none';load();}
async function logout(){try{await api('POST','/v1/logout');}catch(e){}location.href=P+'/login.html';}
load();setInterval(load,10000);
</script></body></html>"""
