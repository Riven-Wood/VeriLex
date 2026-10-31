import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, readdirSync, lstatSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const stagedRoot = resolve(desktopRoot, "public", "documents-renderer");
const generatedRoot = resolve(
  desktopRoot,
  "src-tauri",
  "generated",
  "documents-macos-arm64",
  "renderer",
);

rmSync(stagedRoot, { recursive: true, force: true });
{
  if (!existsSync(generatedRoot)) {
    throw new Error("The embedded Word editor requires the locked renderer. Run npm run build:documents on the documented macOS build host first.");
  }
  const lock = JSON.parse(readFileSync(resolve(desktopRoot, 'src/documents/renderer-lock.json'), 'utf8'));
  const hashes = {};
  const visit = (folder, prefix = '') => {
    for (const name of readdirSync(folder).sort()) {
      const path = resolve(folder, name), key = prefix + name, stat = lstatSync(path);
      if (stat.isSymbolicLink()) throw new Error('Word engine symlink rejected');
      if (stat.isDirectory()) visit(path, key + '/');
      else if (stat.isFile() && key !== 'renderer-lock.json') hashes[key] = createHash('sha256').update(readFileSync(path)).digest('hex');
    }
  };
  visit(generatedRoot);
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(hashes).sort(([a],[b])=>a<b?-1:a>b?1:0)));
  if (Object.keys(hashes).length !== lock.asset_file_count || createHash('sha256').update(canonical).digest('hex') !== lock.asset_tree_sha256) throw new Error('Word engine bundle differs from the locked version');
  for (const asset of lock.assets) {
    const path = resolve(generatedRoot, '..', asset.path);
    if (createHash('sha256').update(readFileSync(path)).digest('hex') !== asset.sha256) throw new Error(`Word engine hash mismatch: ${asset.path}`);
  }
  mkdirSync(stagedRoot, { recursive: true });
  cpSync(generatedRoot, stagedRoot, { recursive: true });
  // The pinned renderer includes deleted revision text in DOM offsets, while
  // the DOCX engine addresses only current text. Exclude visible deletions from
  // editing/serialization so typing after a revision cannot shift its target.
  // Validate the upstream asset first, then apply this explicit local patch.
  const bundlePath = resolve(stagedRoot, 'docxodus.bundle.js');
  const bundle = readFileSync(bundlePath, 'utf8');
  const selector = `var GENERATED_CHROME_SELECTOR = '[data-list-marker], a.footnote-ref, a.endnote-ref, a[class$="-backref"]';`;
  if (bundle.split(selector).length !== 2) throw new Error('Word revision offset adapter does not match the locked engine');
  let patched = bundle.replace(selector, selector.replace('[data-list-marker]', 'del, [class$="para-mark-del"], [data-list-marker]'));
  const splitOffset = 'const offset = trimmedSplitOffset(el, rawOffset);';
  if (patched.split(splitOffset).length !== 2) throw new Error('Word split offset adapter mismatch');
  patched = patched.replace(splitOffset, 'const offset = trimmedSpan(el, { start: rawOffset, length: 0 }).start;');
  // Keyboard sync preserves intentional edge spaces. Only the renderer's
  // known empty-paragraph NBSP is absent from the Word run-text coordinates.
  const spanOffset = `function trimmedSpan(block, span) {
    const { leading, trimmedLen } = trimBounds(block);
    const start2 = Math.max(0, Math.min(span.start - Math.min(span.start, leading), trimmedLen));
    const end = Math.max(start2, Math.min(span.start + span.length - leading, trimmedLen));
    return { start: start2, length: end - start2 };
  }`;
  if (patched.split(spanOffset).length !== 2) throw new Error('Word selection offset adapter mismatch');
  patched = patched.replace(spanOffset, `function trimmedSpan(block, span) {
    const text = blockContentText(block);
    const placeholder = block.dataset.vlEmptyPlaceholder === "true" ? text.indexOf("\\u00a0") : -1;
    const length = text.length - (placeholder < 0 ? 0 : 1);
    const map = offset => Math.max(0, Math.min(offset - (placeholder >= 0 && offset > placeholder ? 1 : 0), length));
    const start2 = map(span.start);
    const end = Math.max(start2, map(span.start + span.length));
    return { start: start2, length: end - start2 };
  }`);
  writeFileSync(bundlePath, patched);
  for (const name of ["word-editor.html", "word-editor.js", "word-typography.js", "word-pagination.js", "word-keyboard.js", "word-clipboard.js", "word-search.js", "word-selection.js", "word-guides.js", "word-context.js", "word-layout.js", "word-editor.css"]) cpSync(resolve(desktopRoot, "src/documents", name), resolve(stagedRoot, name));
  cpSync(
    resolve(desktopRoot, "src", "documents", "renderer-self-test.html"),
    resolve(stagedRoot, "renderer-self-test.html"),
  );
  cpSync(
    resolve(desktopRoot, "src", "documents", "renderer-self-test.js"),
    resolve(stagedRoot, "renderer-self-test.js"),
  );
}
