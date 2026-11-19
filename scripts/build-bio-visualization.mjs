import {readFileSync,writeFileSync,readdirSync,statSync,existsSync} from 'node:fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';import {createHash} from 'node:crypto';import {gzipSync,brotliCompressSync} from 'node:zlib';
const root=fileURLToPath(new URL('../bio-agent/web/visualization/',import.meta.url));
const {build}=await import(pathToFileURL(join(root,'node_modules/vite/dist/node/index.js')).href);
const result=await build({root});
const dist=join(root,'dist');const assets=[];
for(const output of (Array.isArray(result)?result:[result]).flatMap(r=>r.output)){
  const name=output.fileName;if(/\.(js|css)$/.test(name)){const path=join(dist,name);
  const bytes=readFileSync(path),gz=gzipSync(bytes),br=brotliCompressSync(bytes);
  writeFileSync(path+'.gz',gz);writeFileSync(path+'.br',br);
  assets.push({assetId:name,path:path.slice(dist.length+1),byteSize:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),gzipBytes:gz.length,brotliBytes:br.length});
}}
const entry=assets.find(a=>a.assetId.startsWith('bio-view-')&&a.assetId.endsWith('.js'));
if(!entry||new Set(assets.map(a=>a.assetId)).size!==assets.length)throw new Error('PREVIEW_ASSET_MANIFEST_INVALID');
writeFileSync(join(dist,'asset-manifest.json'),JSON.stringify({schemaVersion:'bio.assets.v1',entry:entry.assetId,styles:assets.filter(a=>a.assetId.endsWith('.css')).map(a=>a.assetId),assets},null,2)+'\n');
console.log(JSON.stringify({entry:entry.assetId,assets:assets.length,gzipBytes:assets.reduce((n,a)=>n+a.gzipBytes,0)}));
