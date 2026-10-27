import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants as zlibConstants } from "node:zlib";

const contract = {
  schema_version: 1,
  platform: "macos",
  arch: "arm64",
  network_during_build: "denied",
  output: "src-tauri/generated/documents-macos-arm64",
  manifest: "component-manifest.json",
};

if (process.argv.slice(2).includes("--check-contract")) {
  process.stdout.write(`${JSON.stringify(contract)}\n`);
  process.exit(0);
}
if (process.platform !== "darwin" || process.arch !== "arm64") {
  throw new Error("documents_bundle_requires_macos_arm64");
}

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(desktopRoot, "..");
const tauriRoot = resolve(desktopRoot, "src-tauri");
const managedWorkRoot = resolve(tauriRoot, "target", "document-bundle-work");
const stagingRoot = resolve(managedWorkRoot, "staging");
const outputRoot = resolve(tauriRoot, "generated", "documents-macos-arm64");
const cacheRoot = resolve(
  process.env.VERILEX_DOCUMENT_CACHE ??
    join(process.env.HOME ?? "", "Library", "Caches", "VeriLex", "documents-v1"),
);
const python = resolve(repositoryRoot, "runtime", ".venv", "bin", "python");
const node = realpathSync(process.execPath);
const git = "/usr/bin/git";
const sandboxExec = "/usr/bin/sandbox-exec";
const envTool = "/usr/bin/env";
const hdiutil = "/usr/bin/hdiutil";
const ditto = "/usr/bin/ditto";
const dotnet = resolve(cacheRoot, "dotnet", "dotnet");
const preparedNugetPackages = resolve(process.env.HOME ?? "", ".nuget", "packages");

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  }
  return value;
}

function canonicalBytes(value) {
  return Buffer.from(JSON.stringify(canonical(value)), "utf8");
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function treeSha256(root, excluded = new Set()) {
  const entries = Object.fromEntries(
    filesBelow(root)
      .map((path) => relative(root, path).split(sep).join("/"))
      .filter((path) => !excluded.has(path))
      .sort()
      .map((path) => [path, sha256(resolve(root, path))]),
  );
  return {
    fileCount: Object.keys(entries).length,
    sha256: createHash("sha256").update(canonicalBytes(entries)).digest("hex"),
  };
}

function requireFile(path, expectedHash) {
  if (!existsSync(path) || !lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) {
    throw new Error(`bundle_input_missing:${relative(repositoryRoot, path)}`);
  }
  if (expectedHash && sha256(path) !== expectedHash) {
    throw new Error(`bundle_input_hash_mismatch:${relative(cacheRoot, path)}`);
  }
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? repositoryRoot,
    env: options.env ?? process.env,
    stdio: options.capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: options.capture ? "utf8" : undefined,
    maxBuffer: options.capture ? 32 * 1024 * 1024 : undefined,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? String(result.stderr).trim().slice(0, 500) : "";
    throw new Error(`bundle_command_failed:${command}:${result.status ?? "signal"}:${detail}`);
  }
  return options.capture ? String(result.stdout).trim() : "";
}

function copyTree(source, destination) {
  mkdirSync(dirname(destination), { recursive: true });
  cpSync(source, destination, { recursive: true, preserveTimestamps: true });
}

function filesBelow(root) {
  const found = [];
  const visit = (directory) => {
    for (const name of readdirSync(directory).sort()) {
      const path = join(directory, name);
      const metadata = lstatSync(path);
      if (metadata.isSymbolicLink()) throw new Error(`bundle_symlink_rejected:${path}`);
      if (metadata.isDirectory()) visit(path);
      else if (metadata.isFile()) found.push(path);
      else throw new Error(`bundle_special_file_rejected:${path}`);
    }
  };
  visit(root);
  return found;
}

