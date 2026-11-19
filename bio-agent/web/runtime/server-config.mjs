export function serverConfig(z,{compilerRequired=true}={}){
  return z.object({projectsRoot:z.string().required(),compilerRoot:compilerRequired?z.string().required():z.string(),
  mode:z.union(['node-world','node-ssh','ssh']).default('node-world'),connectionFile:z.string(),nodeId:z.string(),
  executionTargetFile:z.string().required(),gatewayPython:z.string(),gatewayReleaseRoot:z.string(),gatewayConnectionFile:z.string(),
  gateMode:z.union(['platform','bio-grant-only']).default('platform'),platformGateModule:z.string(),previewEnabled:z.boolean().default(false),viewEnabled:z.boolean().default(false),directEnabled:z.boolean().default(false),
  computeProjectsRoot:z.string(),computeReleaseRoot:z.string(),computePython:z.string()});
}
