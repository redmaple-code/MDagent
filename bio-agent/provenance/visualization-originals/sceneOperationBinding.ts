import type { ManualSceneSelection, SceneInteraction } from "@ksh/contracts";

interface BindingScene {
  projectId: string;
  sceneSessionId: string;
  frame: number;
  view?: number[] | null;
  revisions?: {CAMERA: number; FRAME: number};
  objects: Array<{
    objectName: string;
    artifact: { projectId: string; artifactId: string; sha256: string };
    states?: number;
    derivedCoordinates?: unknown;
    trajectory?: { artifact: { artifactId: string; sha256: string }; sequence?: unknown;
      startFrame?: number; stride?: number; frameCount?: number; sourceFrameCount?: number };
  }>;
  manualSelection?: ManualSceneSelection;
  interaction?: SceneInteraction;
  researchState?: {
    recipes: Array<{operationId: string; objectName: string; operation: {kind: string; name?: string}}>;
    selectionBindings?: Array<{operationId: string; objectName: string; atomIndices: number[]}>;
  };
}

export interface SceneOperationBinding {
  projectId: string;
  sceneSessionId: string;
  sources: Array<{ objectName: string; identity: string | null }>;
  primaryObjectName?: string | null;
  documentId?: string | null;
  manualSelection?: string;
  orderedPicks?: string;
  namedSelections: Array<{objectName: string; name: string; identity: string | null}>;
  objectSet?: string;
  frameMappings?: Array<{objectName: string; identity: string | null}>;
  presentation?: ScenePresentationBinding;
}

export interface ScenePresentationBinding {
  cameraRevision: number;
  cameraView: string;
  frameRevision: number;
  frame: number;
  preserved?: boolean;
}

export function captureScenePresentation(scene: BindingScene): ScenePresentationBinding | undefined {
  return scene.revisions ? {cameraRevision: scene.revisions.CAMERA, cameraView: JSON.stringify(scene.view ?? null),
    frameRevision: scene.revisions.FRAME, frame: scene.frame} : undefined;
}

export function scenePresentationMatches(binding: ScenePresentationBinding, scene: BindingScene,
  group: "camera" | "frame"): boolean {
  return group === "camera" ? binding.cameraRevision === scene.revisions?.CAMERA &&
    binding.cameraView === JSON.stringify(scene.view ?? null)
    : binding.frameRevision === scene.revisions?.FRAME && binding.frame === scene.frame;
}

// These fields refer to molecular owners in the closed scene/chat payloads.
// Literal labels, property values, filenames and aliases are never interpreted.
const ownerFields = new Set(["objectName", "targetObjectName", "sourceObjectName", "toObjectName"]);
const ownersFields = new Set(["objectNames", "mobileObjectNames"]);
function referencedOwners(payload: unknown, names: Set<string>) {
  if (Array.isArray(payload)) {
    for (const item of payload) referencedOwners(item, names);
  } else if (payload && typeof payload === "object") {
    const derive = "kind" in payload && payload.kind === "derive";
    for (const [key, value] of Object.entries(payload)) {
      if (ownerFields.has(key) && typeof value === "string") names.add(value);
      else if ((ownersFields.has(key) || derive && key === "sources") && Array.isArray(value)) {
        for (const name of value) if (typeof name === "string") names.add(name);
      } else referencedOwners(value, names);
    }
  }
}

function sourceIdentity(scene: BindingScene, name: string): string | null {
  const object = scene.objects.find(object => object.objectName === name);
  return object ? JSON.stringify([object.artifact.projectId, object.artifact.artifactId, object.artifact.sha256,
    object.trajectory?.artifact.artifactId ?? null, object.trajectory?.artifact.sha256 ?? null,
    object.trajectory?.sequence ?? null]) : null;
}

function frameMappingIdentity(scene: BindingScene, name: string): string | null {
  const object = scene.objects.find(object => object.objectName === name), window = object?.trajectory;
  return object ? JSON.stringify([object.states ?? 1, window?.startFrame ?? null, window?.stride ?? null,
    window?.frameCount ?? null, window?.sourceFrameCount ?? null, object.derivedCoordinates ?? null]) : null;
}

function referencedSelections(payload: unknown, owners: string[], names: Map<string, {objectName: string; name: string}>) {
  if (Array.isArray(payload)) {
    for (const item of payload) referencedSelections(item, owners, names);
  } else if (payload && typeof payload === "object") {
    const node = payload as Record<string, unknown>;
    const localOwners = typeof node.objectName === "string" ? [node.objectName] : owners;
    const remember = (value: unknown) => {
      if (typeof value === "string") for (const objectName of localOwners)
        names.set(JSON.stringify([objectName, value]), {objectName, name: value});
    };
    if (node.kind === "named") remember(node.name);
    if (node.kind === "select") {remember(node.name); remember(node.stepFrom);}
    for (const [key, value] of Object.entries(node)) {
      const operandOwners = key === "targetSelector" && typeof node.targetObjectName === "string" ? [node.targetObjectName]
        : key === "mobileSelector" && Array.isArray(node.mobileObjectNames)
          ? node.mobileObjectNames.filter((name): name is string => typeof name === "string") : localOwners;
      referencedSelections(value, operandOwners, names);
    }
  }
}

