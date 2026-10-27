import {existsSync,readFileSync} from 'node:fs';
import {describe,it,expect} from 'vitest';

// Execute the exact source adapter used by the build, without requiring ignored
// generated assets on a clean checkout. DOM Range supplies the real selection.
const prepare=readFileSync('scripts/prepare-document-frontend.mjs','utf8');
const adapterStart=prepare.indexOf('const spanOffset = ');
const adapterEnd=prepare.indexOf('writeFileSync(bundlePath, patched);',adapterStart);
if(adapterStart<0||adapterEnd<0)throw new Error('Word selection adapter missing');
const adapterSource=prepare.slice(adapterStart,adapterEnd);
const originalLiteral=adapterSource.match(/^const spanOffset = (`[\s\S]*?`);/)?.[1];
if(!originalLiteral)throw new Error('Word selection source guard missing');
const originalSource=new Function(`return ${originalLiteral};`)() as string;
const patch=new Function('patched',`${adapterSource};return patched;`);
const functionSource=patch(originalSource);
const mapSpan=new Function('blockContentText',`${functionSource};return trimmedSpan;`)((block:HTMLElement)=>block.textContent||'');
const splitReplacement=prepare.match(/patched = patched\.replace\(splitOffset, ('[^']*')\);/)?.[1];
if(!splitReplacement)throw new Error('Word split adapter missing');
const splitSource=new Function(`return ${splitReplacement};`)() as string;
const splitOffset=new Function('trimmedSpan','el','rawOffset',`${splitSource};return offset;`);
function selectedSpan(text:string,start:number,end:number,placeholder=false){
 const block=document.createElement('p');block.textContent=text;block.dataset.vlEmptyPlaceholder=String(placeholder);
 const range=document.createRange();range.setStart(block.firstChild!,start);range.setEnd(block.firstChild!,end);
 const prefix=range.cloneRange();prefix.selectNodeContents(block);prefix.setEnd(range.startContainer,range.startOffset);
 return {block,selected:range.toString(),span:mapSpan(block,{start:prefix.toString().length,length:range.toString().length})};
}
describe('Word selection offsets preserve document spaces',()=>{
 it('rejects upstream source drift or an ambiguous replacement',()=>{expect(()=>patch('function trimmedSpan() {}')).toThrow('Word selection offset adapter mismatch');expect(()=>patch(originalSource+originalSource)).toThrow('Word selection offset adapter mismatch');});
 it('formats the selected character after leading spaces, not an earlier space',()=>{const {block,selected,span}=selectedSpan('  甲乙丙',3,4);expect(selected).toBe('乙');expect(span).toEqual({start:3,length:1});expect(block.textContent!.slice(span.start,span.start+span.length)).toBe(selected);});
 it('retains intentionally selected leading and trailing spaces',()=>{expect(selectedSpan('  甲乙  ',0,6).span).toEqual({start:0,length:6});expect(selectedSpan('   ',1,3).span).toEqual({start:1,length:2});});
 it('excludes only a known empty paragraph placeholder',()=>{expect(selectedSpan('\u00a0甲乙',1,3,true).span).toEqual({start:0,length:2});expect(selectedSpan('甲乙\u00a0',0,3,true).span).toEqual({start:0,length:2});expect(selectedSpan('甲\u00a0乙',1,2).span).toEqual({start:1,length:1});});
 it('Enter uses the same placeholder-aware offset even after text sync has committed',()=>{const {block}=selectedSpan('\u00a0换行前',4,4,true);block.dataset.committedText='\u00a0换行前';expect(splitOffset(mapSpan,block,4)).toBe(3);expect(splitOffset(mapSpan,selectedSpan(' \u00a0换行前',5,5).block,5)).toBe(5);expect(splitOffset(mapSpan,selectedSpan('换行前\u00a0',3,3,true).block,3)).toBe(3);});
 it.skipIf(!existsSync('public/documents-renderer/docxodus.bundle.js'))('matches the locally staged editor when its generated bundle is available',()=>{expect(readFileSync('public/documents-renderer/docxodus.bundle.js','utf8')).toContain(functionSource);});
});
