import {PluginContext} from 'molstar/lib/mol-plugin/context.js';
import {DefaultPluginSpec} from 'molstar/lib/mol-plugin/spec.js';
import {StateTransforms} from 'molstar/lib/mol-plugin-state/transforms.js';
import {StructureElement} from 'molstar/lib/mol-model/structure.js';
import {OrderedSet} from 'molstar/lib/mol-data/int.js';
import {Color} from 'molstar/lib/mol-util/color/index.js';
import {previewLimits as L} from '../../runtime/visualization/contracts.mjs';
import {LinesBuilder} from 'molstar/lib/mol-geo/geometry/lines/lines-builder.js';
import {Lines} from 'molstar/lib/mol-geo/geometry/lines/lines.js';
import {Shape} from 'molstar/lib/mol-model/shape.js';
import {ShapeRepresentation} from 'molstar/lib/mol-repr/shape/representation.js';
import {bindCanvasTheme} from './theme.mjs';
import {createStructureColorThemeParams} from 'molstar/lib/mol-plugin-state/helpers/structure-representation-params.js';
import {defaultAppearance,validateAppearance} from '../../runtime/visualization/appearance.mjs';
import {applyRepresentationAppearance,replaceRepresentations} from './appearance.mjs';

export async function createMolstarRenderer(container,onObservation=()=>{}){
  const spec=DefaultPluginSpec();spec.actions=[];spec.animations=[];
  // Only local interaction/camera behaviors; no remote custom property providers.
  spec.behaviors=spec.behaviors.slice(0,7);
  const plugin=new PluginContext(spec);await plugin.init();
  if(!await plugin.mountAsync(container))throw new Error('PREVIEW_WEBGL_UNAVAILABLE');
  plugin.canvas3d.setProps({camera:{manualReset:true},cameraResetDurationMs:0});
  const resize=new ResizeObserver(()=>plugin.handleResize());resize.observe(container);
  let topologyModel,model,structure,raw,coordinates,trajectory,manifest,atomMap,bytesIdentity,representation='cartoon',selection=[],representations=[],boxRepresentation,ownSelection=false,selectionTooLarge=false;
  let appearance=defaultAppearance();
  const observation={ready:false,sourceSha256:null,sourceFrameIndex:null,segmentFrameIndex:null,timePs:null,cameraRevision:0,decodedWindowBytes:0};
  const stopTheme=bindCanvasTheme(container,backgroundColor=>{plugin.canvas3d.setProps({renderer:{backgroundColor:Color(backgroundColor)}});observation.backgroundColor=backgroundColor;onObservation({...observation});});
  const camera=plugin.canvas3d.camera.stateChanged.subscribe(()=>{observation.cameraRevision++;onObservation({...observation});});
  async function represent(){
    if(!structure)return;
    const components=[],add=(component,type,automaticColor,isWater=false)=>components.push({component,type,automaticColor,typeParams:isWater&&type==='ball-and-stick'?{sizeFactor:0.3,sizeAspectRatio:0.5,ignoreHydrogens:false}:undefined});let counts;
    const water=['cartoon','sticks'].includes(representation)?await plugin.builders.structure.tryCreateComponentStatic(structure,'water'):null;
    if(representation==='cartoon'){
      const polymer=await plugin.builders.structure.tryCreateComponentStatic(structure,'polymer');
      const ligand=await plugin.builders.structure.tryCreateComponentStatic(structure,'ligand');
      counts={polymer:polymer?.obj?.data?.elementCount||0,ligand:ligand?.obj?.data?.elementCount||0};
      if(polymer)add(polymer,'cartoon','chain-id');
      if(ligand)add(ligand,'ball-and-stick','element-symbol');
      if(water)add(water,'ball-and-stick','element-symbol',true);
      for(const kind of ['ion','lipid','branched']){const component=await plugin.builders.structure.tryCreateComponentStatic(structure,kind);if(component)add(component,'ball-and-stick','element-symbol');}
    }else add(structure,{sticks:'ball-and-stick',spheres:'spacefill',lines:'line'}[representation],'element-symbol',water?.obj?.data?.elementCount===structure.obj?.data?.elementCount);
    representations=await replaceRepresentations(plugin,representations,components,appearance);if(counts)observation.components=counts;
  }
  function select(indices){
    if(!structure)throw new Error('PREVIEW_BOX_ONLY');const lookup=new Set(indices.map(i=>atomMap.indexOf(i)));
    if(lookup.has(-1))throw new Error('PREVIEW_ATOM_NOT_DISPLAYED');
    selection=[...indices];
    ownSelection=true;plugin.managers.interactivity.lociSelects.deselectAll();const data=structure?.obj?.data;
    if(data){const elements=[];for(const unit of data.units){const local=[];for(let i=0;i<unit.elements.length;i++)if(lookup.has(unit.model.atomicHierarchy.atomSourceIndex.value(unit.elements[i])))local.push(i);if(local.length)elements.push({unit,indices:OrderedSet.ofSortedArray(local)});}
      plugin.managers.interactivity.lociSelects.selectOnly({loci:StructureElement.Loci(data,elements)});}
    ownSelection=false;selectionTooLarge=false;onObservation({...observation,selection});
  }
  const picks=plugin.managers.structure.selection.events.changed.subscribe(()=>{
    if(ownSelection||!structure?.obj?.data||!atomMap)return;
    const loci=plugin.managers.structure.selection.getLoci(structure.obj.data);const indices=new Set();
    for(const e of loci.elements||[])for(let i=0;i<OrderedSet.size(e.indices);i++){const atom=e.unit.elements[OrderedSet.getAt(e.indices,i)];indices.add(atomMap[e.unit.model.atomicHierarchy.atomSourceIndex.value(atom)]);}
    selectionTooLarge=indices.size>512;if(selectionTooLarge)return;
    const next=[...indices].sort((a,b)=>a-b);if(JSON.stringify(next)!==JSON.stringify(selection)){selection=next;observation.selectionRevision=(observation.selectionRevision||0)+1;onObservation({...observation,selection});}
  });
  return {
    async openBox(description){
      structure=null;selection=[];atomMap=[];manifest=null;selectionTooLarge=false;await plugin.clear();representations=[];
      if(boxRepresentation){plugin.canvas3d.remove(boxRepresentation);boxRepresentation.destroy();}
      const vectors=description.boxVectorsAngstrom;
      if(!vectors||vectors.length!==3||vectors.some(v=>v.length!==3||v.some(n=>!Number.isFinite(n))))throw new Error('PREVIEW_PERIODIC_BOX_REQUIRED');
      const vertices=Array.from({length:8},(_,i)=>[0,1,2].map(k=>vectors.reduce((sum,v,j)=>sum+((i>>j)&1)*v[k],0)));
      const lines=LinesBuilder.create(12,12);for(let i=0;i<8;i++)for(let j=0;j<3;j++)if(!(i&(1<<j)))lines.addVec(vertices[i],vertices[i|(1<<j)],0);
      const shape=Shape.create('Source periodic box',description,lines.getLines(),()=>Color(0x80c1df),()=>1,()=> 'Periodic cell');
      boxRepresentation=ShapeRepresentation((_ctx,value)=>value,Lines.Utils);await boxRepresentation.createOrUpdate({sizeFactor:2},shape).run();plugin.canvas3d.add(boxRepresentation);plugin.managers.camera.reset();
      Object.assign(observation,{ready:true,sourceSha256:description.source.sourceSha256,sourceFrameIndex:0,segmentFrameIndex:0,timePs:description.timeStartPs,boxOnly:true,decodedWindowBytes:0,components:{polymer:0,ligand:0}});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));onObservation({...observation});
    },
    async open(value,topologyBytes,mapBytes){
      if(boxRepresentation){plugin.canvas3d.remove(boxRepresentation);boxRepresentation.destroy();boxRepresentation=null;}
      structure=null;selection=[];selectionTooLarge=false;await plugin.clear();representations=[];manifest=value;bytesIdentity=null;raw=null;model=null;trajectory=null;
      if(mapBytes.byteLength!==value.atomCount*4)throw new Error('PREVIEW_ATOM_MAPPING_INVALID');
      atomMap=Array.from(new Uint32Array(mapBytes.buffer.slice(mapBytes.byteOffset,mapBytes.byteOffset+mapBytes.byteLength)));
      const data=await plugin.builders.data.rawData({data:new TextDecoder().decode(topologyBytes),label:'Source-bound topology'});
      const pdb=await plugin.builders.structure.parseTrajectory(data,'pdb');topologyModel=await plugin.builders.structure.createModel(pdb);
      if(topologyModel.obj.data.atomicHierarchy.atoms._rowCount!==value.atomCount)throw new Error('PREVIEW_ATOM_MAPPING_INVALID');
      observation.ready=false;observation.sourceSha256=value.source.sourceSha256;observation.sourceFrameIndex=null;observation.timePs=null;observation.boxOnly=false;observation.coordinateMode=value.processing?.mode??'raw';
    },
    async setFrame(bytes,localIndex,mapping){
      if(!raw){
        raw=await plugin.builders.data.rawData({data:bytes,label:'Verified preview XTC'});
        coordinates=await plugin.state.data.build().to(raw).apply(StateTransforms.Model.CoordinatesFromXtc).commit();
        const data=coordinates.obj.data;const size=data.frames.reduce((sum,f)=>sum+f.elementCount*12,0);
        if(data.frames.length>64||size>L.windowBytes||data.frames.some(f=>f.elementCount!==manifest.atomCount)||data.frames.length!==mapping.frameCount)throw new Error('PREVIEW_MEMORY_BUDGET_EXCEEDED');
        observation.decodedWindowBytes=size;
        trajectory=await plugin.state.data.build().toRoot().apply(StateTransforms.Model.TrajectoryFromModelAndCoordinates,{modelRef:topologyModel.ref,coordinatesRef:coordinates.ref},{dependsOn:[topologyModel.ref,coordinates.ref]}).commit();
        model=await plugin.builders.structure.createModel(trajectory,{modelIndex:localIndex});
        structure=await plugin.builders.structure.createStructure(model);await represent();bytesIdentity=bytes;
      }else if(bytesIdentity!==bytes){const update=plugin.state.data.build();update.to(raw).update({data:bytes,label:'Verified preview XTC'});update.to(model).update({modelIndex:localIndex});await update.commit();bytesIdentity=bytes;
        const data=coordinates.obj.data,size=data.frames.reduce((sum,f)=>sum+f.elementCount*12,0);if(data.frames.length>64||size>L.windowBytes||data.frames.some(f=>f.elementCount!==manifest.atomCount)||data.frames.length!==mapping.frameCount)throw new Error('PREVIEW_MEMORY_BUDGET_EXCEEDED');observation.decodedWindowBytes=size;
      }else await plugin.state.data.build().to(model).update({modelIndex:localIndex}).commit();
      if(model.obj?.data?.modelNum!==localIndex)throw new Error('PREVIEW_RENDERER_FRAME_MISMATCH');
      // Commit to the real canvas before reporting a displayed frame.
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      observation.ready=!!structure.obj?.data?.elementCount&&plugin.canvas3d.reprCount.value>0;
      Object.assign(observation,{segmentFrameIndex:localIndex,sourceFrameIndex:mapping.sourceFrameIndex,timePs:mapping.timePs,fileId:mapping.fileId});
      if(selection.length)select(selection);onObservation({...observation});return {...observation};
    },
    async setRepresentation(value,nextAppearance){
      if(!structure)throw new Error('PREVIEW_BOX_ONLY');if(!['cartoon','sticks','spheres','lines'].includes(value))throw new Error('INVALID_VIEW_COMMAND');
      const requested=validateAppearance(nextAppearance??appearance),previous=appearance,previousRepresentation=representation;
      if(value===representation&&representations.length)appearance=await applyRepresentationAppearance(plugin,representations,requested,createStructureColorThemeParams);
      else{representation=value;appearance=requested;try{await represent();}catch(error){appearance=previous;representation=previousRepresentation;throw error;}}
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));onObservation({...observation,representation,appearance:{...appearance}});
    },select,
    async focus(){const loci=structure?plugin.managers.structure.selection.getLoci(structure.obj.data):null;if(selection.length&&loci)plugin.managers.camera.focusLoci(loci,{durationMs:0});else plugin.managers.camera.reset(undefined,0);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));},
    observe(){if(selectionTooLarge)throw new Error('PREVIEW_SELECTION_LIMIT');return {...observation,representation,appearance:{...appearance},representationStyles:representations.map(entry=>{const p=entry.selector.cell?.params?.values;return {type:p?.type.name,color:p?.colorTheme.name,colorParams:p?.colorTheme.params,material:p?.type.params.material,sizeFactor:p?.type.params.sizeFactor,sizeAspectRatio:p?.type.params.sizeAspectRatio,ignoreHydrogens:p?.type.params.ignoreHydrogens};}),selection:[...selection],camera:plugin.canvas3d.camera.getSnapshot()};},
    restoreCamera(value){plugin.canvas3d.camera.setState(value,0);},
    dispose(){stopTheme();resize.disconnect();camera.unsubscribe();picks.unsubscribe();plugin.dispose();container.replaceChildren();}
  };
}
