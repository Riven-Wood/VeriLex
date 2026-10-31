import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(desktopRoot, "..");
const python =
  process.env.VERILEX_PYTHON ??
  resolve(
    repositoryRoot,
    "runtime",
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );

if (!existsSync(python)) {
  throw new Error(
    `VeriLex Python environment not found at ${python}. Create runtime/.venv and install runtime[dev].`,
  );
}

const legacyReaderBuild = spawnSync(python, [resolve(repositoryRoot, "scripts/documents/build_legacy_doc_reader.py")], {stdio: "inherit"});
if (legacyReaderBuild.status !== 0) throw new Error("Legacy DOC reader build failed");
const legacyReaderArgs = process.platform === "darwin" && process.arch === "arm64"
  ? ["--add-data", `${resolve(desktopRoot, "src-tauri/generated/legacy-reader-macos-arm64")}:verilex_runtime/attachments/legacy_reader`]
  : [];

// Build the actual upstream-based MCP, never require npm/node installation by end users.
const imaBuild = spawnSync(process.execPath, [resolve(desktopRoot, "scripts/build-ima-mcp.mjs")], {stdio: "inherit"});
if (imaBuild.status !== 0) throw new Error("ima MCP bundle failed");
const pdfBuild = spawnSync(process.execPath, [resolve(desktopRoot, "scripts/build-pdf-plugin.mjs")], {stdio: "inherit"});
if (pdfBuild.status !== 0) throw new Error("PDF MCP bundle failed");
const imaAssets = resolve(repositoryRoot, "runtime/verilex_runtime/knowledge/ima_mcp");
const tauriRoot = resolve(desktopRoot, "src-tauri");
const sidecarRoot = resolve(tauriRoot, "sidecar");
const pyinstallerRoot = resolve(tauriRoot, "target", "pyinstaller");
mkdirSync(sidecarRoot, { recursive: true });
mkdirSync(pyinstallerRoot, { recursive: true });
// python-docx 1.2 opens parts/../templates/default-comments.xml without
// normalizing it. Frozen modules live in PYZ, so parts/ otherwise does not
// exist on disk and creating the first comment fails despite bundled XML.
const docxPartsMarker = resolve(pyinstallerRoot, "docx-parts-directory.txt");
writeFileSync(docxPartsMarker, "Keep docx/parts available for python-docx template paths.\n");

const documentManifest = resolve(
  tauriRoot,
  "generated",
  "documents-macos-arm64",
  "component-manifest.json",
);
const documentTrustHook = resolve(pyinstallerRoot, "document-manifest-trust.py");
const documentBuildArgs = [];
const overlaySelected = (process.env.TAURI_CONFIG ?? "").includes(
  "generated/documents-macos-arm64",
);
if (overlaySelected) {
  if (!existsSync(documentManifest)) {
    throw new Error("Documents overlay requires the generated component manifest.");
  }
  const manifestBytes = readFileSync(documentManifest);
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  if (JSON.stringify(manifest) !== manifestBytes.toString("utf8")) {
    throw new Error("Documents component manifest is not canonical JSON.");
  }
  const manifestSha256 = createHash("sha256").update(manifestBytes).digest("hex");
  writeFileSync(
    documentTrustHook,
    `import verilex_runtime.documents.bundle_lock as _bundle_lock\n_bundle_lock._COMPILED_MANIFEST_SHA256 = ${JSON.stringify(manifestSha256)}\n`,
  );
  documentBuildArgs.push("--runtime-hook", documentTrustHook);
} else {
  rmSync(documentTrustHook, { force: true });
}

