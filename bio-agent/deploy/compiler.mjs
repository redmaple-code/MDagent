import {readFileSync,realpathSync} from 'node:fs';
import {isAbsolute,join} from 'node:path';
import {pathToFileURL} from 'node:url';

export async function loadCompiler(root){
  if(!isAbsolute(root))throw new Error('BIO_COMPILER_PATH_INVALID');
  const actual=realpathSync(root);
  const manifest=JSON.parse(readFileSync(join(actual,'package.json'),'utf8'));
  if(manifest.name!=='typebox'||manifest.version!=='1.1.38')throw new Error('BIO_COMPILER_VERSION_UNSUPPORTED');
  const entry=realpathSync(join(actual,'build/compile/index.mjs'));
  if(!entry.startsWith(actual+'/'))throw new Error('BIO_COMPILER_PATH_INVALID');
  const {Compile}=await import(pathToFileURL(entry));
  if(typeof Compile!=='function')throw new Error('BIO_COMPILER_EXPORT_MISSING');
  return Compile;
}
