import {watch,lstatSync,realpathSync} from 'node:fs';
import {dirname,resolve,relative,basename} from 'node:path';
import z from '@deepseek-ai/schemastery';
export const name='local-profile-watch';
export const Config=z.object({home:z.string().required()});

export function apply(ctx,config){
  const home=realpathSync(config.home);
  // rc.6's launcher requires this seam even when Web disables source HMR.
  // Only the two owned profile patch files are watched; no source module reload is enabled.
  ctx.provide('hmr',{registerConfig(filename,onChange){
    const path=resolve(filename);const rel=relative(home,path);
    if(!['cordis.patch.yml','profiles/web/cordis.patch.yml'].includes(rel)||lstatSync(dirname(path)).isSymbolicLink())throw new Error('PROFILE_WATCH_OUTSIDE_HOME');
    let closed=false,timer,tail=Promise.resolve();
    const watcher=watch(dirname(path),(_event,file)=>{
      if(closed||String(file)!==basename(path))return;
      clearTimeout(timer);timer=setTimeout(()=>{tail=tail.then(()=>closed?undefined:onChange()).catch(()=>{ctx.logger?.warn('Local profile update rejected.');});},100);
    });
    watcher.on('error',()=>{ctx.logger?.warn('Local profile watcher failed.');});
    return ctx.effect(()=>async()=>{closed=true;clearTimeout(timer);watcher.close();await tail;});
  }});
}