function namedSelectionIdentity(scene: BindingScene, objectName: string, name: string): string | null {
  const recipe = scene.researchState?.recipes.findLast(recipe => recipe.objectName === objectName &&
    recipe.operation.kind === "select" && recipe.operation.name === name);
  if (!recipe) return null;
  const members = scene.researchState?.selectionBindings?.find(binding => binding.objectName === objectName &&
    binding.operationId === recipe.operationId);
  // Accepted recipe IDs are immutable journal identities. Retain only their
  // identity/count witness, not a second copy of up to 100000 member indices.
  return JSON.stringify([recipe.operationId, members?.atomIndices.length ?? null]);
}

function selectionIdentity(selection: ManualSceneSelection | undefined) {
  // Native selection membership has no ordering semantics; ordered picks do.
  return JSON.stringify([selection?.status ?? "complete", selection?.atomCount ?? 0,
    (selection?.atoms ?? []).map(atom => [atom.objectName, atom.atomIndex])
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])) || Number(a[1]) - Number(b[1]))]);
}

function picksIdentity(scene: BindingScene) {
  const interaction = scene.interaction;
  return JSON.stringify([scene.frame, interaction?.mode ?? "select", interaction?.requiredPicks ?? 0,
    (interaction?.picks ?? []).map(pick => [pick.objectName, pick.atomIndex, pick.state])]);
}

/** Metadata only: freeze all operands before waiting; never copy source coordinates. */
export function captureSceneOperationBinding(scene: BindingScene, payload: unknown,
  options: { primaryObjectName?: string | null; documentId?: string | null;
    manualSelection?: boolean; orderedPicks?: boolean; allObjects?: boolean;
    frameMapping?: boolean; presentation?: boolean } = {}): SceneOperationBinding {
  const names = new Set<string>();
  const selections = new Map<string, {objectName: string; name: string}>();
  referencedOwners(payload, names);
  referencedSelections(payload, options.primaryObjectName ? [options.primaryObjectName] : [], selections);
  for (const selection of selections.values()) names.add(selection.objectName);
  if (options.primaryObjectName) names.add(options.primaryObjectName);
  if (options.allObjects) for (const object of scene.objects) names.add(object.objectName);
  if (options.manualSelection) for (const atom of scene.manualSelection?.atoms ?? []) names.add(atom.objectName);
  if (options.orderedPicks) for (const pick of scene.interaction?.picks ?? []) names.add(pick.objectName);
  const presentation = options.presentation ? captureScenePresentation(scene) : undefined;
  return { projectId: scene.projectId, sceneSessionId: scene.sceneSessionId,
    sources: [...names].map(objectName => ({ objectName, identity: sourceIdentity(scene, objectName) })),
    namedSelections: [...selections.values()].map(selection => ({...selection,
      identity: namedSelectionIdentity(scene, selection.objectName, selection.name)})),
    ...(options.primaryObjectName !== undefined ? {primaryObjectName: options.primaryObjectName} : {}),
    ...(options.documentId !== undefined ? {documentId: options.documentId} : {}),
    ...(options.manualSelection ? {manualSelection: selectionIdentity(scene.manualSelection)} : {}),
    ...(options.orderedPicks ? {orderedPicks: picksIdentity(scene)} : {}),
    ...(options.allObjects ? {objectSet: JSON.stringify(scene.objects.map(object => object.objectName).sort())} : {}),
    ...(options.frameMapping ? {frameMappings: [...names].map(objectName => ({objectName,
      identity: frameMappingIdentity(scene, objectName)}))} : {}),
    ...(presentation ? {presentation} : {}),
  };
}

export function sceneOperationBindingMatches(binding: SceneOperationBinding, scene: BindingScene | null,
  projectId: string, documentId: string | null): boolean {
  return !!scene && binding.projectId === projectId && binding.projectId === scene.projectId &&
    binding.sceneSessionId === scene.sceneSessionId &&
    (binding.documentId === undefined || binding.documentId === documentId) &&
    binding.sources.every(source => source.identity === sourceIdentity(scene, source.objectName)) &&
    binding.namedSelections.every(selection => selection.identity === namedSelectionIdentity(scene, selection.objectName, selection.name)) &&
    (binding.frameMappings === undefined || binding.frameMappings.every(mapping =>
      mapping.identity === frameMappingIdentity(scene, mapping.objectName))) &&
    (binding.objectSet === undefined || binding.objectSet === JSON.stringify(scene.objects.map(object => object.objectName).sort())) &&
    (binding.manualSelection === undefined || binding.manualSelection === selectionIdentity(scene.manualSelection)) &&
    (binding.orderedPicks === undefined || binding.orderedPicks === picksIdentity(scene));
}
