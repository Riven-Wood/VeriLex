import {readFileSync} from 'node:fs';
import {afterEach,expect,it,vi} from 'vitest';
const source=readFileSync('src/documents/word-guides.js','utf8');
const rect={left:40,right:60,top:40,bottom:60,width:20,height:20};
let guides:{refresh:()=>void;dispose:()=>void}|undefined;
afterEach(()=>{guides?.dispose();vi.restoreAllMocks();vi.unstubAllGlobals();});
function setup(content:string){
 document.body.innerHTML=`<button id="toggle" aria-pressed="true"></button><div id="surface"><div id="paper"><div class="page-box"><div class="page-content">${content}</div></div></div></div>`;
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));vi.stubGlobal('cancelAnimationFrame',vi.fn());
 vi.spyOn(Element.prototype,'getBoundingClientRect').mockReturnValue(rect as DOMRect);
 Object.defineProperty(Range.prototype,'getClientRects',{configurable:true,value:()=>[rect]});
 new Function(source)();
 const paper=document.getElementById('paper')!,surface=document.getElementById('surface')!,button=document.getElementById('toggle')!;
 guides=(window as any).installWordGuides({paper,surface,scrollport:surface,button});guides!.refresh();
 return{paper,surface,button,layer:surface.querySelector('#word-editing-guides')!};
}
it('编辑标记与页角独立于正文，保留选区、文本和保存用 HTML',()=>{
 const {paper,button,layer}=setup('<p data-anchor="p1" contenteditable="true">甲 方<span data-docx-tab="true">\t</span>乙<br>丙</p><p data-anchor="p2" contenteditable="true" data-vl-empty-placeholder="true">&nbsp;</p>');
 const html=paper.innerHTML,range=document.createRange();range.selectNodeContents(paper.querySelector('p')!);window.getSelection()!.addRange(range);
 guides!.refresh();expect(layer.textContent).toBe('·→↲↵↵');expect(layer.querySelectorAll('path')).toHaveLength(1);
 expect(layer.closest('#paper')).toBeNull();expect(paper.innerHTML).toBe(html);expect(window.getSelection()!.getRangeAt(0)).toBe(range);
 button.click();guides!.refresh();expect(layer.textContent).toBe('');expect(layer.querySelectorAll('path')).toHaveLength(1);expect(button.getAttribute('aria-pressed')).toBe('false');expect(paper.innerHTML).toBe(html);
 button.click();guides!.refresh();expect(layer.textContent).toBe('·→↲↵↵');
});
it('不标记删除修订、不可编辑编号与脚注引用中的空格',()=>{
 const {layer}=setup('<p data-anchor="p1" contenteditable="true"><span data-list-marker>1. </span>甲<del>删 除</del><span contenteditable="false">只 读</span><a class="footnote-ref">引 用</a>乙</p>');
 expect(layer.textContent).toBe('↵');
});
it('随正文变化重新定位，不把自然折行当成段落结束',()=>{
 const {paper,layer}=setup('<p data-anchor="p1" contenteditable="true">甲乙丙</p>');
 expect(layer.textContent).toBe('↵');paper.querySelector('p')!.append(' 丁');guides!.refresh();expect(layer.textContent).toBe('·↵');
});
