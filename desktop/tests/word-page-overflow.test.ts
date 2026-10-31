import {readFileSync} from 'node:fs';
import {expect,it} from 'vitest';
const source=readFileSync('src/documents/word-editor.js','utf8');
const fitSource=source.slice(source.indexOf('const pageContentSizes='),source.indexOf('function refreshZoom('));
const typography=readFileSync('src/documents/word-typography.js','utf8');
const trailingSpace=new Function('window',`${typography};return window.wordGridTrailingSpacePx;`)({});
it('完整显示超长段落，重复缩放不累加页高，内容缩短后恢复原页高',()=>{
 document.body.innerHTML='<main id="paper"><div class="page-box" style="height:1000px"><div class="page-content" style="height:800px"><p>长段落</p></div><footer style="bottom:20px">页脚</footer></div></main>';
 const paper=document.querySelector('main')!,page=paper.firstElementChild as HTMLElement,content=page.firstElementChild as HTMLElement;let textHeight=1800;
 Object.defineProperty(content,'clientHeight',{get:()=>parseFloat(content.style.height)});
 Object.defineProperty(content,'scrollHeight',{get:()=>Math.max(textHeight,parseFloat(content.style.height))});
 const fit=new Function('paper','wordGridTrailingSpacePx',`${fitSource};return fitPageContents;`)(paper,trailingSpace);
 fit();expect(content.style.height).toBe('1800px');expect(page.style.height).toBe('2000px');
 fit();expect(page.style.height).toBe('2000px');expect((page.lastElementChild as HTMLElement).style.bottom).toBe('20px');
 textHeight=600;fit();expect(content.style.height).toBe('800px');expect(page.style.height).toBe('1000px');
});
it('页末仅忽略最大字号下的网格空白，不忽略图片或真实溢出',()=>{
 document.body.innerHTML='<main><div class="page-box" style="height:120px"><div class="page-content" style="height:100px"><p style="--docx-line-grid:1;--docx-grid-font-px:32;font-size:16px;line-height:40px">混合字号</p></div></div></main>';
 const paper=document.querySelector('main')!,page=paper.firstElementChild as HTMLElement,content=page.firstElementChild as HTMLElement;let textHeight=104;
 Object.defineProperty(content,'clientHeight',{get:()=>parseFloat(content.style.height)});
 Object.defineProperty(content,'scrollHeight',{get:()=>Math.max(textHeight,parseFloat(content.style.height))});
 const fit=new Function('paper','wordGridTrailingSpacePx',`${fitSource};return fitPageContents;`)(paper,trailingSpace);
 fit();expect(page.style.height).toBe('120px');
 textHeight=105;fit();expect(page.style.height).toBe('121px');
 content.querySelector('p')!.append(document.createElement('img'));fit();expect(page.style.height).toBe('125px');
});
