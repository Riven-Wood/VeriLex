// VeriLex filesystem adapter around the MIT QuillPDF page engine.
import { constants } from 'node:fs';
import { open, lstat, realpath, mkdtemp, mkdir, writeFile, rename, rm, rmdir } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { merge, splitByRanges, extractPages, pageCount, parseRanges } from './core.ts';

const MAX_BYTES = 32 * 1024 * 1024;
const MAX_TOTAL = 64 * 1024 * 1024;
const error = (message) => { throw new Error(message); };

export async function operate(root, name, args) {
  const rootPath = await realpath(root);
  const files = name === 'pdf_merge' ? args.inputs : [args.input];
  if (!Array.isArray(files) || files.length < (name === 'pdf_merge' ? 2 : 1) || files.length > 20)
    error('请选择 2 至 20 份 PDF 合并，或一份 PDF 拆分、提取。');
  if (!['pdf_merge', 'pdf_split', 'pdf_extract', 'pdf_page_count'].includes(name)) error('此插件不支持该操作。');
  if (Object.keys(args).some(k => !['inputs', 'input', 'output_name', 'ranges', 'pages'].includes(k)))
    error('参数不受支持；插件不允许覆盖原件。');
  let totalBytes = 0;
  const buffers = [];
  const counts = [];
  for (const file of files) {
    if (typeof file !== 'string' || !file.toLowerCase().endsWith('.pdf') || file.includes('\\') || file.includes('\0'))
      error('请选择文件夹内的 PDF 文件。');
    const relative = path.isAbsolute(file) ? path.relative(rootPath, file) : file;
    const parts = relative.split('/');
    if (!relative || parts.some(p => !p || p === '.' || p === '..')) error('文件必须位于连接时选定的文件夹内。');
    let resolved = rootPath;
    for (const part of parts) {
      resolved = path.join(resolved, part);
      if ((await lstat(resolved)).isSymbolicLink()) error('不处理符号链接，请使用文件夹内的 PDF 原文件。');
    }
    const handle = await open(resolved, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const stat = await handle.stat();
      if (!stat.isFile()) error('请选择普通 PDF 文件。');
      if (stat.size > MAX_BYTES || (totalBytes += stat.size) > MAX_TOTAL) error('PDF 过大：单份最多 32 MB，一次合计最多 64 MB。');
      const bytes = Buffer.alloc(stat.size + 1);
      let used = 0;
      while (used < bytes.length) {
        const { bytesRead } = await handle.read(bytes, used, bytes.length - used, used);
        if (!bytesRead) break;
        used += bytesRead;
      }
      if (used !== stat.size) error('PDF 在读取时发生变化，请稍后重试。');
      if (!bytes.subarray(0, Math.min(1024, used)).includes('%PDF-')) error('文件不是有效的 PDF。');
      const input = bytes.subarray(0, used);
      const count = await pageCount(input);
      if (count < 1 || count > 500) error('每份 PDF 需有 1 至 500 页。');
      buffers.push(input); counts.push(count);
    } finally { await handle.close(); }
  }
  if (name === 'pdf_page_count') return { page_count: counts[0], outputs: [] };
  const outputName = args.output_name;
  if (typeof outputName !== 'string' || !/^[^/\\\x00-\x1f]{1,100}\.pdf$/i.test(outputName) || outputName.startsWith('.'))
    error('请使用不含路径的新 PDF 文件名。');
  let outputs;
  if (name === 'pdf_merge') {
    if (counts.reduce((a,b) => a+b, 0) > 1000) error('一次最多输出 1000 页，请分批处理。');
    outputs = [await merge(buffers)];
  } else {
    const spec = name === 'pdf_split' ? args.ranges : args.pages;
    if (typeof spec !== 'string' || spec.length > 2000 || !/^\s*[1-9]\d*(?:\s*-\s*[1-9]\d*)?(?:\s*,\s*[1-9]\d*(?:\s*-\s*[1-9]\d*)?)*\s*$/.test(spec))
      error('页码格式有误，请填写如 1-3,5；页码从 1 开始。');
    let groups;
    try { groups = parseRanges(spec, counts[0]); } catch { error(`页码超出范围或顺序有误；这份 PDF 共 ${counts[0]} 页。`); }
    if (groups.flat().length > 1000 || (name === 'pdf_split' && groups.length > 50))
      error('一次最多输出 1000 页、50 份文件，请分批处理。');
    outputs = name === 'pdf_split' ? await splitByRanges(buffers[0], spec)
      : [await extractPages(buffers[0], groups.flat().map(n => n + 1).join(','))];
  }
  if (outputs.reduce((sum, b) => sum + b.length, 0) > MAX_TOTAL) error('结果超过 64 MB，请分批处理。');
  // Reopen every output before any publication; create-only output directory.
  const outputCounts = await Promise.all(outputs.map(pageCount));
  const stage = await mkdtemp(path.join(rootPath, '.verilex-pdf-'));
  const resultDir = 'PDF结果-' + randomUUID();
  const destination = path.join(rootPath, resultDir);
  let published = false;
  try {
    const names = outputs.map((_, i) => outputs.length === 1 ? outputName : outputName.replace(/\.pdf$/i, `-${i+1}.pdf`));
    for (let i = 0; i < outputs.length; i++) await writeFile(path.join(stage, names[i]), outputs[i], {flag:'wx', mode:0o600});
    // Reserve the destination exclusively; rename replaces only our empty directory.
    await mkdir(destination, {mode:0o700});
    try { await rename(stage, destination); } catch (e) { await rmdir(destination).catch(() => {}); throw e; }
    published = true;
    return { outputs: names.map((n,i) => ({path:path.join(destination,n), relative_path:resultDir+'/'+n, pages:outputCounts[i]})), originals_preserved:true };
  } finally { if (!published) await rm(stage, {recursive:true, force:true}); }
}

export function userError(err) {
  const text = err instanceof Error ? err.message : '';
  if (/password|encrypt/i.test(text)) return 'PDF 已加密，请先用可信的 PDF 阅读器另存未加密副本。原件未改动。';
  if (err?.code === 'ENOENT') return '没有找到 PDF 文件，请检查所选文件夹和文件名。';
  if (['EACCES','EPERM','EROFS'].includes(err?.code)) return '没有文件夹读写权限，请重新选择可读写的文件夹。';
  if (/[\u4e00-\u9fff]/.test(text)) return text;
  return 'PDF 处理失败，文件可能损坏或包含不支持的结构。原件未改动，请先在 PDF 阅读器中检查。';
}
