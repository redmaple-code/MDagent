import {validateAppearance,representationAppearance} from '../../runtime/visualization/appearance.mjs';
export async function replaceRepresentations(plugin,previous,components,value){
  const appearance=validateAppearance(value),update=plugin.state.data.build(),next=[];
  for(const entry of components){
    const props=representationAppearance(appearance,entry.automaticColor);
    const selector=plugin.builders.structure.representation.buildRepresentation(update,entry.component,{type:entry.type,...props,typeParams:{...entry.typeParams,...props.typeParams}});
    if(!selector)throw new Error('VIEWPORT_NOT_READY');next.push({...entry,selector});
  }
  for(const entry of previous)update.delete(entry.selector.ref);
  await update.commit({revertOnError:true});return next;
}
export async function applyRepresentationAppearance(plugin,entries,value,colorThemeParams){
  const appearance=validateAppearance(value),update=plugin.state.data.build();
  for(const entry of entries){
    const current=entry.selector.cell?.params?.values;if(!current)throw new Error('VIEWPORT_NOT_READY');
    const props=representationAppearance(appearance,entry.automaticColor);
    const colorTheme=colorThemeParams(plugin,entry.component.obj.data,current.type.name,props.color,props.colorParams);
    update.to(entry.selector).update({...current,colorTheme,type:{...current.type,params:{...current.type.params,material:props.typeParams.material}}});
  }
  await update.commit({revertOnError:true});return appearance;
}
