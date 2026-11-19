import z from '@deepseek-ai/schemastery';
import {loadSdk} from '../../agent/sdk.mjs';
import {installBio} from './bio-runtime.mjs';
export {name,inject,toolNames,SessionProjects,projectForSession,installBio} from './bio-runtime.mjs';
export const Config=z.object({projectsRoot:z.string().required(),python:z.string().required(),sdkRoot:z.string().required()});
export async function apply(ctx,config){
  const sdk=await loadSdk(config.sdkRoot);
  return installBio(ctx,config,sdk.Compile);
}
