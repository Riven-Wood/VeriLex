import {readFileSync} from 'node:fs';
import {afterEach,describe,it,expect,vi} from 'vitest';
const source=readFileSync('src/documents/word-selection.js','utf8');
let controls:{dispose:()=>void;flushPending:()=>void};
function setup(){
 document.body.innerHTML='<main><p data-anchor="a" contenteditable="true">甲方合同</p><p data-anchor="b" contenteditable="true">乙方合同</p><span class="page-number">1</span></main>';
 const paper=document.querySelector('main')!,blocks=[...paper.querySelectorAll('p')];
 const editor={activeBlock:blocks[0],wireBlock:vi.fn(),anchorIdOf:(b:HTMLElement)=>b.dataset.anchor,syncBlock:vi.fn(),onKeydown:vi.fn(),clearDragSelection:vi.fn()};
 controls=new Function(source+';return window.installWordSelection;')()(editor,paper);
 function select(block:HTMLElement,offset=1){const r=document.createRange();r.setStart(block.firstChild!,offset);r.collapse(true);getSelection()!.removeAllRanges();getSelection()!.addRange(r);document.dispatchEvent(new Event('selectionchange'));}
 return {paper,blocks,editor,select};
}
afterEach(()=>{controls?.dispose();vi.restoreAllMocks();getSelection()?.removeAllRanges();});
describe('shared native Word editing host',()=>{
 it('uses a continuous host while preserving addressable and focusable paragraph anchors',()=>{const {paper,blocks,editor}=setup();expect(paper.contentEditable).toBe('true');expect(paper.getAttribute('aria-multiline')).toBe('true');expect(blocks.every(b=>b.tabIndex===-1&&b.getAttribute('contenteditable')==='true')).toBe(true);const fresh=document.createElement('p');fresh.setAttribute('data-anchor','c');fresh.setAttribute('contenteditable','true');editor.wireBlock(fresh);expect(fresh.tabIndex).toBe(-1);});
 it('flushes pending typing in every edited paragraph when the caret moves before autosave',()=>{const {paper,blocks,editor,select}=setup();select(blocks[0]);paper.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertText'}));blocks[0].textContent+='甲';paper.dispatchEvent(new InputEvent('input',{bubbles:true}));select(blocks[1]);paper.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertText'}));blocks[1].textContent+='乙';paper.dispatchEvent(new InputEvent('input',{bubbles:true}));expect(editor.activeBlock).toBe(blocks[1]);controls.flushPending();expect(editor.syncBlock.mock.calls).toEqual([[blocks[0],'a'],[blocks[1],'b']]);controls.flushPending();expect(editor.syncBlock).toHaveBeenCalledTimes(2);});
 it('routes surface keyboard events to the paragraph at the caret without duplicating handled commands',()=>{const {paper,blocks,editor,select}=setup();select(blocks[1]);const tab=new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true});paper.dispatchEvent(tab);expect(editor.onKeydown).toHaveBeenCalledWith(blocks[1],tab);const handled=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});handled.preventDefault();paper.dispatchEvent(handled);expect(editor.onKeydown).toHaveBeenCalledTimes(1);});
 it('blocks native input into page chrome outside anchored text',()=>{const {paper}=setup();const number=paper.querySelector('span')!,r=document.createRange();r.selectNodeContents(number);r.collapse(true);getSelection()!.removeAllRanges();getSelection()!.addRange(r);const event=new InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'insertText',data:'x'});paper.dispatchEvent(event);expect(event.defaultPrevented).toBe(true);});
 it('does not submit detached paragraphs after a structural command replaces them',()=>{const {paper,blocks,editor,select}=setup();select(blocks[0]);paper.dispatchEvent(new InputEvent('input',{bubbles:true}));blocks[0].remove();controls.flushPending();expect(editor.syncBlock).not.toHaveBeenCalled();});
 it('normalizes a selection ending in page chrome to the last real paragraph',()=>{const {paper,blocks}=setup();const range=document.createRange();range.setStart(paper,0);range.setEnd(paper,paper.childNodes.length);getSelection()!.removeAllRanges();getSelection()!.addRange(range);document.dispatchEvent(new Event('selectionchange'));expect(getSelection()!.toString()).toBe('甲方合同乙方合同');expect(getSelection()!.focusNode).toBe(blocks[1]);});

});

