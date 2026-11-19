import test from 'node:test';import assert from 'node:assert/strict';
import {defaultAppearance,validateAppearance,representationAppearance} from '../visualization/appearance.mjs';
const presets=await import('../visualization/style-templates.mjs').catch(error=>{if(error.code!=='ERR_MODULE_NOT_FOUND')throw error;return {};});
const drawing=await import('../../visualization/src/appearance.mjs');
test('templates offer unique complete styles for biomolecules and LAMMPS materials',()=>{
  assert.ok(Array.isArray(presets.styleTemplates));assert.equal(new Set(presets.styleTemplates.map(p=>p.id)).size,presets.styleTemplates.length);
  assert.ok(presets.styleTemplates.some(p=>p.group==='materials'));assert.ok(presets.styleTemplates.some(p=>p.group==='bio'));
  for(const p of presets.styleTemplates){const value=presets.resolveStyleTemplate(p.id);assert.ok(['cartoon','sticks','spheres','lines'].includes(value.representation));assert.deepEqual(validateAppearance(value.appearance),value.appearance);assert.equal(presets.matchStyleTemplate(value.representation,value.appearance),p.id);}
  for(const id of ['materials-atoms','materials-metal','materials-interface','materials-ball-stick','materials-wireframe'])assert.notEqual(presets.resolveStyleTemplate(id).representation,'cartoon');
});
test('resolved presets cannot mutate the catalog and reject unknown IDs',()=>{
  const p=presets.resolveStyleTemplate('materials-metal'),before=structuredClone(p);p.appearance.uniformColor=0;
  assert.deepEqual(presets.resolveStyleTemplate('materials-metal'),before);assert.throws(()=>presets.resolveStyleTemplate('http://example.test'),/INVALID_STYLE_TEMPLATE/);
});
test('template matching ignores an unused uniform swatch but notices actual refinements',()=>{
  const p=presets.resolveStyleTemplate('protein-classic');assert.equal(presets.matchStyleTemplate(p.representation,{...p.appearance,uniformColor:0xaa0000}),'protein-classic');
  assert.equal(presets.matchStyleTemplate(p.representation,{...p.appearance,colorScheme:'residue'}),'custom');
  const paper=presets.resolveStyleTemplate('paper-clean');assert.equal(presets.matchStyleTemplate(paper.representation,{...paper.appearance,uniformColor:paper.appearance.uniformColor+1}),'custom');
});
test('protein rainbow uses the sequence theme rather than residual type categories',()=>{
  const value={...defaultAppearance(),colorScheme:'rainbow'};assert.deepEqual(validateAppearance(value),value);assert.equal(representationAppearance(value,'chain-id').color,'sequence-id');
});
function replacementFixture(fail=false,components=[{component:{obj:{data:{}}},type:'spacefill',automaticColor:'element-symbol'}]){
  const previous=[{selector:{ref:'old'}},{selector:{ref:'old-water'}}];
  const applied={old:true,'old-water':true},pending=[],events=[];let commitOptions;
  const plugin={state:{data:{build:()=>({delete:ref=>{pending.push(()=>delete applied[ref]);},commit:async options=>{commitOptions=options;events.push('commit');if(fail)throw new Error('GPU_UPDATE_FAILED');for(const apply of pending)apply();}})}},builders:{structure:{representation:{buildRepresentation:(_builder,_component,props)=>{assert.ok(applied.old&&applied['old-water']);events.push(props.type);pending.push(()=>{applied.new=props;});return {ref:'new'};}}}}};
  return {plugin,previous,components,applied,events,options:()=>commitOptions};
}
test('geometry template replacement builds and removes representations in one rollback-capable commit',async()=>{
  assert.equal(typeof drawing.replaceRepresentations,'function');const f=replacementFixture();const next=await drawing.replaceRepresentations(f.plugin,f.previous,f.components,defaultAppearance());
  assert.deepEqual(f.events,['spacefill','commit']);assert.deepEqual(f.options(),{revertOnError:true});assert.deepEqual(Object.keys(f.applied),['new']);assert.equal(next[0].selector.ref,'new');assert.equal(next[0].component,f.components[0].component);
});
test('failed geometry template replacement retains the previous scene',async()=>{
  assert.equal(typeof drawing.replaceRepresentations,'function');const f=replacementFixture(true);await assert.rejects(drawing.replaceRepresentations(f.plugin,f.previous,f.components,defaultAppearance()),/GPU_UPDATE_FAILED/);
  assert.deepEqual(f.applied,{old:true,'old-water':true});assert.deepEqual(f.options(),{revertOnError:true});
});
test('water geometry reaches the builder together with the requested appearance',async()=>{
  const f=replacementFixture(false,[{component:{obj:{data:{elementCount:3}}},type:'ball-and-stick',automaticColor:'element-symbol',typeParams:{sizeFactor:0.3,sizeAspectRatio:0.5,ignoreHydrogens:false}}]);
  await drawing.replaceRepresentations(f.plugin,f.previous,f.components,{...defaultAppearance(),material:'glossy'});
  assert.equal(f.applied.new.typeParams.sizeFactor,0.3);
  assert.equal(f.applied.new.typeParams.sizeAspectRatio,0.5);
  assert.equal(f.applied.new.typeParams.ignoreHydrogens,false);
  assert.equal(f.applied.new.typeParams.material.roughness,0.2);
  assert.equal(f.applied.new.color,'element-symbol');
});
