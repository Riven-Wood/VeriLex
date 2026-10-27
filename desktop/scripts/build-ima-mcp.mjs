// Bundle the MIT ima server, locked MCP SDK and compatible official Node runtime.
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root = new URL('../../runtime/verilex_runtime/knowledge/ima_mcp/', import.meta.url);
const version = '22.23.2';
const archives = {
  'darwin-arm64': ['tar.gz', '61130f394c1630d211dd50aecc4353d379480f36d3ac913cd85dbba1aed585c6'],
  'darwin-x64': ['tar.gz', '58e99022c2ff89395576cc7fd4d98cea24bb68081475d5f88b801ee8729fb026'],
  'win32-x64': ['zip', '1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97'],
};
const selected = archives[`${process.platform}-${process.arch}`];
if (!selected) throw new Error('Unsupported ima bundle build host');
const platform = process.platform === 'win32' ? 'win' : process.platform;
const name = `node-v${version}-${platform}-${process.arch}`;
const cache = fileURLToPath(new URL('../src-tauri/target/ima-node/', import.meta.url));
mkdirSync(cache, {recursive: true});
const archive = resolve(cache, `${name}.${selected[0]}`);
if (!existsSync(archive)) {
  const fetched = spawnSync('curl', ['--fail', '--location', '--max-time', '120',
    `https://nodejs.org/dist/v${version}/${name}.${selected[0]}`, '-o', archive], {stdio: 'inherit'});
  if (fetched.status !== 0) throw new Error('Cannot download locked ima Node runtime');
}
if (createHash('sha256').update(readFileSync(archive)).digest('hex') !== selected[1])
  throw new Error('ima Node archive checksum mismatch');
const extracted = spawnSync('tar', ['-xf', archive, '-C', cache], {stdio:'inherit'});
if (extracted.status !== 0) throw new Error('Cannot extract ima Node runtime');
const binary = process.platform === 'win32' ? 'node.exe' : 'bin/node';
copyFileSync(resolve(cache, name, binary), fileURLToPath(new URL(process.platform === 'win32' ? 'node.exe' : 'node', root)));
copyFileSync(resolve(cache, name, 'LICENSE'), fileURLToPath(new URL('NODE-LICENSE.txt', root)));
copyFileSync(fileURLToPath(new URL('../../node_modules/@modelcontextprotocol/sdk/LICENSE', import.meta.url)),
  fileURLToPath(new URL('MCP-SDK-LICENSE.txt', root)));
const bundled = await build({metafile: true, entryPoints: [fileURLToPath(new URL('server.mjs', root))],
  outfile: fileURLToPath(new URL('server.cjs', root)), bundle: true,
  platform: 'node', target: 'node22', format: 'cjs', legalComments: 'eof'});

const packages = new Set();
for (const input of Object.keys(bundled.metafile.inputs)) {
  if (!input.includes('node_modules/')) continue;
  let dir = dirname(resolve(input));
  while (dirname(dir) !== dir) {
    const manifest = resolve(dir, 'package.json');
    if (existsSync(manifest) && JSON.parse(readFileSync(manifest, 'utf8')).name) break;
    dir = dirname(dir);
  }
  packages.add(dir);
}
let notices = '';
for (const dir of [...packages].sort()) {
  const pkg = JSON.parse(readFileSync(resolve(dir, 'package.json'), 'utf8'));
  const licenses = readdirSync(dir).filter(name => /^(licen[sc]e|copying|notice)(\.|$)/i.test(name));
  if (!licenses.length) throw new Error(`Missing bundled license for ${pkg.name}`);
  notices += `\n${pkg.name} ${pkg.version}\n`;
  for (const name of licenses) notices += readFileSync(resolve(dir, name), 'utf8') + '\n';
}
writeFileSync(fileURLToPath(new URL('DEPENDENCY-LICENSE.txt', root)), notices);
