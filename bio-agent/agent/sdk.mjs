import {createHash} from 'node:crypto';
import {readFileSync,readdirSync,statSync,existsSync,realpathSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join,dirname,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const sha=data=>createHash('sha256').update(data).digest('hex');
export function createRedactor(secrets=[]){
  const clean=text=>{
    for(const secret of secrets.filter(x=>typeof x==='string'&&x.length))text=text.split(secret).join('[REDACTED]');
    return text.replace(/[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g,c=>`\\u${c.charCodeAt(0).toString(16).padStart(4,'0')}`);
  };
  return value=>typeof value==='string'?clean(value):JSON.parse(JSON.stringify(value,(_key,item)=>typeof item==='string'?clean(item):item));
}

export async function loadSdk(root){
  root=realpathSync(resolve(root));
  const packages={},files=[];
  for(const [key,dir,name] of [['agent','agent','@automd/agent-runtime'],['models','ai','@automd/model-runtime']]){
    const base=join(root,'packages',dir);const manifest=JSON.parse(readFileSync(join(base,'package.json'),'utf8'));
    if(manifest.name!==name||manifest.version!=='0.80.10')throw new Error('SDK_VERSION_UNSUPPORTED');
    const entry=join(base,'dist/index.js');
    packages[key]={name,version:manifest.version,entry,entrySha256:sha(readFileSync(entry))};
    const walk=folder=>{for(const name of readdirSync(folder).sort()){const path=join(folder,name);if(statSync(path).isDirectory())walk(path);else if(path.endsWith('.js'))files.push({path,sha256:sha(readFileSync(path))});}};
    walk(join(base,'dist'));
  }
  const require=createRequire(join(root,'packages/agent/package.json'));
  const compilePath=require.resolve('typebox/compile');
  let folder=dirname(compilePath),typebox;
  while(folder!==dirname(folder)){
    const path=join(folder,'package.json');
    if(existsSync(path)){const pkg=JSON.parse(readFileSync(path,'utf8'));if(pkg.name==='typebox'){typebox={version:pkg.version,entry:compilePath,sha256:sha(readFileSync(compilePath))};break;}}
    folder=dirname(folder);
  }
  if(!typebox)throw new Error('SDK_DEPENDENCY_MISSING');
  const [agent,models,compile]=await Promise.all([import(pathToFileURL(packages.agent.entry)),import(pathToFileURL(packages.models.entry)),import(pathToFileURL(compilePath))]);
  if(!agent.Agent||!models.createModels||!compile.Compile||!models.AssistantMessageEventStream)throw new Error('SDK_EXPORT_MISSING');
  const providerFactories={};
  for(const name of ['openai','anthropic','deepseek','openrouter']){
    const module=await import(pathToFileURL(join(root,`packages/ai/dist/providers/${name}.js`)));
    providerFactories[name]=module[`${name}Provider`];
  }
  const lock=join(root,'package-lock.json');
  const identity={root,packages,typebox,nodeVersion:process.version,runtimeSha256:sha(JSON.stringify(files)),lock:{path:lock,sha256:sha(readFileSync(lock))}};
  return {Agent:agent.Agent,createModels:models.createModels,AssistantMessageEventStream:models.AssistantMessageEventStream,Compile:compile.Compile,providerFactories,identity,inventory:files};
}

export function createBioModels(sdk,{providerId,modelId,env}){
  const names={openai:'OPENAI_API_KEY',anthropic:'ANTHROPIC_API_KEY',deepseek:'DEEPSEEK_API_KEY',openrouter:'OPENROUTER_API_KEY'};
  if(!Object.hasOwn(names,providerId))throw new Error('MODEL_AUTH_UNSUPPORTED');
  const key=env?.[names[providerId]];
  if(typeof key!=='string'||!key.trim()||typeof modelId!=='string'||!modelId)throw new Error('MODEL_CONFIG_REQUIRED');
  const models=sdk.createModels({authContext:{env:async name=>name===names[providerId]?key:undefined,fileExists:async()=>false}});
  models.setProvider(sdk.providerFactories[providerId]());
  const model=models.getModel(providerId,modelId);
  if(!model)throw new Error('MODEL_CONFIG_REQUIRED');
  return {models,model,identity:{providerId,modelId,baseUrl:model.baseUrl,sdkSha256:sdk.identity.runtimeSha256},redact:createRedactor([key])};
}
