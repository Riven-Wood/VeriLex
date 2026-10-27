import type {JSONContent} from '@tiptap/core';
import type {EditorSelection} from './LitigationDocumentEditor';
// Word uses paragraph-local UTF-16 offsets; AI snapshots use structural offsets.
// Compare the entire body text sequence before mapping, including duplicate text.
export function wordSelection(document:JSONContent,value:unknown):EditorSelection|null {
 const v=value as {texts?:unknown;start:number;end:number;from:number;to:number}|null;
 if(!v||!Array.isArray(v.texts)||!v.texts.every(t=>typeof t==='string')||![v.start,v.end,v.from,v.to].every(Number.isInteger))return null;
 const texts=v.texts as string[];
 const blocks:{from:number;text:string}[]=[];
 const walk=(node:JSONContent,pos:number):number=>{
  if(node.type==='text')return node.text?.length??0;
  if(node.type==='hardBreak')return 1;
  if(node.type==='paragraph'||node.type==='heading')blocks.push({from:pos+1,text:(node.content??[]).map(n=>n.text??'\n').join('')});
  let size=node.type==='doc'?0:1;for(const child of node.content??[])size+=walk(child,pos+size);return size+(node.type==='doc'?0:1);
 };walk(document,0);
 if(blocks.length!==v.texts.length||blocks.some((b,i)=>b.text!==texts[i])||v.start<0||v.end<v.start||v.end>=blocks.length)return null;
 const first=blocks[v.start],last=blocks[v.end];
 if(v.from<0||v.from>first.text.length||v.to<0||v.to>last.text.length)return null;
 const from=first.from+v.from,to=last.from+v.to;
 if(to<=from)return null;
 const text=blocks.slice(v.start,v.end+1).map((b,i)=>b.text.slice(i===0?v.from:0,i===v.end-v.start?v.to:undefined)).join('\n');
 return text?{from,to,text}:null;
}

export function wordRange(document:JSONContent,range:{from?:number;to?:number;text?:string}|null|undefined) {
 if(!range||range.from==null||range.to==null)return null;
 const blocks:{from:number;text:string}[]=[];
 const walk=(node:JSONContent,pos:number):number=>{if(node.type==='text')return node.text?.length??0;if(node.type==='hardBreak')return 1;if(node.type==='paragraph'||node.type==='heading')blocks.push({from:pos+1,text:(node.content??[]).map(n=>n.text??'\n').join('')});let size=node.type==='doc'?0:1;for(const child of node.content??[])size+=walk(child,pos+size);return size+(node.type==='doc'?0:1);};walk(document,0);
 const start=blocks.findIndex(b=>b.from<=range.from!&&b.from+b.text.length>=range.from!),end=blocks.findIndex(b=>b.from<=range.to!&&b.from+b.text.length>=range.to!);
 if(start<0||end<start)return null;
 const value={texts:blocks.map(b=>b.text),start,end,from:range.from-blocks[start].from,to:range.to-blocks[end].from};
 return wordSelection(document,value)?.text===range.text?value:null;
}
