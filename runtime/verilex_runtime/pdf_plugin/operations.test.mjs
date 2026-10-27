import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, readdir, symlink, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { PDFDocument } from 'pdf-lib';
import { operate, userError } from './operations.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(),'verilex-pdf-test-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const a = await PDFDocument.create();
  [201,202,203,204].forEach(width=>a.addPage([width,300]));
  const b = await PDFDocument.create(); b.addPage([401,500]);
  const originals = [Buffer.from(await a.save()),Buffer.from(await b.save())];
  await writeFile(path.join(root,'甲.pdf'),originals[0]);
  await writeFile(path.join(root,'乙.pdf'),originals[1]);
  return {root,originals};
}
async function widths(result) {
  return Promise.all(result.outputs.map(async o=>(await PDFDocument.load(await readFile(o.path))).getPages().map(p=>p.getWidth())));
}
test('real merge, split, extract preserve order and exact original bytes',async t=>{
  const {root,originals}=await fixture(t);
  const merged=await operate(root,'pdf_merge',{inputs:['乙.pdf','甲.pdf'],output_name:'合并.pdf'});
  assert.deepEqual(await widths(merged),[[401,201,202,203,204]]);
  const split=await operate(root,'pdf_split',{input:'甲.pdf',ranges:'1-2,3-4',output_name:'拆分.pdf'});
  assert.deepEqual(await widths(split),[[201,202],[203,204]]);
  const extract=await operate(root,'pdf_extract',{input:'甲.pdf',pages:'4,1-2,1',output_name:'甲.pdf'});
  assert.deepEqual(await widths(extract),[[204,201,202,201]]);
  assert.deepEqual(await readFile(path.join(root,'甲.pdf')),originals[0]);
  assert.deepEqual(await readFile(path.join(root,'乙.pdf')),originals[1]);
  const again=await operate(root,'pdf_extract',{input:'甲.pdf',pages:'1',output_name:'甲.pdf'});
  assert.notEqual(extract.outputs[0].path,again.outputs[0].path);
  const reused=await operate(root,'pdf_page_count',{input:again.outputs[0].relative_path});
  assert.equal(reused.page_count,1);
});
test('invalid page specs never create even a partial result',async t=>{
  const {root}=await fixture(t);
  for (const pages of ['0','5','2-1','1-2-3','1,,2','1,','1e0','1,../../x','']) {
    await assert.rejects(()=>operate(root,'pdf_extract',{input:'甲.pdf',pages,output_name:'结果.pdf'}));
  }
  await assert.rejects(()=>operate(root,'pdf_split',{input:'甲.pdf',ranges:'1-2,999',output_name:'结果.pdf'}));
  assert.deepEqual((await readdir(root)).sort(),['乙.pdf','甲.pdf']);
});
test('reject traversal, symlinks, source replacement option and corrupted input',async t=>{
  const {root}=await fixture(t);
  await symlink(path.join(root,'甲.pdf'),path.join(root,'链接.pdf'));
  for(const input of ['../甲.pdf','链接.pdf','/etc/passwd.pdf'])
    await assert.rejects(()=>operate(root,'pdf_page_count',{input}));
  await assert.rejects(()=>operate(root,'pdf_extract',{input:'甲.pdf',pages:'1',output_name:'../甲.pdf'}));
  await assert.rejects(()=>operate(root,'pdf_extract',{input:'甲.pdf',pages:'1',output_name:'甲.pdf',force:true}));
  await writeFile(path.join(root,'坏.pdf'),'%PDF- broken');
  await assert.rejects(()=>operate(root,'pdf_page_count',{input:'坏.pdf'}));
  assert.equal((await readdir(root)).filter(n=>n.startsWith('PDF结果')||n.startsWith('.verilex')).length,0);
});
test('bounded input and output; Chinese recoverable errors',async t=>{
  const {root}=await fixture(t);
  await writeFile(path.join(root,'大.pdf'),Buffer.alloc(32*1024*1024+1));
  await assert.rejects(()=>operate(root,'pdf_page_count',{input:'大.pdf'}),/32 MB/);
  await assert.rejects(()=>operate(root,'pdf_split',{input:'甲.pdf',ranges:Array(51).fill('1').join(','),output_name:'结果.pdf'}),/50/);
  assert.match(userError(new Error('encrypted')),/加密.*原件未改动/);
  assert.match(userError(Object.assign(new Error(),{code:'ENOENT'})),/没有找到/);
  assert.match(userError(new Error('private parser trace')),/可能损坏/);
});
