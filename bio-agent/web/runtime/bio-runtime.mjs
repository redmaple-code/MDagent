import {createHash} from 'node:crypto';
import {readFileSync,mkdirSync,existsSync,lstatSync,realpathSync,writeFileSync} from 'node:fs';
import {isAbsolute,join} from 'node:path';
import {BridgeClient} from '../../agent/bridge-client.mjs';
import {createTools,contracts} from '../../agent/tools.mjs';
import {createRedactor} from '../../agent/sdk.mjs';
import {wireParameters,decodeWireArguments} from './wire-contracts.mjs';
import {presentCall,presentResult} from './tool-presentation.mjs';
import {targetHash} from '../../agent/execution-target.mjs';
import {installHumanApi} from './human-api.mjs';
import {installPreviewRelay} from './visualization/preview-relay.mjs';
import {installViewRuntime,displayToolNames} from './visualization/view-runtime.mjs';

export const name='bio-openmm-tools';
export const inject=['tools','systemPrompt','agents'];
export const toolNames=Object.keys(contracts.tools);
const domainPrompt=readFileSync(new URL('../../agent/domain-prompt.md',import.meta.url),'utf8');
const clean=createRedactor();

export function projectForSession(root,id){
  if(!isAbsolute(root)||typeof id!=='string'||id.length<1||id.length>256)throw new Error('BIO_SESSION_ID_INVALID');
  return join(root,createHash('sha256').update(id).digest('hex'));
}

export class SessionProjects{
  constructor({projectsRoot,python,Compile,remote,executionTarget,executionGate,bridgeFactory=options=>new BridgeClient(options)}){
    if(!isAbsolute(projectsRoot)||!isAbsolute(python))throw new Error('BIO_PATH_INVALID');
    mkdirSync(projectsRoot,{recursive:true,mode:0o700});
    if(lstatSync(projectsRoot).isSymbolicLink())throw new Error('BIO_PROJECT_ROOT_SYMLINK');
    this.root=realpathSync(projectsRoot);this.python=python;this.remote=remote;this.executionTarget=executionTarget;this.bridgeFactory=bridgeFactory;
    this.executionGate=executionGate;this.retries=new Map();
    this.checks=new Map(toolNames.map(name=>[name,Compile(contracts.tools[name].parameters)]));
    this.records=new Map();this.closed=false;
  }
  async get(id){
    if(this.closed)throw new Error('BIO_PLUGIN_CLOSED');
    await this.guard('connect',id,{});
    if(this.records.has(id)){
      const record=this.records.get(id);
      if(!record.bridge.closed){await record.bridge.ready;return record;}
      this.records.delete(id);
    }
    if(this.records.size>=64)throw new Error('BIO_SESSION_LIMIT');
    const project=projectForSession(this.root,id);
    mkdirSync(project,{recursive:true,mode:0o700});
    if(lstatSync(project).isSymbolicLink()||realpathSync(project)!==project)throw new Error('BIO_PROJECT_SYMLINK');
    const ownerPath=join(project,'web-session-owner.json');
    const remoteProject=this.remote?projectForSession(this.remote.projectsRoot,id):undefined;
    const owner={schemaVersion:'bio.web-session.v1',sessionId:id,project,...(this.remote?{remoteProject,remoteIdentity:this.remote.identity}:{}),
      ...(this.executionTarget?{executionTargetSha256:targetHash(this.executionTarget)}:{})};
    if(existsSync(ownerPath)){
      if(lstatSync(ownerPath).isSymbolicLink()||readFileSync(ownerPath,'utf8')!==JSON.stringify(owner)+'\n')throw new Error('BIO_SESSION_OWNER_CHANGED');
    }else writeFileSync(ownerPath,JSON.stringify(owner)+'\n',{flag:'wx',mode:0o600});
    const bridge=this.bridgeFactory({project,python:this.python,...(this.remote?{remote:this.remote,remoteProject}:{}),
      ...(this.executionTarget?{executionTarget:this.executionTarget}:{})});
    const uncertain=new Set();
    const record={sessionId:id,project:remoteProject||project,receiptProject:project,bridge,uncertain,
      tools:new Map(createTools(bridge,{uncertain}).map(tool=>[tool.name,tool])),ticking:false};
    this.records.set(id,record);
    try{await bridge.ready;}catch{if(this.records.get(id)===record)this.records.delete(id);await bridge.close();throw new Error('BIO_BRIDGE_UNAVAILABLE');}
    this.retries.delete(id);
    return record;
  }
  async guard(action,id,args,signal){if(this.executionGate)await this.executionGate(action,id,args,signal);}
  async execute(name,args,exec){
    if(!this.checks.get(name)?.Check(args))throw new Error('INVALID_RAW_ARGUMENTS');
    if(!exec.agent?.id||exec.agent.session?.id!==exec.agent.id)throw new Error('BIO_SESSION_REQUIRED');
    if(exec.signal.aborted)throw new Error('BIO_ABORTED');
    await this.guard(name,exec.agent.id,args,exec.signal);
    const record=await this.get(exec.agent.id);
    const result=await record.tools.get(name).execute(exec.callId,args,exec.signal);
    return clean(result.details);
  }
  async tick(){
    if(this.closed)return;
    const ids=new Set([...this.records.keys(),...this.retries.keys()]);
    await Promise.allSettled([...ids].map(async id=>{
      const retry=this.retries.get(id);
      if(retry&&Date.now()<retry.after)return;
      let record=this.records.get(id);
      if(!record||record.bridge.closed){
        try{record=await this.get(id);}catch{
          const count=(retry?.count||0)+1;this.retries.set(id,{count,after:Date.now()+Math.min(60000,2000*2**Math.min(count,5))});return;
        }
      }
      if(record.ticking)return;record.ticking=true;
      try{await this.guard('advance',id,{});await record.bridge.control('reconcile',{advance:true});}finally{record.ticking=false;}
    }));
  }
  async close(){this.closed=true;await Promise.allSettled([...this.records.values()].map(record=>record.bridge.close()));}
}