describe('bounded mouse selection',()=>{
 function mouseHarness(){
  const h=setup();
  Object.defineProperty(h.paper,'getBoundingClientRect',{value:()=>({left:0,right:400,top:0,bottom:200,width:400,height:200})});
  h.blocks.forEach((b,i)=>Object.defineProperty(b,'getBoundingClientRect',{value:()=>({left:100,right:300,top:20+i*50,bottom:40+i*50,width:200,height:20})}));
  Object.defineProperty(document,'caretRangeFromPoint',{configurable:true,value:vi.fn((x:number,y:number)=>{
   const block=x>300?h.blocks[0]:h.blocks[y<60?0:1];
   const r=document.createRange();r.setStart(block.firstChild!,x>300?0:Math.max(0,Math.min(4,Math.round((x-100)/50))));r.collapse(true);return r;
  })});
  const mouse=(target:EventTarget,type:string,x:number,y:number,extra:MouseEventInit={})=>{const event=new MouseEvent(type,{bubbles:true,cancelable:true,button:0,buttons:type==='mouseup'?0:1,detail:1,clientX:x,clientY:y,...extra});target.dispatchEvent(event);return event;};
  return {...h,mouse};
 }
 it('right margin clamps to the nearby line without replacing a middle-paragraph anchor with document start',()=>{
  const h=mouseHarness();h.mouse(h.blocks[1],'mousedown',200,80);h.mouse(document,'mousemove',390,80);document.dispatchEvent(new Event('selectionchange'));h.mouse(document,'mouseup',390,80);
  expect(getSelection()!.toString()).toBe('合同');expect(getSelection()!.anchorNode).toBe(h.blocks[1].firstChild);expect(getSelection()!.anchorOffset).toBe(2);
 });
 it('left margin keeps a forward drag in the selected paragraph instead of selecting previous paragraphs',()=>{
  const h=mouseHarness();h.mouse(h.blocks[1],'mousedown',200,80);h.mouse(document,'mouseup',5,80);expect(getSelection()!.toString()).toBe('乙方');expect(getSelection()!.anchorOffset).toBe(2);
 });
 it('both directions can still select across paragraphs with precise endpoints',()=>{
  const h=mouseHarness();h.mouse(h.blocks[0],'mousedown',200,30);h.mouse(document,'mouseup',200,80);expect(getSelection()!.toString()).toBe('合同乙方');
  h.mouse(h.blocks[1],'mousedown',200,80);h.mouse(document,'mouseup',200,30);expect(getSelection()!.toString()).toBe('合同乙方');expect(getSelection()!.anchorNode).toBe(h.blocks[1].firstChild);
 });
 it('blank-page clicks do not replace an existing selection; explicit whole-document selection still works',()=>{
  const h=mouseHarness();h.select(h.blocks[1],2);expect(h.mouse(h.paper,'mousedown',10,150).defaultPrevented).toBe(true);expect(getSelection()!.anchorNode).toBe(h.blocks[1].firstChild);
  const range=document.createRange();range.selectNodeContents(h.paper);getSelection()!.removeAllRanges();getSelection()!.addRange(range);document.dispatchEvent(new Event('selectionchange'));expect(getSelection()!.toString()).toBe('甲方合同乙方合同');
 });
 it('shift click extends the existing anchor; right click and double click remain native',()=>{
  const h=mouseHarness();h.select(h.blocks[0],2);h.mouse(h.blocks[1],'mousedown',200,80,{shiftKey:true});h.mouse(document,'mouseup',200,80);expect(getSelection()!.toString()).toBe('合同乙方');
  expect(h.mouse(h.blocks[1],'mousedown',200,80,{button:2}).defaultPrevented).toBe(false);expect(h.mouse(h.blocks[1],'mousedown',200,80,{detail:2}).defaultPrevented).toBe(false);
 });
 it('losing focus ends the gesture so later pointer motion cannot change the selection',()=>{
  const h=mouseHarness();h.mouse(h.blocks[1],'mousedown',200,80);window.dispatchEvent(new Event('blur'));h.mouse(document,'mousemove',100,30);expect(getSelection()!.isCollapsed).toBe(true);
 });
});
