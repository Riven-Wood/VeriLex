import {readFileSync} from 'node:fs';
import {describe,it,expect} from 'vitest';

function parse(html:string){
  const source=readFileSync('src/documents/word-clipboard.js','utf8');
  return new Function(source+';return window.parseWordClipboard;')()(html);
}
describe('Word formatted clipboard',()=>{
  it('keeps mixed inline formatting and paragraph alignment without executing markup',()=>{
    const value=parse('<p style="text-align:center;font-family:\'仿宋\';font-size:12pt">甲<b>乙</b><span style="color:#ff0000;font-size:18pt">丙</span></p>');
    expect(value.paragraphs).toHaveLength(1);
    expect(value.paragraphs[0].runs.map((r:any)=>r.text).join('')).toBe('甲乙丙');
    expect(value.paragraphs[0].format.alignment).toBe('center');
    expect(value.paragraphs[0].runs[0].format).toMatchObject({fontFamily:'仿宋',fontSizePts:12,bold:false});
    expect(value.paragraphs[0].runs[1].format.bold).toBe(true);
    expect(value.paragraphs[0].runs[2].format).toMatchObject({color:'FF0000',fontSizePts:18});
  });
  it('preserves paragraphs, blank lines, soft breaks and tabs',()=>{
    const value=parse('<p>第一行<br>第二行</p><p><br></p><p style="white-space:pre-wrap">甲\t乙</p>');
    expect(value.paragraphs.map((p:any)=>p.runs.map((r:any)=>r.text).join(''))).toEqual(['第一行\n第二行','','甲\t乙']);
  });
  it('normalizes HTML whitespace without inventing indents from pretty printing',()=>{
    const value=parse('\n <p>第一 <b> 加粗 </b> 末尾</p>\n <p>第二</p>\n');
    expect(value.paragraphs.map((p:any)=>p.runs.map((r:any)=>r.text).join(''))).toEqual(['第一 加粗 末尾','第二']);
  });
  it('does not silently flatten tables, images, or executable content',()=>{
    for(const html of ['<table><tr><td>甲</td></tr></table>','<p>甲<img src="https://example.com/a.png"></p>','<script>bad()</script><p>甲</p>'])expect(()=>parse(html)).toThrow();
  });
  it('blocks oversized fragments before parsing and ignores office metadata',()=>{
    expect(()=>parse('字'.repeat(1000001))).toThrow();
    expect(parse('<meta charset="utf-8"><!--StartFragment--><p>正文</p><!--EndFragment-->').paragraphs[0].runs[0].text).toBe('正文');
  });
  it('supports semantic lists and nested inline overrides',()=>{
    const value=parse('<ol><li><b>加粗<span style="font-weight:normal">普通</span></b></li><li>下一项</li></ol>');
    expect(value.paragraphs.map((p:any)=>p.list)).toEqual(['decimal','decimal']);
    expect(value.paragraphs[0].runs.map((r:any)=>r.format.bold)).toEqual([true,false]);
  });
  it('does not inherit centered alignment into a following ordinary source paragraph',()=>{
    const value=parse('<p style="text-align:center">标题</p><p>正文</p>');
    expect(value.paragraphs.map((p:any)=>p.format.alignment)).toEqual(['center','left']);
  });
  it('keeps intentional spaces in preformatted clipboard text',()=>{
    const value=parse('<pre>  甲  </pre>');expect(value.paragraphs[0].runs[0].text).toBe('  甲  ');
  });
});