export async function installBio(ctx,config,Compile,{projects=new SessionProjects({...config,Compile})}={}){
  // Preset tools are inherited by sessions. A preset-wide empty allow would also hide its own
  // tools from those sessions, so mask the host layer explicitly and update on later additions.
  let mask,signature='',updating=false;
  const maskHostTools=()=>{
    if(updating)return;
    const names=ctx.tools.schemas().map(tool=>tool.name).sort();const next=JSON.stringify(names);
    if(next===signature)return;signature=next;updating=true;
    try{const replacement=names.length?ctx.tools.restrict({deny:names}):undefined;mask?.();mask=replacement;}finally{updating=false;}
  };
  maskHostTools();ctx.on('tools/change',maskHostTools);
  ctx.tools.presentAs('native');
  ctx.tools.guard(exec=>(toolNames.includes(exec.name)||config.viewEnabled&&displayToolNames.includes(exec.name))?undefined:'BIO_TOOL_NOT_ALLOWED');
  for(const tool of toolNames){
    const contract=contracts.tools[tool];
    const parameters=wireParameters(tool);const check=Compile(parameters);
    ctx.tools.register({name:tool,description:contract.description,parameters,
      output:{schema:{},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
      presentCall:args=>presentCall(tool,args),
      presentResult:(args,result)=>presentResult(tool,args,result),
      execute:(args,exec)=>{
        if(!check.Check(args))throw new Error('INVALID_WIRE_ARGUMENTS');
        return projects.execute(tool,decodeWireArguments(tool,args),exec);
      }});
  }
  ctx.systemPrompt.section({name:'deployment:persona',order:0,complete:true,text:({agent})=>{
    const project=agent?projectForSession(projects.remote?.projectsRoot||projects.root,agent.id):'(尚未创建会话)';
    const route=projects.remote?.transport==='node-ssh'?`经登记节点 ${projects.remote.nodeId} 的已有 SSH 配置连接最终计算目标 ${projects.executionTarget.targetId}`
      :projects.remote?.transport==='node-world'?`复用原平台绑定的登记节点 ${projects.remote.nodeId}，最终计算目标为 ${projects.executionTarget?.targetId||projects.remote.hostAlias}`
      :`通过 SSH 连接计算端 ${projects.remote?.hostAlias}`;
    const target=projects.remote?`本部署${route}；能力探测、结构检查、体系构建、拓扑生成、参数化、动力学、验收和分析都在最终计算端执行。网页服务器和 SSH 接入节点仅传输工具请求与收据。不得把计算端说成网页服务器，也不得建议网页服务器安装科学依赖。远程连接不可用时停止操作，不回退本机。下文的科学项目路径均位于最终计算端，终端导入和审阅也要在那里进行。\n`:'';
    return domainPrompt+'\n\n你通过本网页与普通客户对话。每轮优先问最必要的 1–3 项；客户不懂的设置可给出明确建议并解释，再请客户确认。不要要求客户填写内部 JSON、工具名或哈希。先检查实际输入；不要把空白项目说成已有文件。\n'+
      target+
      `本会话科学项目为 ${project}。页面“Bio 任务”可上传 PDB/ENT 结构到计算端、查看输入、完整审阅并批准草案、单独提交计算、查询/取消/恢复及下载产物；普通聊天附件仍不会自动成为科学输入。人工批准只产生 READY，不自动提交；聊天中的“批准”不能代替页面人工审阅。需要审阅时说明草案与版本并指向“Bio 任务”的审阅入口，不要求客户运行内部命令或填写 JSON。\n`+
      (projects.executionTarget?`固定计算目标为 ${projects.executionTarget.targetId}，资源策略允许 CPU 总线程 ${projects.executionTarget.resourcePolicy.cpuThreads}；已配置的 GPU 编号为 ${projects.executionTarget.resourcePolicy.gpuDevices.join(',')||'无'}。构建、最小化与分析用计算端 CPU，动力学及验证仅在实际探测与授权后选择 CPU/CUDA；连接 GPU 服务器不等于已启用 GPU。\n`:'')+
      (agent&&projects.records.get(agent.id)?.resourcePreference?`客户在“Bio 任务”选择了后续新方案的资源偏好：${JSON.stringify(projects.records.get(agent.id).resourcePreference)}。新方案优先采用该偏好；已有方案和授权不会因此改变。\n`:'')+
      '面向初次使用者时，首轮通常控制在 200–350 字，只讲眼下的下一步、最多两个必要问题；不要在缺结构时展开整套参数、工具名、软件版本、项目哈希路径或审批命令。没有结构时先说明需要标准蛋白 PDB，可在“Bio 任务”上传。\n'+
      '根据当前研究目标说明能力范围，不要每轮列出所有限制。能力名称必须有实际工具证据；这是 OpenMM 流程，不得宣称已有 GROMACS 流程。二肽也是短肽；不能说二肽不是小肽、二肽构象研究没有科学意义。这里只因测试文件和极短采样而不能得出研究结论。把 RMSD 等术语解释成具体意思。\n'+
      '客户当前明确的条件优先于你之前的建议和举例；“就用这份文件”只确认文件，不确认你提出的其他参数。客户说常温时给出如 25 ℃ = 298.15 K 的具体建议，不可沿用此前的 37 ℃ 建议。用户不懂参数时，先只用文字推荐具体模拟时长、温度、计算资源和用途，再请其确认，此轮不要调用 bio_plan。仅说“你推荐”不等于接受尚未展示的预算。客户后续接受具体建议或明确要求生成方案后再调用 bio_plan，避免重复确认。只请求先看方案不等于计算授权。\n'+
      '温度用摄氏度给出时必须按 K = ℃ + 273.15 精确换算；37 ℃ 是 310.15 K，不得写成 310 K 后称两者相等。蛋白或小肽构象分析默认只选择 protein，按目的用 backbone 等合适的对齐选择；溶剂仍参与动力学，水和离子不得混入肽的 RMSD/RMSF/回旋半径。分析建议须说明选择和用途。\n'+
      'RMSD 是相对参考结构的偏离，不是稳定性判据；数值大、上升或迟迟不平稳都不能直接宣称蛋白不稳定，正常柔性或结构域运动也能导致变化。不要声称固定几 ns 就一定足够采样；长度与重复应根据目标、体系和实际检查决定。说明本流程能产出的分析，不把尚未接入的方法说成已有功能。\n'+
      '本实现 RMSF 输出是每个选定原子的波动，CSV 含原子及残基身份；protein 全原子选择不是逐残基聚合，也不是只看骨架。对齐用 backbone 不会自动把统计选择改成 backbone。向新手可说“看哪些位置动得多”，不要把实际全原子结果误称为逐残基 RMSF。\n'+
      '现有 protein-ligand-external 只把新制备配体放在蛋白外部，已有验收证明限于制备和 MD 工程流程可运行；不导入或保留已结合配体的口袋姿态，不是对接后的复合物模拟入口。不要声称提交含配体 PDB 或已有结合位置就能用本流程验证停留、口袋接触或结合稳定性。当前分析契约只有 RMSD、RMSF、回旋半径，口袋接触/占据率/结合自由能未接入。客户问药物结合时应先直接说明这一区别和所需新增能力，不让客户白准备一个本流程不能接受的复合物文件。\n'+
      '膜或金属中心不支持时，不把去膜、删除金属当作原研究问题的默认解法；这会改变物理体系。先说明需要新增的膜构建、金属配位参数化能力。只有用户明确提出另一个简化研究目标时，才能再讨论其适用性，不能为了生成草案改变客户原来的目标。\n'+
      '修改尚未运行的 DRAFT 时先 bio_get 核对，用 bio_plan 生成新草案；可保持原 workflowId 形成新 revision，旧 revision 必须保留，新的 revision 没有旧 grant。bio_continue 仅适用于查询确认存在且可用的计算 attempt，不能把改温度/分析的草案当续跑，更不能编造 attempt ID。工具返回 ok=false 时必须准确说明未完成，不能当成成功。当前没有删除方案的工具，不得向客户承诺删除；用新版本区分当前采用的方案。';
  }});
  ctx.systemPrompt.suppressRuntimeContext();
  const turns=new Map();
  ctx.on('agent/request',async (payload,next)=>{
    const previous=turns.get(payload.agent.id);
    const turn=previous?.turn===payload.turn?previous:{turn:payload.turn,requests:0,tools:0,started:Date.now()};
    if(++turn.requests>12||Date.now()-turn.started>300000)throw new Error('BIO_TURN_BUDGET_EXCEEDED');
    turns.set(payload.agent.id,turn);
    const config=await next();return {...config,maxTokens:4096};
  });
  ctx.tools.guard(exec=>{
    const turn=turns.get(exec.agent?.id);
    if(turn&&++turn.tools>32)return 'BIO_TOOL_BUDGET_EXCEEDED';
  });
  const timer=setInterval(()=>{void projects.tick();},2000);timer.unref();
  ctx.effect(()=>async()=>{clearInterval(timer);await projects.close();});
  if(config.humanApi!==false)ctx.inject(['connection'],apiCtx=>{installHumanApi(apiCtx,projects,{tenantId:config.executionTarget?.tenantId||'local',toolNames});});
  if(config.previewEnabled)ctx.inject(['webServer','subprocess'],apiCtx=>{installPreviewRelay(apiCtx,projects,{toolNames,directEnabled:config.directEnabled});});
  if(config.previewEnabled&&config.viewEnabled)installViewRuntime(ctx,projects,{toolNames},Compile);
}
