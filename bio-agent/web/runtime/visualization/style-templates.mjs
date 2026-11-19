import {defaultAppearance,validateAppearance} from './appearance.mjs';
const preset=(id,label,group,representation,colorScheme,material,uniformColor=0x5f9fd8)=>Object.freeze({id,label,group,representation,appearance:Object.freeze({...defaultAppearance(),colorScheme,material,uniformColor})});
export const styleTemplates=Object.freeze([
  preset('protein-classic','经典蛋白','bio','cartoon','auto','matte'),
  preset('protein-rainbow','蛋白彩虹','bio','cartoon','rainbow','glossy'),
  preset('molecular-ball-stick','球棍模型','bio','sticks','element','glossy'),
  preset('water-ball-stick','水分子','bio','sticks','auto','matte'),
  preset('molecular-spacefill','空间填充','bio','spheres','element','glossy'),
  preset('paper-clean','论文简洁','bio','cartoon','uniform','matte',0x617d9a),
  preset('molecular-sculpture','金属雕塑','bio','spheres','uniform','metal',0x809caf),
  preset('materials-atoms','元素原子','materials','spheres','element','matte'),
  preset('materials-metal','金属晶体','materials','spheres','uniform','metal',0xb0becf),
  preset('materials-interface','界面分组','materials','spheres','chain','glossy'),
  preset('materials-ball-stick','材料球棍','materials','sticks','element','matte'),
  preset('materials-wireframe','轻量线框','materials','lines','uniform','matte',0x729fc3)
]);
export function resolveStyleTemplate(id){
  const value=styleTemplates.find(p=>p.id===id);if(!value)throw new Error('INVALID_STYLE_TEMPLATE');
  return {representation:value.representation,appearance:{...value.appearance}};
}
export function matchStyleTemplate(representation,value){
  const appearance=validateAppearance(value);
  return styleTemplates.find(p=>p.representation===representation&&p.appearance.colorScheme===appearance.colorScheme&&p.appearance.material===appearance.material&&(appearance.colorScheme!=='uniform'||p.appearance.uniformColor===appearance.uniformColor))?.id??'custom';
}