function materializeSymlinks(root) {
  for (let pass = 0; pass < 8; pass += 1) {
    const links = [];
    const visit = (directory) => {
      for (const name of readdirSync(directory)) {
        const path = join(directory, name);
        const metadata = lstatSync(path);
        if (metadata.isSymbolicLink()) links.push(path);
        else if (metadata.isDirectory()) visit(path);
      }
    };
    visit(root);
    if (links.length === 0) return;
    links.sort((a, b) => b.split(sep).length - a.split(sep).length);
    for (const link of links) {
      if (!lstatSync(link).isSymbolicLink()) continue;
      const source = realpathSync(link);
      const temporary = `${link}.verilex-materialized`;
      rmSync(temporary, { recursive: true, force: true });
      if (statSync(source).isDirectory()) copyTree(source, temporary);
      else copyFileSync(source, temporary);
      unlinkSync(link);
      renameSync(temporary, link);
    }
  }
  throw new Error("bundle_symlink_materialization_failed");
}

function cleanEnvironment(home) {
  mkdirSync(home, { recursive: true });
  const executablePath = [dirname(node), dirname(dotnet), "/usr/bin", "/bin", "/usr/sbin", "/sbin"].join(":");
  return {
    HOME: home,
    PATH: executablePath,
    LANG: "en_US.UTF-8",
    LC_ALL: "en_US.UTF-8",
    DOTNET_ROOT: dirname(dotnet),
    DOTNET_CLI_HOME: join(home, "dotnet-cli"),
    NUGET_PACKAGES: preparedNugetPackages,
    NUGET_HTTP_CACHE_PATH: join(home, "nuget-http"),
    NuGetAudit: "false",
    RestoreIgnoreFailedSources: "true",
    DOTNET_CLI_TELEMETRY_OPTOUT: "1",
    NPM_CONFIG_OFFLINE: "true",
    NPM_CONFIG_AUDIT: "false",
    NPM_CONFIG_FUND: "false",
    NPM_CONFIG_UPDATE_NOTIFIER: "false",
    PYINSTALLER_CONFIG_DIR: join(home, "pyinstaller"),
    TMPDIR: join(home, "tmp"),
    PYTHONHASHSEED: "0",
    SOURCE_DATE_EPOCH: "1788134400",
    ZERO_AR_DATE: "1",
  };
}

