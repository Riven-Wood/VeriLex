import {readFileSync} from 'node:fs';
import {expect,it} from 'vitest';
const source=readFileSync('src/documents/word-editor.js','utf8');
const fitSource=source.slice(source.indexOf('const pageContentSizes='),source.indexOf('function refreshZoom('));
it('完整显示超长段落，重复缩放不累加页高，内容缩短后恢复原页高',()=>{
 document.body.innerHTML='<main id="paper"><div class="page-box" style="height:1000px"><div class="page-content" style="height:800px"><p>长段落</p></div><footer style="bottom:20px">页脚</footer></div></main>';
 const paper=document.querySelector('main')!,page=paper.firstElementChild as HTMLElement,content=page.firstElementChild as HTMLElement;let textHeight=1800;
 Object.defineProperty(content,'clientHeight',{get:()=>parseFloat(content.style.height)});
 Object.defineProperty(content,'scrollHeight',{get:()=>Math.max(textHeight,parseFloat(content.style.height))});
 const fit=new Function('paper',`${fitSource};return fitPageContents;`)(paper);
 fit();expect(content.style.height).toBe('1800px');expect(page.style.height).toBe('2000px');
 fit();expect(page.style.height).toBe('2000px');expect((page.lastElementChild as HTMLElement).style.bottom).toBe('20px');
 textHeight=600;fit();expect(content.style.height).toBe('800px');expect(page.style.height).toBe('1000px');
});
