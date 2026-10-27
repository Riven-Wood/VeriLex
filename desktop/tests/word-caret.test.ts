import {readFileSync} from 'node:fs';
import {afterEach,expect,it,vi} from 'vitest';
const source=readFileSync('src/documents/word-editor.js','utf8');
const rectFor=new Function(`${source.slice(source.indexOf('function textCaretRect('),source.indexOf('function refreshTextCaret('))};return textCaretRect;`)();
Object.defineProperty(Range.prototype,'getClientRects',{configurable:true,writable:true,value:()=>[]});
afterEach(()=>vi.restoreAllMocks());
const rect=(x:number,y:number,w:number,h:number)=>({left:x,right:x+w,top:y,bottom:y+h,width:w,height:h});
function setup(html:string){document.body.innerHTML=`<p>${html}</p>`;return document.querySelector('p')!;}
it('Mac大行距中的光标使用文字高度，保留原生横向位置',()=>{
 const p=setup('甲方付款'),n=p.firstChild!,range=document.createRange();range.setStart(n,2);range.collapse(true);
 vi.spyOn(Range.prototype,'getClientRects').mockImplementation(function(this:Range){return [this.collapsed?rect(70,0,0,104):rect(40+this.startOffset*15,80,15,24)] as any;});
 expect(rectFor(range,p)).toEqual({left:70,top:80,height:24});
 expect(p.innerHTML).toBe('甲方付款');expect(range.startOffset).toBe(2);
});
it('空折叠矩形仍定位当前字符边缘并跳过隐藏修订',()=>{
 const p=setup('甲<del>隐去</del><span>付款</span>'),n=p.querySelector('span')!.firstChild!,range=document.createRange();range.setStart(n,1);range.collapse(true);
 vi.spyOn(Range.prototype,'getClientRects').mockImplementation(function(this:Range){return (this.collapsed?[]:[rect(50+this.startOffset*20,120,20,28)]) as any;});
 expect(rectFor(range,p)).toEqual({left:70,top:120,height:28});
});
it('自然换行的行首遵循原生光标所在行，避免误用上一行坐标',()=>{
 const p=setup('甲乙丙丁'),n=p.firstChild!,range=document.createRange();range.setStart(n,2);range.collapse(true);
 vi.spyOn(Range.prototype,'getClientRects').mockImplementation(function(this:Range){return [this.collapsed?rect(10,24,0,100):this.startOffset<2?rect(180,0,20,24):rect(10,100,20,24)] as any;});
 expect(rectFor(range,p)).toEqual({left:10,top:100,height:24});
});
it('无可见文字时保持原生光标；不改正文以补光标',()=>{
 const p=setup('<br>'),range=document.createRange();range.setStart(p,0);range.collapse(true);
 expect(rectFor(range,p)).toBeNull();expect(p.innerHTML).toBe('<br>');
});
