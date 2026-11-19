import z from '@deepseek-ai/schemastery';
import {createRequire} from 'node:module';
import {dirname,join} from 'node:path';
import {readFileSync,existsSync} from 'node:fs';
import {serverConfig} from '../../bio-agent/web/runtime/server-config.mjs';
import {apply as applyRemote} from '../../bio-agent/web/runtime/server-apply.mjs';

export {name,toolNames} from '../../bio-agent/web/runtime/bio-runtime.mjs';
export const inject=['tools','systemPrompt','agents','subprocess'];
export const Config=serverConfig(z,{compilerRequired:false});

export function compilerRoot(){
  const require=createRequire(import.meta.url);
  let folder=dirname(require.resolve('typebox/compile'));
  while(folder!==dirname(folder)){
    const manifest=join(folder,'package.json');
    if(existsSync(manifest)&&JSON.parse(readFileSync(manifest,'utf8')).name==='typebox')return folder;
    folder=dirname(folder);
  }
  throw new Error('BIO_COMPILER_DEPENDENCY_MISSING');
}

export async function apply(ctx,config){
  return applyRemote(ctx,{...config,compilerRoot:config.compilerRoot||compilerRoot()});
}