function buildRenderer(sourceRoot, environment) {
  const npmRoot = join(sourceRoot, "npm");
  const wasmProject = join(sourceRoot, "wasm", "DocxodusWasm");
  run(
    dotnet,
    [
      "publish",
      join(wasmProject, "DocxodusWasm.csproj"),
      "-c",
      "Release",
      "-p:NuGetAudit=false",
      "-p:RestoreIgnoreFailedSources=true",
    ],
    { cwd: wasmProject, env: environment },
  );
  const appBundle = join(
    wasmProject,
    "bin",
    "Release",
    "net10.0",
    "browser-wasm",
    "AppBundle",
  );
  const wasmOutput = join(npmRoot, "dist", "wasm");
  rmSync(wasmOutput, { recursive: true, force: true });
  mkdirSync(join(wasmOutput, "_framework"), { recursive: true });
  for (const name of readdirSync(join(appBundle, "_framework")).sort()) {
    if (name.endsWith(".map") || name.endsWith(".symbols")) continue;
    const source = join(appBundle, "_framework", name);
    if (lstatSync(source).isFile()) copyFileSync(source, join(wasmOutput, "_framework", name));
  }
  copyFileSync(join(wasmProject, "main.js"), join(wasmOutput, "main.js"));
  copyFileSync(join(wasmProject, "index.html"), join(wasmOutput, "index.html"));
  for (const name of ["dotnet.js", "dotnet.native.js"]) {
    const path = join(wasmOutput, "_framework", name);
    writeFileSync(
      path,
      readFileSync(path, "utf8").replaceAll('credentials:"same-origin"', 'credentials:"omit"'),
    );
  }
  const boot = join(wasmOutput, "_framework", "dotnet.boot.js");
  writeFileSync(
    boot,
    readFileSync(boot, "utf8").replace(/"integrity"(\s*:\s*"sha256-)/g, '"hash"$1'),
  );
  for (const path of filesBelow(join(wasmOutput, "_framework"))) {
    const bytes = readFileSync(path);
    writeFileSync(
      `${path}.br`,
      brotliCompressSync(bytes, {
        params: {
          [zlibConstants.BROTLI_PARAM_QUALITY]: 11,
          [zlibConstants.BROTLI_PARAM_SIZE_HINT]: bytes.length,
        },
      }),
    );
  }
  const modules = join(npmRoot, "node_modules");
  run(node, [join(modules, "typescript", "bin", "tsc")], { cwd: npmRoot, env: environment });
  const esbuild = join(modules, "esbuild", "bin", "esbuild");
  const jobs = [
    ["src/pagination.ts", "--bundle", "--format=iife", "--global-name=DocxodusPagination", "--outfile=dist/pagination.bundle.js"],
    ["src/session.ts", "--bundle", "--format=iife", "--global-name=DocxodusSession", "--outfile=dist/session.bundle.js"],
    ["src/ribbon.ts", "--bundle", "--format=iife", "--global-name=DocxodusEditor", "--outfile=dist/editor.bundle.js"],
    ["src/embed.ts", "--bundle", "--format=esm", "--outfile=dist/embed.bundle.js"],
    ["src/embed.ts", "--bundle", "--format=iife", "--global-name=Docxodus", "--outfile=dist/embed.iife.js"],
    ["src/docxodus.worker.ts", "--bundle", "--format=esm", "--metafile=dist/docxodus.worker.meta.json", "--outfile=dist/docxodus.worker.js"],
    ["src/worker-proxy.ts", "--bundle", "--format=esm", "--outfile=dist/worker-proxy.bundle.js"],
    ["src/export-browser.ts", "--bundle", "--format=esm", "--metafile=dist/export-browser.meta.json", "--outfile=dist/export-browser.bundle.js"],
  ];
  for (const args of jobs) run(esbuild, args, { cwd: npmRoot, env: environment });
  run(node, [join(npmRoot, "scripts", "generate-export-assets.mjs")], { cwd: npmRoot, env: environment });
  return join(npmRoot, "dist");
}

function componentInfo(relativePath, pdfiumLicenses) {
  if (relativePath.startsWith("libreoffice/")) {
    return {
      version: "26.2.4",
      role: "unmodified_docx_to_pdf_process",
      licenses: [
        "libreoffice/LibreOffice.app/Contents/Resources/LICENSE",
        "libreoffice/LibreOffice.app/Contents/Resources/NOTICE",
      ],
    };
  }
  if (relativePath.startsWith("rasterizer/")) {
    return { version: "5.11.0", role: "isolated_pdf_rasterizer", licenses: pdfiumLicenses };
  }
  if (relativePath.startsWith("renderer/") || relativePath.startsWith("docxodus/")) {
    return {
      version: "b1457bbd78b14177e3e71c739911f1772a6b1b22",
      role: "selected_docxodus_engine",
      licenses: ["licenses/docxodus/LICENSE"],
    };
  }
  if (relativePath.startsWith("adeu/")) {
    return {
      version: "8a8b103849a3173c07638040c33282731d1af92e",
      role: "required_adeu_auxiliary",
      licenses: ["licenses/adeu/LICENSE"],
    };
  }
  if (relativePath.startsWith("fonts/")) {
    return {
      version: "ade3d1533e06b2b1462ffcde8e08b129627ca360",
      role: "fixed_document_font",
      licenses: ["licenses/fonts/OFL-1.1.txt"],
    };
  }
  if (relativePath.startsWith("licenses/")) {
    return { version: "1", role: "distributed_license", licenses: [relativePath] };
  }
  return {
    version: "1",
    role: "document_bundle_metadata",
    licenses: ["licenses/verilex/THIRD_PARTY_NOTICES.md"],
  };
}

const sourceLock = JSON.parse(
  readFileSync(resolve(repositoryRoot, "third_party", "documents", "sources.lock.json"), "utf8"),
);
const selection = JSON.parse(
  readFileSync(resolve(repositoryRoot, "third_party", "documents", "engine-selection.json"), "utf8"),
);
const sources = Object.fromEntries(sourceLock.sources.map((source) => [source.id, source]));
const requiredSources = [
  "docxodus",
  "adeu",
  "libreoffice-macos-arm64",
  "pypdfium2-macos-arm64",
  "document-fonts",
];
if (requiredSources.some((id) => !sources[id])) throw new Error("document_source_lock_incomplete");

const libreOfficeDmg = resolve(
  cacheRoot,
  "downloads",
  "LibreOffice_26.2.4_MacOS_aarch64.dmg",
);
const pypdfiumWheel = resolve(
  cacheRoot,
  "downloads",
  "pypdfium2-5.11.0-py3-none-macosx_12_0_arm64.whl",
);
requireFile(libreOfficeDmg, sources["libreoffice-macos-arm64"].artifact_sha256);
requireFile(pypdfiumWheel, sources["pypdfium2-macos-arm64"].artifact_sha256);
requireFile(dotnet);
if (!existsSync(python) || !statSync(python).isFile()) {
  throw new Error("bundle_python_environment_missing");
}

const fontLock = JSON.parse(
  readFileSync(resolve(repositoryRoot, "qa", "documents", "fonts", "fonts.lock.json"), "utf8"),
);
for (const font of fontLock.fonts) {
  requireFile(resolve(cacheRoot, "fonts", font.source_path.split("/").at(-1)), font.sha256);
}

// Re-enter under one network-denying boundary so every child inherits it.
if (process.env.VERILEX_DOCUMENT_BUILD_SANDBOXED !== "1") {
  const profile = [
    "(version 1)",
    "(allow default)",
    "(deny network*)",
    "(allow network* (local unix-socket))",
  ].join("\n");
  const result = spawnSync(
    sandboxExec,
    [
      "-p",
      profile,
      envTool,
      "VERILEX_DOCUMENT_BUILD_SANDBOXED=1",
      node,
      fileURLToPath(import.meta.url),
      ...process.argv.slice(2),
    ],
    { cwd: desktopRoot, env: process.env, stdio: "inherit" },
  );
  if (result.error) throw result.error;
  process.exit(result.status ?? 2);
}

rmSync(managedWorkRoot, { recursive: true, force: true });
mkdirSync(stagingRoot, { recursive: true });
const environment = cleanEnvironment(resolve(managedWorkRoot, "home"));
mkdirSync(environment.TMPDIR, { recursive: true });
run(
  python,
  [resolve(repositoryRoot, "scripts", "documents", "verify_sources.py"), "--check"],
  { env: environment },
);

const buildSources = resolve(managedWorkRoot, "sources");
mkdirSync(buildSources, { recursive: true });
for (const id of ["docxodus", "adeu"]) {
  const cached = resolve(cacheRoot, "sources", id);
  const destination = resolve(buildSources, id);
  run(git, ["clone", "--no-hardlinks", "--no-checkout", cached, destination], {
    env: environment,
  });
  run(git, ["checkout", "--detach", sources[id].commit], {
    cwd: destination,
    env: environment,
  });
  const commit = run(git, ["rev-parse", "HEAD"], {
    cwd: destination,
    env: environment,
    capture: true,
  });
  const tree = run(git, ["rev-parse", "HEAD^{tree}"], {
    cwd: destination,
    env: environment,
    capture: true,
  });
  if (commit !== sources[id].commit || tree !== sources[id].tree) {
    throw new Error(`source_identity_mismatch:${id}`);
  }
}

const docxodusRoot = resolve(buildSources, "docxodus");
const keyboardPatch = resolve(desktopRoot, "patches", "docxodus-keyboard.patch");
run(git, ["apply", "--check", keyboardPatch], { cwd: docxodusRoot, env: environment });
run(git, ["apply", keyboardPatch], { cwd: docxodusRoot, env: environment });
const cachedModules = resolve(cacheRoot, "sources", "docxodus", "npm", "node_modules");
if (!existsSync(cachedModules)) throw new Error("docxodus_dependency_cache_missing");
symlinkSync(cachedModules, resolve(docxodusRoot, "npm", "node_modules"), "dir");
const rendererDist = buildRenderer(docxodusRoot, environment);

const hostOutput = resolve(managedWorkRoot, "docxodus-host");
run(
  dotnet,
  [
    "publish",
    resolve(docxodusRoot, "tools", "python-host", "pyhost.csproj"),
    "-c",
    "Release",
    "-r",
    "osx-arm64",
    "--self-contained",
    "true",
    "-p:PublishSingleFile=true",
    "-p:PublishReadyToRun=false",
    "-p:IncludeNativeLibrariesForSelfExtract=true",
    "-p:NuGetAudit=false",
    "-p:RestoreIgnoreFailedSources=true",
    "-o",
    hostOutput,
  ],
  { cwd: docxodusRoot, env: environment },
);
requireFile(resolve(hostOutput, "docxodus-pyhost"));

const rendererRoot = resolve(stagingRoot, "renderer");
copyTree(resolve(rendererDist, "wasm"), resolve(rendererRoot, "wasm"));
copyFileSync(resolve(rendererDist, "embed.iife.js"), resolve(rendererRoot, "docxodus.bundle.js"));
copyFileSync(resolve(rendererDist, "docxodus.worker.js"), resolve(rendererRoot, "docxodus.worker.js"));
copyFileSync(
  resolve(rendererDist, "worker-proxy.bundle.js"),
  resolve(rendererRoot, "worker-proxy.bundle.js"),
);
copyFileSync(
  resolve(repositoryRoot, "qa", "documents", "fixtures", "read_structure.docx"),
  resolve(rendererRoot, "safe-basic.docx"),
);
copyTree(hostOutput, resolve(stagingRoot, "docxodus", "host"));
copyTree(
  resolve(buildSources, "adeu", "python", "src", "adeu"),
  resolve(stagingRoot, "adeu", "package", "adeu"),
);
writeFileSync(
  resolve(stagingRoot, "adeu", "capabilities.json"),
  canonicalBytes({
    schema_version: 1,
    capabilities: selection.required_adeu_capabilities,
  }),
);

const rasterizer = resolve(stagingRoot, "rasterizer", "document-rasterizer");
mkdirSync(dirname(rasterizer), { recursive: true });
run(
  python,
  [
    resolve(repositoryRoot, "scripts", "documents", "build_rasterizer.py"),
    "--python",
    python,
    "--output",
    rasterizer,
  ],
  { env: environment },
);
chmodSync(rasterizer, 0o755);

const mountPoint = resolve(managedWorkRoot, "libreoffice-mount");
mkdirSync(mountPoint, { recursive: true });
run(hdiutil, ["attach", "-readonly", "-nobrowse", "-mountpoint", mountPoint, libreOfficeDmg], {
  env: environment,
});
try {
  mkdirSync(resolve(stagingRoot, "libreoffice"), { recursive: true });
  run(
    ditto,
    [
      "--noqtn",
      resolve(mountPoint, "LibreOffice.app"),
      resolve(stagingRoot, "libreoffice", "LibreOffice.app"),
    ],
    { env: environment },
  );
} finally {
  run(hdiutil, ["detach", mountPoint], { env: environment });
}
materializeSymlinks(resolve(stagingRoot, "libreoffice", "LibreOffice.app"));

const licensesRoot = resolve(stagingRoot, "licenses");
for (const directory of ["docxodus", "adeu", "fonts", "verilex"]) {
  mkdirSync(resolve(licensesRoot, directory), { recursive: true });
}
copyFileSync(
  resolve(repositoryRoot, "third_party", "licenses", "documents", "Docxodus-LICENSE.txt"),
  resolve(licensesRoot, "docxodus", "LICENSE"),
);
copyFileSync(
  resolve(repositoryRoot, "third_party", "licenses", "documents", "Adeu-LICENSE.txt"),
  resolve(licensesRoot, "adeu", "LICENSE"),
);
copyFileSync(
  resolve(repositoryRoot, "qa", "documents", "fonts", "LICENSES", "OFL-1.1.txt"),
  resolve(licensesRoot, "fonts", "OFL-1.1.txt"),
);
copyFileSync(
  resolve(repositoryRoot, "THIRD_PARTY_NOTICES.md"),
  resolve(licensesRoot, "verilex", "THIRD_PARTY_NOTICES.md"),
);
const pdfiumLicenseSource = resolve(
  repositoryRoot,
  "runtime",
  ".venv",
  "lib",
  "python3.13",
  "site-packages",
  "pypdfium2-5.11.0.dist-info",
  "licenses",
);
copyTree(pdfiumLicenseSource, resolve(licensesRoot, "pypdfium2"));
const pdfiumLicenses = filesBelow(resolve(licensesRoot, "pypdfium2")).map((path) =>
  relative(stagingRoot, path).split(sep).join("/"),
);

mkdirSync(resolve(stagingRoot, "fonts"), { recursive: true });
for (const font of fontLock.fonts) {
  const name = font.source_path.split("/").at(-1);
  copyFileSync(resolve(cacheRoot, "fonts", name), resolve(stagingRoot, "fonts", name));
}

const rendererLockPath = resolve(repositoryRoot, "desktop", "src", "documents", "renderer-lock.json");
const rendererLock = JSON.parse(readFileSync(rendererLockPath, "utf8"));
if (
  rendererLock.route !== "docxodus" ||
  rendererLock.version !== sources.docxodus.commit ||
  rendererLock.source_sha256 !== sources.docxodus.tree ||
  rendererLock.assets.some((asset) => /^https?:/i.test(asset.path))
) {
  throw new Error("renderer_lock_identity_mismatch");
}
// Updating the lock is an explicit maintainer operation after changing the
// reviewed source patch. Normal builds still verify every generated byte.
if (process.argv.includes("--update-renderer-lock")) {
  rendererLock.local_patch_sha256 = sha256(keyboardPatch);
  for (const asset of rendererLock.assets) asset.sha256 = sha256(resolve(stagingRoot, asset.path));
  const nextTree = treeSha256(rendererRoot, new Set(["renderer-lock.json"]));
  rendererLock.asset_file_count = nextTree.fileCount;
  rendererLock.asset_tree_sha256 = nextTree.sha256;
  writeFileSync(rendererLockPath, JSON.stringify(rendererLock, null, 2) + "\n");
}
if (rendererLock.local_patch_sha256 !== sha256(keyboardPatch)) throw new Error("renderer_local_patch_mismatch");
for (const asset of rendererLock.assets) {
  const path = resolve(stagingRoot, asset.path);
  if (!path.startsWith(`${stagingRoot}${sep}`) || !existsSync(path) || sha256(path) !== asset.sha256) {
    throw new Error(
      `renderer_lock_asset_mismatch:${asset.path}:${existsSync(path) ? sha256(path) : "missing"}`,
    );
  }
}
const rendererTree = treeSha256(rendererRoot, new Set(["renderer-lock.json"]));
if (
  rendererLock.asset_file_count !== rendererTree.fileCount ||
  rendererLock.asset_tree_sha256 !== rendererTree.sha256
) {
  throw new Error(
    `renderer_lock_tree_mismatch:${rendererTree.fileCount}:${rendererTree.sha256}`,
  );
}
copyFileSync(rendererLockPath, resolve(rendererRoot, "renderer-lock.json"));

const template = JSON.parse(
  readFileSync(resolve(tauriRoot, "documents", "component-manifest.template.json"), "utf8"),
);
if (template.platform !== "macos" || template.arch !== "arm64" || template.minimum_macos !== "12.0") {
  throw new Error("component_manifest_template_invalid");
}
const components = filesBelow(stagingRoot).map((path) => {
  const relativePath = relative(stagingRoot, path).split(sep).join("/");
  const info = componentInfo(relativePath, pdfiumLicenses);
  return {
    id: `${info.role}-${createHash("sha256").update(relativePath).digest("hex").slice(0, 20)}`,
    version: info.version,
    platform: template.platform,
    arch: template.arch,
    relative_path: relativePath,
    size_bytes: statSync(path).size,
    sha256: sha256(path),
    license_paths: info.licenses,
    minimum_macos: template.minimum_macos,
    role: info.role,
  };
});
const manifest = {
  schema_version: 1,
  selected_engine: selection.selected_engine,
  system_read_allowlist: [
    "/System/Library",
    "/System/Volumes/Preboot/Cryptexes",
    "/usr/lib",
    "/usr/bin",
    "/usr/share",
    "/bin",
    "/dev",
    "/private/etc",
    "/private/var/db/timezone",
    "/Library/Fonts",
  ],
  components,
};
const manifestBytes = canonicalBytes(manifest);
writeFileSync(resolve(stagingRoot, "component-manifest.json"), manifestBytes);
const manifestHash = createHash("sha256").update(manifestBytes).digest("hex");
writeFileSync(resolve(managedWorkRoot, "component-manifest.sha256"), `${manifestHash}\n`);

rmSync(outputRoot, { recursive: true, force: true });
mkdirSync(dirname(outputRoot), { recursive: true });
renameSync(stagingRoot, outputRoot);
process.stdout.write(
  `${JSON.stringify({
    status: "passed",
    component_count: components.length,
    manifest_sha256: manifestHash,
  })}\n`,
);
