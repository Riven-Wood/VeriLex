import {expect,it} from 'vitest';
import {wordSelection} from '../src/components/editor/wordSelection';
const document={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'重复😀重复'}]},{type:'paragraph',content:[{type:'text',text:'另一段'}]}]};
it('maps the exact second occurrence with UTF-16 offsets',()=>{expect(wordSelection(document,{texts:['重复😀重复','另一段'],start:0,end:0,from:4,to:6})).toEqual({from:5,to:7,text:'重复'});});
it('maps a multi-paragraph selection and refuses stale or partial bodies',()=>{expect(wordSelection(document,{texts:['重复😀重复','另一段'],start:0,end:1,from:4,to:1})).toEqual({from:5,to:10,text:'重复\n另'});expect(wordSelection(document,{texts:['重复😀重复'],start:0,end:0,from:4,to:6})).toBeNull();});
