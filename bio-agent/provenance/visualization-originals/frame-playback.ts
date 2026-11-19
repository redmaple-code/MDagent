import type { FramePlaybackConfig, VizContext } from "@ksh/contracts";
import { contextMatches } from "./context";

/** Frame navigation preserves a newly observed camera, not other context changes. */
export function framePlaybackContextMatches(expected: VizContext, actual: VizContext): boolean {
  const before = expected.sceneRevisions, after = actual.sceneRevisions;
  const cameraOnly = before && after && after.CAMERA > before.CAMERA
    && actual.contextRevision >= expected.contextRevision;
  return contextMatches(cameraOnly ? {
    ...expected, contextRevision: actual.contextRevision,
    sceneRevisions: {...before, CAMERA: after.CAMERA},
  } : expected, actual)
    && JSON.stringify(expected.coordinateBasis ?? null) === JSON.stringify(actual.coordinateBasis ?? null);
}

export function validateFrameRange(
  config: FramePlaybackConfig,
  states: number,
): void {
  if (
    !Number.isInteger(config.startFrame) ||
    !Number.isInteger(config.endFrame) ||
    config.startFrame < 1 ||
    config.startFrame >= config.endFrame ||
    config.endFrame > states ||
    config.endFrame > 1000000 ||
    !Number.isFinite(config.framesPerSecond) ||
    config.framesPerSecond < 1 ||
    config.framesPerSecond > 20 ||
    ![1, -1].includes(config.direction) ||
    typeof config.loop !== "boolean"
  )
    throw Error("INVALID_PARAMS");
}

export function nextFrameInRange(
  config: FramePlaybackConfig,
  current: number | null,
): number | null {
  const first = config.direction === 1 ? config.startFrame : config.endFrame;
  if (current === null) return first;
  const next = current + config.direction;
  return next >= config.startFrame && next <= config.endFrame
    ? next
    : config.loop
      ? first
      : null;
}

/** Contiguous directional windows retain every file frame within bounded memory. */
export function filePlaybackWindow(config: FramePlaybackConfig, target: number, atoms: number) {
  if (!config.source || !Number.isSafeInteger(atoms) || atoms < 1 || atoms > 8000000
    || !Number.isInteger(target) || target < config.startFrame || target > config.endFrame)
    throw Error("INVALID_PARAMS");
  validateFrameRange(config, config.source.sourceFrameCount);
  const capacity = Math.min(256, Math.floor(8000000 / atoms));
  const startFrame = config.direction === 1 ? target : Math.max(config.startFrame, target - capacity + 1);
  const last = config.direction === 1 ? Math.min(config.endFrame, target + capacity - 1) : target;
  return {startFrame, frameCount: last - startFrame + 1, stride: 1, displayFrame: target - startFrame + 1};
}

/** Prepare only the next unloaded window shortly before its boundary. */
export function nextPrefetchWindow(config: FramePlaybackConfig, current: number, atoms: number,
  loaded: {startFrame: number; frameCount: number; stride: number}) {
  if (!config.source) return null;
  const end = loaded.startFrame + (loaded.frameCount - 1) * loaded.stride;
  let target = loaded.stride === 1 ? (config.direction === 1 ? end + 1 : loaded.startFrame - 1)
    : current + config.direction;
  let distance = Math.abs(target - current);
  if (target < config.startFrame || target > config.endFrame) {
    if (!config.loop) return null;
    target = config.direction === 1 ? config.startFrame : config.endFrame;
    distance = Math.abs((config.direction === 1 ? config.endFrame : config.startFrame) - current) + 1;
  }
  const offset = (target - loaded.startFrame) / loaded.stride;
  if (Number.isInteger(offset) && offset >= 0 && offset < loaded.frameCount) return null;
  if (distance > Math.min(32, Math.ceil(config.framesPerSecond * 2))) return null;
  const window = filePlaybackWindow(config, target, atoms);
  return atoms * window.frameCount * 12 <= 32 * 1024 * 1024 ? window : null;
}
