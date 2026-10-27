import {expect,test,vi} from 'vitest';
import {diagramViewport} from '../src/platform/diagramViewport';
for(const content of [
 '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
 '<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
 '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:red"/></svg>',
 '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://example.com/x)"/></svg>',
 '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="u\\72l(https://example.com/x)"/></svg>',
 '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
]) test(`active or unsupported SVG cannot enter the measurement DOM: ${content}`,async()=>{
 const before=document.body.childElementCount;
 await expect(diagramViewport(btoa(content))).rejects.toThrow();
 expect(document.body.childElementCount).toBe(before);
});

// jsdom has no SVG layout engine. Only geometry is stubbed; XML validation,
// DOM insertion, serialization and cleanup all exercise the production path.
test('long-text alignment survives preview preparation without changing the original',async()=>{
 const source='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200"><text x="20" y="40" font-size="20" textLength="420" lengthAdjust="spacing">Long timeline paragraph</text></svg>';
 const content=btoa(source),before=document.body.childElementCount;
 const originalImport=document.importNode.bind(document);
 const geometry=vi.spyOn(document,'importNode').mockImplementation((node,deep)=>{
  const imported=originalImport(node,deep);
  Object.defineProperties(imported,{
   viewBox:{value:{baseVal:{x:0,y:0,width:500,height:200}}},
   getCTM:{value:()=>({inverse:()=>({})})},
   getBBox:{value:()=>({x:20,y:20,width:420,height:24})},
  });
  return imported;
 });
 try {
  const result=await diagramViewport(content);
  const preview=new DOMParser().parseFromString(atob(result.content),'image/svg+xml');
  expect(preview.querySelector('text')?.getAttribute('textLength')).toBe('420');
  expect(preview.querySelector('text')?.getAttribute('lengthAdjust')).toBe('spacing');
  expect(preview.querySelector('text')?.textContent).toBe('Long timeline paragraph');
  expect(atob(content)).toBe(source);
  expect(document.body.childElementCount).toBe(before);
 } finally {geometry.mockRestore();}
});
