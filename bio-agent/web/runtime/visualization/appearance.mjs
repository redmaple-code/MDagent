const schemes=new Set(['auto','element','chain','residue','rainbow','uniform']);
const materials=Object.freeze({matte:Object.freeze({metalness:0,roughness:1,bumpiness:0}),glossy:Object.freeze({metalness:0,roughness:0.2,bumpiness:0}),metal:Object.freeze({metalness:1,roughness:0.6,bumpiness:0})});
export function defaultAppearance(){return {colorScheme:'auto',uniformColor:0x5f9fd8,material:'matte'};}
export function validateAppearance(value,code='INVALID_VIEW_APPEARANCE'){
  if(!value||Array.isArray(value)||Object.keys(value).sort().join('|')!=='colorScheme|material|uniformColor'||!schemes.has(value.colorScheme)||!Object.hasOwn(materials,value.material)||!Number.isSafeInteger(value.uniformColor)||value.uniformColor<0||value.uniformColor>0xffffff)throw new Error(code);
  return {...value};
}
export function representationAppearance(value,automaticColor){
  const appearance=validateAppearance(value),color={auto:automaticColor,element:'element-symbol',chain:'chain-id',residue:'residue-name',rainbow:'sequence-id',uniform:'uniform'}[appearance.colorScheme];
  const colorParams=appearance.colorScheme==='uniform'?{value:appearance.uniformColor}:appearance.colorScheme==='element'?{carbonColor:{name:'element-symbol',params:{}}}:{};
  return {color,colorParams,typeParams:{material:{...materials[appearance.material]}}};
}
