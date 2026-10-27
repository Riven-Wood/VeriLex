import {proposalMarkKey} from "./proposalMarks";
import type {EditorProposal} from "./client";
import {useEffect, useRef, useState} from 'react';
import {EditorContent, useEditor} from '@tiptap/react';
import type {JSONContent} from '@tiptap/core';
import {editorExtensions} from './schema';
import './LitigationDocumentEditor.css';

export interface EditorSelection {from:number; to:number; text:string}
interface Props {
  document:JSONContent; editable:boolean; proposalMode?:'inline'|'comment'; positionKey?:string; proposal?:EditorProposal|null;
  onDraft:(document:JSONContent)=>Promise<unknown>;
  onSave:(document:JSONContent)=>Promise<unknown>;
  onSelection?:(selection:EditorSelection|null)=>void;
  onChange?:(document:JSONContent)=>void;
  onUndoAI?:()=>void;
}
export function LitigationDocumentEditor({document:initial,editable,onDraft,onSave,onSelection,onChange,proposal,proposalMode,onUndoAI,positionKey}:Props) {
  const [status,setStatus]=useState(''); const [saving,setSaving]=useState(false);
  const [format,setFormat]=useState(false); const [search,setSearch]=useState(false);
  const [find,setFind]=useState(''); const [replace,setReplace]=useState('');
  const [,refresh]=useState(0);
  const callbacks=useRef({onDraft,onSave,onSelection,onChange});callbacks.current={onDraft,onSave,onSelection,onChange};
  const latest=useRef(initial); const dirty=useRef(false); const serial=useRef(0);const mounted=useRef(true);
  const queue=useRef(Promise.resolve()); const debounce=useRef<ReturnType<typeof setTimeout>>();
  const composition=useRef(false);
  const flush=useRef<()=>Promise<void>>(async()=>{});
  flush.current=async()=>{
    if(!dirty.current||composition.current)return;
    const version=serial.current,doc=latest.current,write=callbacks.current.onDraft;
    const operation=queue.current.then(async()=>{
      try {await write(doc);if(serial.current===version){dirty.current=false;if(mounted.current)setStatus('草稿已保存');}}
      catch(e){if(mounted.current)setStatus(e instanceof Error?e.message:'草稿保存失败，请重试');throw e;}
    });
    queue.current=operation.catch(()=>{});return operation;
  };
  const editor=useEditor({extensions:editorExtensions(),content:initial,editable,
    editorProps:{attributes:{role:'textbox','aria-label':'文书正文','aria-multiline':'true',spellcheck:'false'},
      handleDOMEvents:{compositionstart:()=>{composition.current=true;return false;},compositionend:()=>{
        composition.current=false;clearTimeout(debounce.current);debounce.current=setTimeout(()=>void flush.current().catch(()=>{}),1000);return false;
      }}},
    onUpdate:({editor})=>{latest.current=editor.getJSON();dirty.current=true;serial.current++;setStatus('未保存');callbacks.current.onChange?.(latest.current);
      clearTimeout(debounce.current);if(!composition.current)debounce.current=setTimeout(()=>void flush.current().catch(()=>{}),1000);
    },
    onSelectionUpdate:({editor})=>{const {from,to}=editor.state.selection;if(positionKey)try{localStorage.setItem(positionKey,JSON.stringify({cursor:to,scroll:editor.view.dom.closest('.litigation-shell')?.scrollTop??0}));}catch{};callbacks.current.onSelection?.(from===to?null:{from,to,text:editor.state.doc.textBetween(from,to,'\n')});},
    onTransaction:()=>refresh(v=>v+1),
  });
  useEffect(()=>{
    if(!editor||!positionKey)return;
    try{const stored=JSON.parse(localStorage.getItem(positionKey)??'null');if(stored&&Number.isInteger(stored.cursor)&&stored.cursor<=editor.state.doc.content.size){editor.commands.setTextSelection(stored.cursor);const container=editor.view.dom.closest('.litigation-shell');if(container)container.scrollTop=stored.scroll??0;}}catch{}
    const container=editor.view.dom.closest('.litigation-shell');
    const remember=()=>{try{localStorage.setItem(positionKey,JSON.stringify({cursor:editor.state.selection.to,scroll:container?.scrollTop??0}));}catch{}};
    container?.addEventListener('scroll',remember);return()=>{remember();container?.removeEventListener('scroll',remember);};
  },[editor,positionKey]);
  useEffect(()=>{
    if(!editor)return;
    const range=proposal?.selection as {from?:number;to?:number}|null|undefined;
    const matching=proposal&&JSON.stringify(latest.current)===JSON.stringify(proposal.preview_before??proposal.before);
    const mark=matching?(proposal.preview_changes??(proposal.changes??[{selection:range,replacement:proposal.replacement}]).filter((_,i)=>!proposal.applied?.includes(i))).map((change,index)=>({commentId:proposalMode==='comment'?`proposal-${proposal.id}-${(proposal.changes??[{}]).map((_,i)=>i).filter(i=>!proposal.applied?.includes(i))[index]??index}`:undefined,from:change.selection?.from??0,to:change.selection?.to??editor.state.doc.content.size,text:change.replacement})):null;
    editor.view.dispatch(editor.state.tr.setMeta(proposalMarkKey,mark));
  },[editor,proposal,proposalMode]);
  useEffect(()=>{editor?.setEditable(editable,false);},[editor,editable]);
  useEffect(()=>{
    mounted.current=true;
    const interval=setInterval(()=>void flush.current().catch(()=>{}),5000);
    const blur=()=>void flush.current().catch(()=>{});
    window.addEventListener('blur',blur);
    return()=>{mounted.current=false;clearInterval(interval);clearTimeout(debounce.current);window.removeEventListener('blur',blur);void flush.current().catch(()=>{});};
  },[]);
  const save=async()=>{
    if(!editor||!editable||composition.current||saving)return;
    setSaving(true);const version=serial.current;const doc=editor.getJSON();
    clearTimeout(debounce.current);
    try {const write=callbacks.current.onSave;const operation=queue.current.then(()=>write(doc));queue.current=operation.then(()=>{},()=>{});await operation;if(serial.current===version){dirty.current=false;setStatus('已保存');}}
    catch(e){setStatus(e instanceof Error?e.message:'保存失败，请重试');}
    finally{setSaving(false);}
  };
  const searchNext=(substitute=false)=>{
    if(!editor||!find)return;
    const hits:{from:number;to:number}[]=[];
    editor.state.doc.descendants((node,pos)=>{
      if(!node.isTextblock)return;
      // Leaf nodes such as hardBreak occupy one document position too.
      const text=node.textBetween(0,node.content.size,'','\n');let at=text.indexOf(find);
      while(at>=0){hits.push({from:pos+1+at,to:pos+1+at+find.length});at=text.indexOf(find,at+find.length);}
      return false;
    });
    if(!hits.length){setStatus('未找到该文字');return;}
    const current=editor.state.selection;
    const chosen=(substitute?hits.find(h=>h.from===current.from&&h.to===current.to):null)
      ??hits.find(h=>h.from>=current.to)??hits[0];
    if(substitute)editor.chain().focus().insertContentAt(chosen,replace?{type:'text',text:replace}:[]).run();
    else editor.chain().focus().setTextSelection(chosen).scrollIntoView().run();
  };
  if(!editor)return null;
  return <section className="litigation-editor" aria-label="文书编辑器" onKeyDown={e=>{
    if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&e.key.toLowerCase()==='z'&&!editor.can().undo()&&editable&&onUndoAI){e.preventDefault();onUndoAI();}
    if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();void save();}
    if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='f'){e.preventDefault();setSearch(true);}
  }}>
    <div className="litigation-editor-toolbar" role="toolbar" aria-label="文书格式">
      <select aria-label="段落样式" disabled={!editable} value={editor.isActive('heading')?String(editor.getAttributes('heading').level):'0'} onChange={e=>{
        const level=Number(e.target.value);if(level)editor.chain().focus().setHeading({level:level as 1|2|3}).run();else editor.chain().focus().setParagraph().run();
      }}><option value="0">正文</option><option value="1">一级标题</option><option value="2">二级标题</option><option value="3">三级标题</option></select>
      <button type="button" disabled={!editable} aria-label="粗体" aria-pressed={editor.isActive('bold')} onClick={()=>editor.chain().focus().toggleBold().run()}><b>B</b></button>
      <button type="button" disabled={!editable} aria-label="斜体" aria-pressed={editor.isActive('italic')} onClick={()=>editor.chain().focus().toggleItalic().run()}><i>I</i></button>
      <button type="button" disabled={!editable} aria-label="下划线" aria-pressed={editor.isActive('underline')} onClick={()=>editor.chain().focus().toggleUnderline().run()}><u>U</u></button>
      <button type="button" aria-label="撤销" disabled={!editable||(!editor.can().undo()&&!onUndoAI)} onClick={()=>editor.can().undo()?editor.chain().focus().undo().run():onUndoAI?.()}>↶</button>
      <button type="button" aria-label="重做" disabled={!editable||!editor.can().redo()} onClick={()=>editor.chain().focus().redo().run()}>↷</button>
      <button type="button" aria-expanded={format} onClick={()=>setFormat(!format)}>格式</button>
      <button type="button" aria-expanded={search} onClick={()=>setSearch(!search)}>查找</button>
      <button type="button" disabled={!editable||saving} onClick={()=>void save()}>{saving?'保存中…':'保存'}</button>
    </div>
    {format&&<div className="litigation-editor-options">
      <label>字体<select aria-label="字体" disabled={!editable} value={editor.getAttributes('textStyle').fontFamily??'宋体'} onChange={e=>editor.chain().focus().setFontFamily(e.target.value).run()}>{['宋体','黑体','仿宋','楷体','Arial','Times New Roman'].map(s=><option key={s}>{s}</option>)}</select></label>
      <label>字号<select aria-label="字号" disabled={!editable} value={editor.getAttributes('textStyle').fontSize??'12pt'} onChange={e=>editor.chain().focus().setFontSize(e.target.value).run()}>{[10,12,14,16,18,22,24,26,28,36].map(n=><option key={n} value={`${n}pt`}>{n}</option>)}</select></label>
      <label>对齐<select aria-label="对齐" disabled={!editable} value={editor.getAttributes(editor.isActive('heading')?'heading':'paragraph').textAlign??'left'} onChange={e=>editor.chain().focus().setTextAlign(e.target.value).run()}>{[['left','左对齐'],['center','居中'],['right','右对齐'],['justify','两端对齐']].map(([v,l])=><option value={v} key={v}>{l}</option>)}</select></label>
      <label>行距<select aria-label="行距" disabled={!editable} onChange={e=>editor.chain().focus().updateAttributes(editor.isActive('heading')?'heading':'paragraph',{lineHeight:e.target.value}).run()} defaultValue="1.5"><option value="1">单倍</option><option value="1.5">1.5倍</option><option value="2">双倍</option></select></label>
      <button type="button" disabled={!editable} onClick={()=>editor.chain().focus().updateAttributes(editor.isActive('heading')?'heading':'paragraph',{indent:24}).run()}>缩进</button>
      <button type="button" disabled={!editable} onClick={()=>editor.chain().focus().updateAttributes(editor.isActive('heading')?'heading':'paragraph',{indent:0}).run()}>取消缩进</button>
      <button type="button" disabled={!editable} aria-pressed={editor.isActive('orderedList')} onClick={()=>editor.chain().focus().toggleOrderedList().run()}>编号</button>
      <button type="button" disabled={!editable} aria-pressed={editor.isActive('bulletList')} onClick={()=>editor.chain().focus().toggleBulletList().run()}>项目符号</button>
      <button type="button" disabled={!editable||editor.isActive('table')} onClick={()=>editor.chain().focus().insertTable({rows:3,cols:2,withHeaderRow:true}).run()}>插入表格</button>
      {editor.isActive('table')&&<><button type="button" disabled={!editable} onClick={()=>editor.chain().focus().addRowAfter().run()}>增加行</button><button type="button" disabled={!editable} onClick={()=>editor.chain().focus().deleteRow().run()}>删除行</button></>}
    </div>}
    {search&&<div className="litigation-editor-find"><input aria-label="查找文字" value={find} onChange={e=>setFind(e.target.value)}/><button type="button" disabled={!find} onClick={()=>searchNext()}>下一处</button><input aria-label="替换为" value={replace} onChange={e=>setReplace(e.target.value)}/><button type="button" disabled={!find||!editable} onClick={()=>searchNext(true)}>替换此处</button></div>}
    <EditorContent editor={editor}/>
    {status&&<div className="litigation-editor-status" role="status">{status}{status.includes('失败')&&<button type="button" onClick={()=>void flush.current().catch(()=>{})}>重试保存草稿</button>}</div>}
  </section>;
}
