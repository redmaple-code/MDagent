// Generate an additive candidate; never change the original tenant profile or its providers.
import {mkdirSync,existsSync,lstatSync,realpathSync,readFileSync,writeFileSync} from 'node:fs';
import {dirname,isAbsolute,join,resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {loadTarget,targetHash} from '../agent/execution-target.mjs';

export function tenantCandidate(config){
  const allowed=['tenantId','output','executionTargetFile','compilerRoot','projectsRoot','platformGateModule','mode',
    'gatewayPython','gatewayReleaseRoot','gatewayConnectionFile','rosterPluginId','rosterConfig','agentPlugin'];
  if(!config||Object.keys(config).some(key=>!allowed.includes(key)))throw new Error('BIO_TENANT_CONFIG_INVALID');
  if(config.agentPlugin!==undefined&&config.agentPlugin!=='dsh-bio-agent')throw new Error('BIO_TENANT_CONFIG_INVALID');
  const target=loadTarget(config.executionTargetFile);
  if(config.tenantId!==target.tenantId||!['node-world','node-ssh'].includes(config.mode)||!config.rosterConfig
    ||Object.keys(config.rosterConfig).some(key=>!['default','roots','includeUserRoot'].includes(key))
    ||!Array.isArray(config.rosterConfig.roots)||config.rosterConfig.roots.some(root=>Object.keys(root).some(key=>!['path','trust'].includes(key))
      ||typeof root.path!=='string'||!isAbsolute(root.path)||!['system','user'].includes(root.trust)))throw new Error('BIO_TENANT_CONFIG_INVALID');
  if(config.rosterPluginId!==undefined&&!/^[a-z0-9][a-z0-9_.-]{0,127}$/.test(config.rosterPluginId))throw new Error('BIO_TENANT_CONFIG_INVALID');
  for(const key of ['output','compilerRoot','projectsRoot','platformGateModule'])if(typeof config[key]!=='string'||!isAbsolute(config[key]))throw new Error('BIO_TENANT_PATH_INVALID');
  if(config.mode==='node-ssh')for(const key of ['gatewayPython','gatewayReleaseRoot','gatewayConnectionFile'])if(!isAbsolute(config[key]||''))throw new Error('BIO_TENANT_PATH_INVALID');
  const release=fileURLToPath(new URL('../',import.meta.url)),output=resolve(config.output),presets=join(output,'presets');
  if(output===resolve(release)||output.startsWith(resolve(release)+'/'))throw new Error('BIO_TENANT_OUTPUT_INSIDE_RELEASE');
  const pluginConfig={projectsRoot:config.projectsRoot,compilerRoot:config.compilerRoot,mode:config.mode,nodeId:target.nodeId,
    executionTargetFile:config.executionTargetFile,gateMode:'platform',platformGateModule:config.platformGateModule,
    ...(config.mode==='node-ssh'?Object.fromEntries(['gatewayPython','gatewayReleaseRoot','gatewayConnectionFile'].map(key=>[key,config[key]])):{})};
  const roster={...config.rosterConfig,roots:[...config.rosterConfig.roots,{path:presets,trust:'system'}]};
  const files={
    'presets/bio/preset.yml':{name:'Bio 生物分子模拟',description:'在账号已绑定的计算目标完成结构构建、拓扑、OpenMM 及分析。'},
    'presets/bio/agent.cordis.yml':[{id:'bio-tools',name:config.agentPlugin||pathToFileURL(join(release,'web/runtime/server-plugin.mjs')).href,config:pluginConfig}],
    'profile.patch.json':[{id:config.rosterPluginId||'agent-presets',config:roster},{insert:[{id:'bio-web-tool-cards',name:'bio-web-tool-cards'}]}],
    'candidate.json':{schemaVersion:'bio.tenant-profile.v1',tenantId:target.tenantId,nodeId:target.nodeId,
      executionTargetSha256:targetHash(target),status:'GENERATED_NOT_APPLIED',requiresAuthenticatedTenantGateway:true,
      originalProvidersChanged:false,scientificExecutionAuthorized:false}
  };
  return {output,files};
}

export function writeTenantCandidate(config){
  const candidate=tenantCandidate(config),root=candidate.output;
  // All conflicts are checked before any file is written. A fresh candidate directory is required.
  if(existsSync(root))throw new Error('BIO_TENANT_OUTPUT_CONFLICT');
  let parent=dirname(root);while(!existsSync(parent))parent=dirname(parent);
  if(realpathSync(parent)!==parent||!lstatSync(parent).isDirectory())throw new Error('BIO_TENANT_OUTPUT_SYMLINK');
  mkdirSync(root,{recursive:true,mode:0o700});
  if(realpathSync(root)!==root)throw new Error('BIO_TENANT_OUTPUT_SYMLINK');
  for(const [name,value] of Object.entries(candidate.files)){
    const path=join(root,name);mkdirSync(dirname(path),{recursive:true,mode:0o700});
    if(realpathSync(dirname(path))!==dirname(path))throw new Error('BIO_TENANT_OUTPUT_SYMLINK');
    writeFileSync(path,JSON.stringify(value,null,2)+'\n',{flag:'wx',mode:0o600});
  }
  return {output:root,files:Object.keys(candidate.files),status:'GENERATED_NOT_APPLIED'};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{
    if(process.argv.length!==4||process.argv[2]!=='--config')throw new Error('BIO_TENANT_ARGUMENT_INVALID');
    console.log(JSON.stringify(writeTenantCandidate(JSON.parse(readFileSync(process.argv[3],'utf8')))));
  }catch(error){process.stderr.write(/^[A-Z][A-Z0-9_]+$/.test(error.message)?error.message+'\n':'BIO_TENANT_CANDIDATE_FAILED\n');process.exitCode=1;}
}
