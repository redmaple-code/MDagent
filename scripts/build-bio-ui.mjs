import {readFileSync,writeFileSync} from 'node:fs';
const source=new URL('../bio-agent/web/runtime/ui-cards/client.source.js',import.meta.url);
const output=new URL('../dsh-plugins/dsh-bio-ui/client.js',import.meta.url);
const bundled=new URL('../bio-agent/web/runtime/ui-cards/client.js',import.meta.url);
const input=readFileSync(source,'utf8');
if(input.split('__BIO_CARDS_PACKAGE_ID__').length!==2)throw new Error('BIO_UI_PACKAGE_MARKER_INVALID');
const compiled=input.replace('__BIO_CARDS_PACKAGE_ID__',JSON.stringify('bio-web-tool-cards'));
writeFileSync(output,compiled);writeFileSync(bundled,compiled);
