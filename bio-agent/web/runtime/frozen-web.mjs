import z from '@deepseek-ai/schemastery';
import * as FrontendStatic from '@deepseek-ai/dsh-host-frontend-static';
import {resolveLanTrust} from '@deepseek-ai/dsh-web-app';
export const name='original-mdexp-web';
export const inject=['webServer'];
export const Config=z.object({distIndex:z.string().required()});
export function apply(ctx,config){
  if(ctx.webServer.host!=='127.0.0.1')throw new Error('BIO_WEB_LOOPBACK_REQUIRED');
  ctx.provide('webRuntime',resolveLanTrust(ctx.webServer.host,[]));
  ctx.plugin(FrontendStatic,{distIndex:config.distIndex});
  ctx.get('loader')?.await().then(()=>console.log(`Bio web: http://127.0.0.1:${ctx.webServer.port}`),()=>{});
}
