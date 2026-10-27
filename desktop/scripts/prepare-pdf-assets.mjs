import {cpSync,mkdirSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname,resolve} from 'node:path';
const require=createRequire(import.meta.url);
const source=dirname(require.resolve('pdfjs-dist/package.json'));
for(const name of ['cmaps','standard_fonts']){
 const destination=resolve(import.meta.dirname,'../public/pdfjs',name);
 mkdirSync(destination,{recursive:true});cpSync(resolve(source,name),destination,{recursive:true});
}
