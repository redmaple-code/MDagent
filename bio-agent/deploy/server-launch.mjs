#!/usr/bin/env node
import {spawn} from 'node:child_process';
import {existsSync,lstatSync,mkdirSync,readFileSync,readdirSync,realpathSync,symlinkSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname,isAbsolute,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {loadCompiler} from './compiler.mjs';
import {loadRemoteConnection} from '../agent/remote-bridge.mjs';
import {loadTarget,targetHash} from '../agent/execution-target.mjs';

export const releaseRoot=fileURLToPath(new URL('../',import.meta.url));
const json=value=>JSON.stringify(value,null,2)+'\n';

function initial(path,data){
  mkdirSync(dirname(path),{recursive:true,mode:0o700});
  if(existsSync(path)){
    if(lstatSync(path).isSymbolicLink()||readFileSync(path,'utf8')!==data)throw new Error('BIO_STATE_CONFIG_CONFLICT');
  }else writeFileSync(path,data,{flag:'wx',mode:0o600});
}

function link(path,target){
  mkdirSync(dirname(path),{recursive:true,mode:0o755});
  try{
    if(!lstatSync(path).isSymbolicLink()||realpathSync(path)!==realpathSync(target))throw new Error('BIO_DEPENDENCY_LINK_CONFLICT');
  }catch(error){if(error.code==='ENOENT')symlinkSync(target,path,'dir');else throw error;}
}

export function packageAt(dshRoot,name,version){
  const supported=Array.isArray(version)?version:[version];
  const dshManifest=realpathSync(join(dshRoot,'node_modules/@deepseek-ai/dsh/package.json'));
  const req=createRequire(dshManifest);
  let selected;
  try{
    let folder=dirname(realpathSync(req.resolve(name)));
    while(folder!==dirname(folder)){
      const manifest=join(folder,'package.json');
      if(existsSync(manifest)&&JSON.parse(readFileSync(manifest,'utf8')).name===name){selected=folder;break;}
      folder=dirname(folder);
    }
  }catch{}
  // Frozen frontend packages may export only assets, without a JS entry point.
  const directManifest=join(dshRoot,'node_modules',name,'package.json');
  if(!selected&&existsSync(directManifest)&&JSON.parse(readFileSync(directManifest,'utf8')).name===name)selected=realpathSync(dirname(directManifest));
  if(!selected){
    const store=join(dshRoot,'node_modules/.pnpm'),prefix=name.replace('/','+')+'@';
    const matches=readdirSync(store).filter(item=>item.startsWith(prefix)).map(item=>join(store,item,'node_modules',name)).filter(folder=>{
      const manifest=join(folder,'package.json');
      return existsSync(manifest)&&supported.includes(JSON.parse(readFileSync(manifest,'utf8')).version);
    });
    if(matches.length!==1)throw new Error('BIO_DEPENDENCY_UNRESOLVED:'+name);
    selected=realpathSync(matches[0]);
  }
  if(!supported.includes(JSON.parse(readFileSync(join(selected,'package.json'),'utf8')).version))throw new Error('BIO_DEPENDENCY_VERSION_UNSUPPORTED:'+name);
  return selected;
}

export async function prepareServer({state,dshRoot,connectionFile,executionTargetFile,port=3081,bindDependencies=true}){
  for(const path of [state,dshRoot,connectionFile,executionTargetFile])if(typeof path!=='string'||!isAbsolute(path))throw new Error('BIO_SERVER_PATH_INVALID');
  const remote=loadRemoteConnection(connectionFile);
  const executionTarget=loadTarget(executionTargetFile);
  if(['python','projectsRoot','releaseRoot'].some(key=>remote[key]!==executionTarget[key]))throw new Error('BIO_EXECUTION_TARGET_CONFIG_MISMATCH');
  state=resolve(state);dshRoot=realpathSync(dshRoot);
  if(state===resolve(releaseRoot)||state.startsWith(resolve(releaseRoot)+'/'))throw new Error('BIO_STATE_INSIDE_RELEASE');
  if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('BIO_SERVER_PORT_INVALID');
  const dsh=JSON.parse(readFileSync(join(dshRoot,'node_modules/@deepseek-ai/dsh/package.json'),'utf8'));
  if(dsh.version!=='0.1.0-rc.6')throw new Error('BIO_DSH_VERSION_UNSUPPORTED');
  const deps={};
  for(const name of ['@deepseek-ai/dsh-web-app','@deepseek-ai/dsh-host-frontend-static','@deepseek-ai/dsh-web-frontend'])deps[name]=packageAt(dshRoot,name,'0.1.0-rc.6');
  // The server already has the exact compiler; its web plugin does not need the AutoMD model SDK.
  deps.typebox=packageAt(dshRoot,'typebox','1.1.38');
  const schemastery=packageAt(dshRoot,'@deepseek-ai/schemastery',['3.18.1','3.18.4']);
  deps['@deepseek-ai/schemastery']=schemastery;
  await loadCompiler(deps.typebox);
  if(bindDependencies)for(const [name,target] of Object.entries(deps))if(name!=='typebox'&&name!=='@deepseek-ai/dsh-web-frontend')link(join(releaseRoot,'node_modules',name),target);
  mkdirSync(state,{recursive:true,mode:0o700});
  if(lstatSync(state).isSymbolicLink()||realpathSync(state)!==state)throw new Error('BIO_STATE_SYMLINK');
  const marker=join(state,'.bio-isolated-state.json');
  if(!existsSync(marker)&&readdirSync(state).length)throw new Error('BIO_STATE_NOT_EMPTY');
  initial(marker,json({schemaVersion:'bio.isolated-state.v1',releaseRoot:resolve(releaseRoot),dshRoot,connectionFile,
    remoteIdentity:remote.identity,executionTargetFile,executionTargetSha256:targetHash(executionTarget),port}));
  const home=join(state,'home'),projects=join(state,'projects'),workspace=join(state,'workspace'),presets=join(state,'presets');
  for(const path of [home,projects,workspace,presets]){
    mkdirSync(path,{recursive:true,mode:0o700});
    if(lstatSync(path).isSymbolicLink()||realpathSync(path)!==path)throw new Error('BIO_STATE_SYMLINK');
  }
  const cards=join(releaseRoot,'web/runtime/ui-cards');
  if(bindDependencies){
    link(join(releaseRoot,'node_modules/bio-web-tool-cards'),cards);
    link(join(home,'node_modules/bio-web-tool-cards'),cards);
  }
  initial(join(presets,'bio/preset.yml'),json({name:'Bio 生物分子模拟（隔离入口）',description:'计算端 OpenMM 流程；通过 Bio 任务面板导入结构、审批和查询结果。'}));
  initial(join(presets,'bio/agent.cordis.yml'),json([{id:'bio-tools',name:pathToFileURL(join(releaseRoot,'web/runtime/server-plugin.mjs')).href,
    config:{mode:'ssh',projectsRoot:projects,connectionFile,executionTargetFile,compilerRoot:deps.typebox,gateMode:'bio-grant-only'}}]));
  const patches=[
    {id:'hmr',disabled:true},
    {insert:[{id:'bio-profile-watch',name:pathToFileURL(join(releaseRoot,'web/runtime/profile-watch.mjs')).href,config:{home}}]},
    {id:'directory-picker',disabled:true},
    {insert:[{id:'bio-directory-browse',name:'@deepseek-ai/dsh-host-directory-picker-browse'},
      {id:'bio-directory-browse-ui',name:'@deepseek-ai/dsh-client-ui-directory-picker-browse'}]},
    {insert:[{id:'bio-web-tool-cards',name:'bio-web-tool-cards'}]},
    {id:'web-runtime',disabled:true},
    {insert:[{id:'bio-original-web',name:pathToFileURL(join(releaseRoot,'web/runtime/frozen-web.mjs')).href,
      config:{distIndex:join(deps['@deepseek-ai/dsh-web-frontend'],'dist/index.html')}}]},
    {id:'agent-presets',disabled:true},
    {insert:[{id:'bio-agent-roster',name:'@deepseek-ai/dsh-agent-presets',config:{default:'bio',roots:[{path:presets,trust:'system'}],includeUserRoot:false}}]},
    {id:'session-title-llm',disabled:true},{id:'llm-retry',disabled:true},
    {id:'agent-default-model',config:{provider:'deepseek-official',model:'deepseek-flash'}},
    {id:'llm-pi-ai',config:{providers:{}}},
    {id:'llm-deepseek',config:{apiKeyEnv:'BIO_SERVER_API_KEY',baseURL:'https://tokens.gpuhome.cc/v1',thinking:'disabled',
      models:[{id:'deepseek-flash',name:'DeepSeek Flash',contextWindow:65536,maxTokens:4096}],maxTokens:4096,defaultContextWindow:65536,
      streamIdleTimeoutMs:60000,retryPolicy:{mode:'normal',maxRetries:0}}},
  ];
  initial(join(home,'profiles/web/package.json'),json({name:'bio-server-isolated-web',private:true,type:'module',dsh:{profile:{bundles:['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}));
  initial(join(home,'profiles/web/cordis.yml'),'[]\n');
  initial(join(home,'profiles/web/cordis.patch.yml'),json(patches));
  const env={PATH:dirname(process.execPath)+':/usr/bin:/bin',HOME:home,DSH_HOME:home,DSH_CWD:workspace,
    DSH_TELEMETRY_DISABLED:'1',DSH_TOOLS_MODE:'native',DSH_PERMISSION_MODE:'workspace-write',LANG:'C.UTF-8'};
  // An operator may supply a staging key in the launching process; nothing reads production credentials.
  if(process.env.BIO_SERVER_API_KEY)env.BIO_SERVER_API_KEY=process.env.BIO_SERVER_API_KEY;
  return {home,projects,workspace,presets,connectionFile,executionTargetFile,executionTarget,remote,port,dependencies:deps,env,
    argv:[join(dshRoot,'node_modules/@deepseek-ai/dsh/lib/bin.js'),'--profile','web','--host','127.0.0.1','--port',String(port)]};
}

async function main(){
  const args=process.argv.slice(2),options={};
  for(let i=0;i<args.length;i++){
    const key=args[i];
    if(key==='--dump-config'){options.dump=true;continue;}
    if(!['--state','--dsh-root','--connection','--execution-target','--port'].includes(key)||options[key]||!args[i+1]||args[i+1].startsWith('--'))throw new Error('BIO_SERVER_ARGUMENT_INVALID');
    options[key]=args[++i];
  }
  const config=await prepareServer({state:options['--state'],dshRoot:options['--dsh-root'],connectionFile:options['--connection'],
    executionTargetFile:options['--execution-target'],port:options['--port']?Number(options['--port']):3081});
  const argv=options.dump?[...config.argv,'--dump-config']:config.argv;
  const child=spawn(process.execPath,argv,{cwd:config.workspace,env:config.env,shell:false,stdio:'inherit'});
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
  child.on('error',()=>{process.stderr.write('BIO_SERVER_START_FAILED\n');process.exitCode=1;});
  child.on('exit',code=>{process.exitCode=code??1;});
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{process.stderr.write(String(error.message)+'\n');process.exitCode=1;});
