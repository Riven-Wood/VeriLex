import {readFileSync} from 'node:fs';
import {describe,it,expect} from 'vitest';
const source=readFileSync('src/documents/word-editor.js','utf8');
const safeCss=new Function(source.slice(source.indexOf('function safeCss('),source.indexOf('function safeHtml('))+';return safeCss;')();
describe('paragraph revision geometry',()=>{
 it('shows inserted runs using font color without generated underline or background',()=>{
  const css=safeCss('ins.rev-ins {text-decoration: underline;color:#006400;background-color: #e6ffe6;} .document-run{text-decoration:underline;}');
  expect(css).toContain('text-decoration:none;');expect(css).toContain('background-color:transparent;');expect(css).toContain('color:#006400;');expect(css).toContain('.document-run{text-decoration:underline;}');
 });
 it.each(['ins','del'])('keeps the %s marker outside paragraph layout',kind=>{
  const css=safeCss(`.rev-para-${kind} {border-left: 3px solid #006400; padding-left: 4px;}`);
  expect(css).toContain('box-shadow:-3px 0 0 #006400');expect(css).not.toContain('border-left');expect(css).not.toContain('padding-left');
 });
 it('preserves document borders, indents and unrelated revision decorations',()=>{
  const css='.document-paragraph {border-left: 3px solid #006400;padding-left:4px;text-indent:2em;} .rev-format-change {border-bottom:2px dotted orange;}';
  expect(safeCss(css)).toBe(css);
 });
});

it('compensates automatic lh spacing while retaining exact spacing and font metrics',()=>{
 expect(safeCss('.a{line-height:calc(1lh * var(--docx-auto-line-spacing));}.b{line-height:24pt}')).toBe('.a{line-height:calc(1lh * var(--docx-auto-line-spacing) / var(--verilex-lh-zoom, 1));}.b{line-height:24pt}');
});

it('applies spacing correction to the inline styles emitted by the real Word engine',()=>{
 const rules=new Set<string>();
 const render=new Function('document','cssRules','inlineClasses','sheet', 'let appliedCssRuleCount=0;'+source.slice(source.indexOf('function safeCss('),source.indexOf('function exportsForEditor('))+';return safeHtml;')(document,rules,new Map(),{replaceSync:()=>{}});
 const html=render('<p style="--docx-auto-line-spacing:1.5"><span style="line-height:calc(1lh * var(--docx-auto-line-spacing))">正文</span></p>');
 expect(html).toContain('正文');expect([...rules].join('')).toContain('var(--verilex-lh-zoom, 1)');
});

it('keeps text following empty XML spans outside dynamic page fields',()=>{
 const render=new Function('document','cssRules','inlineClasses','sheet', 'let appliedCssRuleCount=0;'+source.slice(source.indexOf('function safeCss('),source.indexOf('function exportsForEditor('))+';return safeHtml;')(document,new Set(),new Map(),{replaceSync:()=>{}});
 const div=document.createElement('div');
 div.innerHTML=render('<p>第 <span data-field="PAGE"><span style="font-size:9pt"/><span>7</span><span/></span> 页 共 <span data-field="NUMPAGES"><span/><span>7</span><span/></span> 页</p>');
 div.querySelector('[data-field="PAGE"]')!.textContent='2';
 div.querySelector('[data-field="NUMPAGES"]')!.textContent='8';
 expect(div.textContent).toBe('第 2 页 共 8 页');
});
