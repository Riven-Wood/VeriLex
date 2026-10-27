// Bundle only QuillPDF's MIT page operations and the existing locked MCP SDK.
import { build } from 'esbuild';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const assets = new URL('../../runtime/verilex_runtime/pdf_plugin/', import.meta.url);
const built = await build({metafile:true,entryPoints:[fileURLToPath(new URL('server.mjs',assets))],outfile:fileURLToPath(new URL('server.cjs',assets)),
 bundle:true,platform:'node',target:'node22',format:'cjs',legalComments:'eof',banner:{js:'// QuillPDF adapter: Copyright (c) 2026 Purple Directive, MIT. See LICENSE.txt.'}});
await build({entryPoints:[fileURLToPath(new URL('evidence.mjs',assets))],outfile:fileURLToPath(new URL('evidence.cjs',assets)),bundle:true,platform:'node',target:'node22',format:'cjs',legalComments:'eof',banner:{js:'// Uses QuillPDF MIT core; see LICENSE.txt and DEPENDENCY-LICENSE.txt.'}});
// Record the actual resolved packages, including nested dependency versions.
let licenses = '';
const packages = new Set();
for (const input of Object.keys(built.metafile.inputs)) {
 if (!input.includes('node_modules/')) continue;
 let directory = dirname(resolve(input));
 while (dirname(directory) !== directory) {
  const manifest = resolve(directory,'package.json');
  if (existsSync(manifest) && JSON.parse(readFileSync(manifest,'utf8')).name) break;
  directory = dirname(directory);
 }
 packages.add(directory);
}
for (const directory of [...packages].sort()) {
 const manifest = JSON.parse(readFileSync(resolve(directory,'package.json'),'utf8'));
 const files = readdirSync(directory).filter(n => /^(licen[sc]e|copying|notice)(\.|$)/i.test(n));
 if (!files.length) throw new Error(`Missing license for ${manifest.name}`);
 licenses += `\n${manifest.name} ${manifest.version}\n`;
 for (const file of files) licenses += readFileSync(resolve(directory,file),'utf8')+'\n';
}
writeFileSync(new URL('DEPENDENCY-LICENSE.txt',assets),licenses);
