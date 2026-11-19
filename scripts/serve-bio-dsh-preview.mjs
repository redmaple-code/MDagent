/** Native DSH engineering experience. No production configuration is modified. */
import {spawn,spawnSync} from 'node:child_process';
import {existsSync,lstatSync,mkdirSync,readFileSync,realpathSync,symlinkSync,writeFileSync,createWriteStream} from 'node:fs';
import {resolve,join,dirname,isAbsolute} from 'node:path';import {fileURLToPath,pathToFileURL} from 'node:url';
import YAML from 'yaml';
import {prepareServer} from '../bio-agent/deploy/server-launch.mjs';
import {scientificEnv} from '../bio-agent/agent/bridge-client.mjs';
const repo=fileURLToPath(new URL('../',import.meta.url));
const save=(p,value)=>writeFileSync(p,JSON.stringify(value,null,2)+'\n',{mode:0o600});
export async function prepareNativeProfile({stateRoot,repoRoot=repo,python='/opt/anaconda3/envs/modeling/bin/python',port=3081,connectorPort=18773,modelHome}){
  if(!isAbsolute(stateRoot||'')||resolve(stateRoot)!==stateRoot||realpathSync(dirname(stateRoot))!==dirname(stateRoot)||stateRoot===repoRoot||stateRoot.startsWith(join(repoRoot,'bio-agent')+'/'))throw new Error('NATIVE_STATE_BOUNDARY');
  if(existsSync(stateRoot))throw new Error('NATIVE_STATE_NOT_NEW');
  if(!Number.isInteger(connectorPort)||connectorPort<1024||connectorPort>65535||connectorPort===port)throw new Error('NATIVE_PORT_INVALID');
  mkdirSync(stateRoot,{mode:0o700});const bio=join(repoRoot,'bio-agent'),compute=join(stateRoot,'compute');mkdirSync(compute,{mode:0o700});
  const result=spawnSync(python,['-I','-B','-c','import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from bioagent.execution_target import local_target;print(json.dumps(local_target(Path(sys.argv[2]),Path(sys.executable))))',join(bio,'src'),join(compute,'bootstrap')],{encoding:'utf8',env:scientificEnv(python)});
  if(result.status!==0)throw new Error('NATIVE_TARGET_FAILED');const target=JSON.parse(result.stdout);
  const targetFile=join(stateRoot,'target.json'),connectionFile=join(stateRoot,'connection.json'),configFile=join(stateRoot,'operator-ssh-config'),knownHosts=join(stateRoot,'operator-known-hosts');
  const blob=Buffer.alloc(51);blob.writeUInt32BE(11);blob.write('ssh-ed25519',4);blob.writeUInt32BE(32,15);
  writeFileSync(configFile,'Host engineering-stdio\n HostName compute.invalid\n',{mode:0o600});writeFileSync(knownHosts,target.targetId+' ssh-ed25519 '+blob.toString('base64')+'\n',{mode:0o600});
  const connection={schemaVersion:'bio.ssh-connection.v1',hostAlias:'engineering-stdio',configFile,knownHosts,projectsRoot:compute,releaseRoot:bio,python};save(targetFile,target);save(connectionFile,connection);
  const prepared=await prepareServer({state:join(stateRoot,'dsh'),dshRoot:repoRoot,connectionFile,executionTargetFile:targetFile,port,bindDependencies:false});
  if(process.env.BIO_PREVIEW_PYMOL_PYTHON)prepared.env.BIO_PREVIEW_PYMOL_PYTHON=process.env.BIO_PREVIEW_PYMOL_PYTHON;
  const link=join(prepared.home,'node_modules/bio-web-tool-cards');mkdirSync(dirname(link),{recursive:true,mode:0o700});symlinkSync(join(bio,'web/runtime/ui-cards'),link,'dir');
  save(join(prepared.presets,'bio/preset.yml'),YAML.parse(readFileSync(join(repoRoot,'presets/bio/preset.yml'),'utf8')));
  save(join(prepared.presets,'bio/agent.cordis.yml'),[{id:'bio-native-preview',name:pathToFileURL(join(repoRoot,'scripts/bio-dsh-preview-plugin.mjs')).href,config:{stateRoot,repoRoot,python,compilerRoot:prepared.dependencies.typebox,port,connectorPort,engineeringOnly:true}}]);
  save(join(stateRoot,'.native-preview.json'),{schemaVersion:'bio.native-engineering.v1',engineeringOnly:true,repoRoot,port,connectorPort});
  const patchFile=join(prepared.home,'profiles/web/cordis.patch.yml'),patches=JSON.parse(readFileSync(patchFile));
  patches.push({insert:[{id:'native-empty-sessions',name:pathToFileURL(join(repoRoot,'scripts/bio-dsh-preview-plugin.mjs')).href,config:{stateRoot,repoRoot,port,connectorPort,engineeringOnly:true,bootstrap:true}}]});save(patchFile,patches);
  if(modelHome){
    const path=join(modelHome,'.credentials.yaml'),stat=lstatSync(path);
    if(stat.isSymbolicLink()||!stat.isFile()||stat.uid!==process.getuid()||(stat.mode&0o077))throw new Error('NATIVE_MODEL_HOME_UNSAFE');
    const settings=YAML.parse(readFileSync(join(modelHome,'settings.yaml'),'utf8'));
    if(settings['llm-deepseek']?.baseURL!=='https://tokens.gpuhome.cc/v1')throw new Error('NATIVE_MODEL_PROVIDER_MISMATCH');
    const value=YAML.parse(readFileSync(path,'utf8')).BIO_WEB_TEST_API_KEY;
    if(typeof value!=='string'||!value)throw new Error('NATIVE_MODEL_KEY_MISSING');prepared.env.BIO_SERVER_API_KEY=value;
  }
  return {...prepared,projectsRoot:compute,engineeringOnly:true,viewEnabled:false,stateRoot};
}
export async function startNativePreview(options){
  const profile=await prepareNativeProfile(options),log=createWriteStream(join(profile.stateRoot,'native-dsh.log'),{flags:'wx',mode:0o600});
  const child=spawn(process.execPath,profile.argv,{cwd:profile.workspace,env:profile.env,shell:false,stdio:['ignore','pipe','pipe']});child.stdout.pipe(log);child.stderr.pipe(log);
  const url='http://127.0.0.1:'+profile.port;let exited=false;child.once('exit',()=>{exited=true;log.end();});child.once('error',()=>{exited=true;});
  const close=async()=>{if(!exited){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,3000))]);if(!exited)child.kill('SIGKILL');}};
  try{let ready=false;for(let i=0;i<200&&!exited;i++){try{const response=await fetch(url,{signal:AbortSignal.timeout(1000)});if(response.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}if(!ready)throw new Error('NATIVE_DSH_START_FAILED');}
  catch(error){await close();throw error;}
  save(join(profile.stateRoot,'gateway.json'),{url,pid:child.pid,supervisorPid:process.pid,connectorPort:profile.connectorPort,engineeringOnly:true,transport:'LOCAL_PYTHON_STDIO',build:JSON.parse(readFileSync(join(options.repoRoot||repo,'bio-agent/web/visualization/dist/asset-manifest.json'))).entry});
  return {url,close};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2),values={};for(let i=0;i<args.length;i++){if(!['--state','--port','--connector-port','--model-home'].includes(args[i])||!args[i+1])throw new Error('NATIVE_ARGUMENT_INVALID');values[args[i]]=args[++i];}
  startNativePreview({stateRoot:values['--state'],port:Number(values['--port']||3081),connectorPort:Number(values['--connector-port']||18773),modelHome:values['--model-home']}).then(service=>{process.stdout.write(JSON.stringify({url:service.url,engineeringOnly:true})+'\n');for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>void service.close());}).catch(error=>{process.stderr.write(error.message+'\n');process.exitCode=1;});
}