const result = spawnSync(
  python,
  [
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "verilex-runtime",
    "--distpath",
    sidecarRoot,
    "--workpath",
    resolve(pyinstallerRoot, "work"),
    "--specpath",
    pyinstallerRoot,
    "--add-data", `${resolve(repositoryRoot, "third_party/file_redaction")}:third_party/file_redaction`,
    ...legacyReaderArgs,
    // Offline redaction: include the installed pinned model and tokenizer assets.
    "--collect-all", "zh_core_web_sm",
    "--collect-all", "spacy",
    "--collect-all", "spacy_pkuseg",
    "--collect-all", "thinc",
    "--copy-metadata", "spacy",
    "--copy-metadata", "zh-core-web-sm",
    // Only the transports we use; a bare "mcp" collect would walk mcp.cli,
    // which imports the optional "typer" CLI extra the runtime never uses.
    "--collect-submodules",
    "mcp.client",
    // IANA timezone fallback is required on Windows and other hosts without zoneinfo files.
    "--collect-all", "tzdata",
    // Retain the diagnostic CLI path; normal packaged stdio uses the small sibling helper.
    "--hidden-import",
    "verilex_runtime.mcp.watchdog",
    // The search provider is registered by a later task; include this narrow
    // module now so Task 3's sidecar check validates the actual adapter.
    "--hidden-import",
    "verilex_runtime.web.provider",
    // Content extraction is wired into the research pipeline by a later task,
    // so include it now together with html5lib's dynamically selected builder.
    "--hidden-import",
    "verilex_runtime.web.extract",
    "--hidden-import",
    "html5lib.treebuilders.etree",
    // DOCX extraction is a dynamic smoke entry as well as an API dependency.
    "--hidden-import",
    "verilex_runtime.attachments.docx",
    "--hidden-import",
    "verilex_runtime.documents.v1_creator",
    "--hidden-import",
    "verilex_runtime.documents.v1_markdown",
    "--collect-data", "pptx",
    // python-pptx opens oxml/../templates; the intermediate directory must exist when frozen.
    "--add-data", `${docxPartsMarker}:pptx/oxml`,
    "--collect-data", "verilex_runtime.ppt_plugin.vendor",
    "--collect-submodules",
    "docx",
    "--add-data", `${docxPartsMarker}:docx/parts`,
    "--collect-data",
    "verilex_runtime.litigation",
    "--hidden-import",
    "lxml.etree",
    "--add-data", `${imaAssets}:verilex_runtime/knowledge/ima_mcp`,
    "--add-data", `${resolve(repositoryRoot, "runtime/verilex_runtime/pdf_plugin")}:verilex_runtime/pdf_plugin`,
    "--add-binary", `${resolve(imaAssets, process.platform === "win32" ? "node.exe" : "node")}:verilex_runtime/knowledge/ima_mcp`,
    ...documentBuildArgs,
    resolve(repositoryRoot, "runtime", "verilex_runtime", "sidecar.py"),
  ],
  {
    cwd: repositoryRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      PYINSTALLER_CONFIG_DIR: resolve(pyinstallerRoot, "config"),
    },
  },
);

if (result.error) throw result.error;
if (result.status !== 0) {
  throw new Error(`PyInstaller exited with status ${result.status ?? "unknown"}`);
}


// Compile the existing stdlib-only watchdog directly, avoiding package __init__
// and the large offline NLP payload on every local MCP connection. The whole
// sidecar directory is already a Tauri bundle resource.
if (process.platform !== "win32") {
  const watchdog = spawnSync(python, [
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
    "--name", "verilex-mcp-watchdog", "--distpath", sidecarRoot,
    "--workpath", resolve(pyinstallerRoot, "watchdog-work"), "--specpath", pyinstallerRoot,
    resolve(repositoryRoot, "runtime/verilex_runtime/mcp/watchdog.py"),
  ], {
    cwd: repositoryRoot, stdio: "inherit",
    env: { ...process.env, PYINSTALLER_CONFIG_DIR: resolve(pyinstallerRoot, "config") },
  });
  if (watchdog.error) throw watchdog.error;
  if (watchdog.status !== 0) throw new Error(`MCP watchdog build failed: ${watchdog.status}`);
}

// onedir has no supervising bootloader process: posix_spawn's PID remains the
// PTY session leader when the bootstrap execs the sandboxed shell.
if (process.platform === "darwin") {
  const terminalHost = spawnSync(python, [
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
    "--name", "verilex-terminal-host", "--distpath", sidecarRoot,
    "--workpath", resolve(pyinstallerRoot, "terminal-work"), "--specpath", pyinstallerRoot,
    resolve(repositoryRoot, "runtime/verilex_runtime/terminal_host.py"),
  ], {
    cwd: repositoryRoot, stdio: "inherit",
    env: { ...process.env, PYINSTALLER_CONFIG_DIR: resolve(pyinstallerRoot, "config") },
  });
  if (terminalHost.error) throw terminalHost.error;
  if (terminalHost.status !== 0) throw new Error(`Terminal host build failed: ${terminalHost.status}`);
}

// Small self-contained local Excel MCP; users do not install Python or Excel.
const excelPlugin = spawnSync(python, [
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
  "--name", "verilex-excel-plugin", "--distpath", sidecarRoot,
  "--workpath", resolve(pyinstallerRoot, "excel-work"), "--specpath", pyinstallerRoot,
  "--exclude-module", "verilex_runtime.app", "--exclude-module", "verilex_runtime.settings",
  "--collect-data", "verilex_runtime.excel_plugin.vendor",
  "--add-data", `${resolve(repositoryRoot, "third_party/licenses/excel-plugin")}:licenses/excel-plugin`,
  resolve(repositoryRoot, "runtime/verilex_runtime/excel_plugin/entry.py"),
], { cwd: repositoryRoot, stdio: "inherit",
  env: { ...process.env, PYINSTALLER_CONFIG_DIR: resolve(pyinstallerRoot, "config") },
});
if (excelPlugin.error) throw excelPlugin.error;
if (excelPlugin.status !== 0) throw new Error(`Excel plugin build failed: ${excelPlugin.status}`);
