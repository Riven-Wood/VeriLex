import {EditorDiscussionProvider,useEditorDiscussion} from "../src/components/editor/DiscussionContext";
import {EditorProposals} from "../src/components/editor/EditorProposals";
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {Editor} from '@tiptap/core';
import {editorExtensions} from '../src/components/editor/schema';
import {LitigationDocumentEditor} from '../src/components/editor/LitigationDocumentEditor';
import {EditableProducts} from '../src/components/editor/EditableProducts';
import {editorClient,type EditableProduct} from '../src/components/editor/client';
vi.mock('../src/components/editor/client',()=>({editorClient:{list:vi.fn(),read:vi.fn(),create:vi.fn(),copy:vi.fn(),save:vi.fn(),draft:vi.fn(),history:vi.fn(),proposals:vi.fn(),decide:vi.fn()}}));
afterEach(()=>{cleanup();vi.clearAllMocks();localStorage.clear();});
const doc={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'中文正文'}]}]};
const product:EditableProduct={id:'a'.repeat(32),title:'答辩状',kind:'documents',document:doc,file_hash:'a'.repeat(64),revision:1,updated_at:'now',external_update:false,path:'文书起草/答辩状.docx'};

test('另存为使用原文件创建副本并提交当前正文',async()=>{
 const native={...product,native_word:true};
 vi.mocked(editorClient.list).mockResolvedValue({products:[native],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(native);
 vi.mocked(editorClient.copy).mockResolvedValue({...native,id:'b'.repeat(32),title:'答辩状（副本）'});
 localStorage.setItem('verilex:editor-product:w:documents',native.id);
 render(<EditableProducts workspaceId="w" kind="documents" visible canWrite onActive={()=>{}}/>);
 await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByText('更多',{selector:'summary'}));
 fireEvent.click(screen.getByRole('button',{name:'另存为'}));
 await waitFor(()=>expect(editorClient.copy).toHaveBeenCalledWith('w',native.id,native.file_hash,doc,'答辩状（副本）'));
 expect(editorClient.create).not.toHaveBeenCalled();
});

test('real editor transactions preserve Chinese table text and support undo/redo',()=>{
 const editor=new Editor({extensions:editorExtensions(),content:doc});
 editor.commands.insertContentAt(3,{type:'text',text:'新增'});expect(editor.getText()).toBe('中文新增正文');
 expect(editor.commands.undo()).toBe(true);expect(editor.getText()).toBe('中文正文');
 expect(editor.commands.redo()).toBe(true);expect(editor.getText()).toBe('中文新增正文');
 editor.commands.setTextSelection(3);editor.commands.toggleBold();editor.commands.insertContent('加粗');
 expect(JSON.stringify(editor.getJSON())).toContain('bold');
 editor.commands.setTextSelection(editor.state.doc.content.size-1);editor.commands.insertTable({rows:2,cols:2});
 expect(JSON.stringify(editor.getJSON())).toContain('table');editor.destroy();
});

test('save reports actual failure and retry uses the current editor body',async()=>{
 const save=vi.fn().mockRejectedValueOnce(new Error('磁盘写入失败')).mockResolvedValue(undefined);
 render(<LitigationDocumentEditor document={doc} editable onDraft={vi.fn()} onSave={save}/>);
 fireEvent.click(screen.getByRole('button',{name:'保存'}));expect(await screen.findByText('磁盘写入失败')).toBeTruthy();
 expect(screen.queryByText('已保存')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'保存'}));expect(await screen.findByText('已保存')).toBeTruthy();
 expect(save.mock.calls[1][0].content[0].content[0].text).toBe('中文正文');
});

test('read-only historical body cannot be saved through the keyboard shortcut',async()=>{
 const save=vi.fn();
 render(<LitigationDocumentEditor document={doc} editable={false} onDraft={vi.fn()} onSave={save}/>);
 fireEvent.keyDown(screen.getByRole('textbox',{name:'文书正文'}),{key:'s',metaKey:true});
 await act(async()=>{});
 expect(save).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'保存'}).hasAttribute('disabled')).toBe(true);
});

test('find replace targets one occurrence and undo restores it',async()=>{
 render(<LitigationDocumentEditor document={{type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'重复段落 重复段落'}]}]}} editable onDraft={vi.fn().mockResolvedValue(undefined)} onSave={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'查找'}));
 fireEvent.change(screen.getByRole('textbox',{name:'查找文字'}),{target:{value:'重复段落'}});
 fireEvent.change(screen.getByRole('textbox',{name:'替换为'}),{target:{value:'选中修改'}});
 // jsdom has no layout API used by ProseMirror's scrollIntoView.
 Range.prototype.getClientRects=()=>[] as unknown as DOMRectList;
 Range.prototype.getBoundingClientRect=()=>({top:0,bottom:0,left:0,right:0,width:0,height:0,x:0,y:0,toJSON:()=>({})});
 fireEvent.click(screen.getByRole('button',{name:'替换此处'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('选中修改 重复段落');
 fireEvent.click(screen.getByRole('button',{name:'撤销'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('重复段落 重复段落');
});

test('new document enters real editor and reload selects saved product with persistent draft',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[],errors:[]});
 vi.mocked(editorClient.create).mockResolvedValue(product);
 vi.mocked(editorClient.draft).mockResolvedValue({});
 const props={workspaceId:'w',kind:'documents' as const,visible:true,canWrite:true,onActive:vi.fn()};
 const view=render(<EditableProducts {...props}/>);
 fireEvent.click(screen.getByRole('button',{name:'新建文书'}));
 fireEvent.click(screen.getByRole('tab',{name:'空白文档'}));
 fireEvent.click(screen.getByRole('button',{name:'创建空白文档'}));
 expect(await screen.findByRole('textbox',{name:'文书正文'})).toBeTruthy();
 expect(props.onActive).toHaveBeenCalledWith(true);view.unmount();
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue({...product,draft_current:true,draft:{base_hash:product.file_hash,updated_at:'now',document:{type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'恢复中文草稿'}]}]}}});
 render(<EditableProducts {...props}/>);
 expect((await screen.findByRole('textbox',{name:'文书正文'})).textContent).toBe('恢复中文草稿');
});

test('proposal marks show a suggestion without changing the saved body',async()=>{
 const draft=vi.fn().mockResolvedValue(undefined),save=vi.fn().mockResolvedValue(undefined);
 const proposal={id:'p',thread_id:'t',product_id:'a',status:'pending' as const,before:doc,after:doc,selection:{from:1,to:3,text:'中文'},replacement:'新文字'};
 render(<LitigationDocumentEditor document={doc} editable proposal={proposal} onDraft={draft} onSave={save}/>);
 expect(await screen.findByLabelText('建议新增文字')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'保存'}));await screen.findByText('已保存');
 expect(JSON.stringify(save.mock.calls[0][0])).not.toContain('新文字');
 expect(draft).not.toHaveBeenCalled();
});


test('acceptance briefly locks the editor until the saved revision returns',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(product);
 vi.mocked(editorClient.proposals).mockResolvedValue({proposals:[{id:'p',product_id:product.id,thread_id:'t',status:'pending',before:doc,after:doc,selection:null,replacement:'建议'}]});
 let finish!:(value:EditableProduct)=>void;
 vi.mocked(editorClient.decide).mockReturnValue(new Promise(resolve=>{finish=resolve;}));
 localStorage.setItem('verilex:editor-product:w:documents',product.id);
 localStorage.setItem('verilex:editor-discussion:w:t',product.id);
 render(<EditorDiscussionProvider workspaceId="w" threadId="t"><EditableProducts workspaceId="w" kind="documents" visible canWrite onActive={()=>{}}/><EditorProposals workspaceId="w"/></EditorDiscussionProvider>);
 const button=await screen.findByRole('button',{name:'接受修改'});
 fireEvent.click(button);
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'文书正文'}).getAttribute('contenteditable')).toBe('false'));
 await act(async()=>{finish({...product,revision:2});});
 await waitFor(()=>expect(screen.getByRole('textbox',{name:'文书正文'}).getAttribute('contenteditable')).toBe('true'));
});


test('creating the first discussion preserves its selection until the message is sent',async()=>{
 let context:ReturnType<typeof useEditorDiscussion>;
 function Observe(){context=useEditorDiscussion();return null;}
 const view=render(<EditorDiscussionProvider workspaceId="w" threadId={null}><Observe/></EditorDiscussionProvider>);
 let selected:{from:number;to:number;text:string}|null={from:1,to:3,text:'中文'};
 act(()=>context!.setTarget({id:product.id,title:'文书',kind:'documents',capture:()=>({product_id:product.id,base_hash:product.file_hash,document:doc,selection:selected}),clearSelection:()=>{selected=null;},apply:()=>{}}));
 view.rerender(<EditorDiscussionProvider workspaceId="w" threadId="first-thread"><Observe/></EditorDiscussionProvider>);
 expect(context!.capture()?.selection?.text).toBe('中文');
 act(()=>context!.sent());expect(selected).toBeNull();
 view.rerender(<EditorDiscussionProvider workspaceId="w" threadId="another-thread"><Observe/></EditorDiscussionProvider>);
 expect(context!.capture()).toBeNull();
});


test('continue processing keeps text entered before choosing a discussion target',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});vi.mocked(editorClient.read).mockResolvedValue(product);vi.mocked(editorClient.draft).mockResolvedValue({});
 localStorage.setItem('verilex:editor-product:w:documents',product.id);
 render(<EditorDiscussionProvider workspaceId="w" threadId="t"><EditableProducts workspaceId="w" kind="documents" visible canWrite onActive={()=>{}}/></EditorDiscussionProvider>);
 const textbox=await screen.findByRole('textbox',{name:'文书正文'});
 await act(async()=>{textbox.textContent='选择讨论前输入的新文字';fireEvent.input(textbox);});
 await screen.findByText('未保存');
 fireEvent.click(screen.getByRole('button',{name:'继续处理此文书'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('选择讨论前输入的新文字');
});

test('closing history must preserve the current unsaved body',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});vi.mocked(editorClient.read).mockResolvedValue(product);vi.mocked(editorClient.draft).mockResolvedValue({});vi.mocked(editorClient.history).mockResolvedValue({versions:[product]});
 localStorage.setItem('verilex:editor-product:w:documents',product.id);
 render(<EditableProducts workspaceId="w" kind="documents" visible canWrite onActive={()=>{}}/>);
 const textbox=await screen.findByRole('textbox',{name:'文书正文'});
 await act(async()=>{textbox.textContent='新补充的重要事实';fireEvent.input(textbox);});
 await screen.findByText('未保存');
 fireEvent.click(screen.getByText('更多',{selector:'summary'}));
 fireEvent.click(screen.getByRole('button',{name:'历史'}));
 await screen.findByRole('button',{name:'关闭历史'});
 fireEvent.click(screen.getByRole('button',{name:'关闭历史'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('新补充的重要事实');
});

test('replace text after a hard line break targets only the requested text',()=>{
 Range.prototype.getClientRects=()=>[] as unknown as DOMRectList;
 Range.prototype.getBoundingClientRect=()=>({top:0,bottom:0,left:0,right:0,width:0,height:0,x:0,y:0,toJSON:()=>({})});
 const body={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'第一行'},{type:'hardBreak'},{type:'text',text:'第二行'}]}]};
 render(<LitigationDocumentEditor document={body} editable onDraft={vi.fn().mockResolvedValue(undefined)} onSave={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'查找'}));
 fireEvent.change(screen.getByRole('textbox',{name:'查找文字'}),{target:{value:'第二行'}});
 fireEvent.change(screen.getByRole('textbox',{name:'替换为'}),{target:{value:'修改后'}});
 fireEvent.click(screen.getByRole('button',{name:'替换此处'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).innerHTML).toBe('<p>第一行<br>修改后</p>');
});

test.each(['current','close'])('visiting history and returning via %s preserves the draft and saves the latest body',async(returnVia)=>{
 const current={...product,revision:2};
 const historical={...product,document:{type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'历史正文'}]}]}};
 vi.mocked(editorClient.list).mockResolvedValue({products:[current],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(current);
 vi.mocked(editorClient.history).mockResolvedValue({versions:[historical]});
 vi.mocked(editorClient.draft).mockResolvedValue({});
 vi.mocked(editorClient.save).mockImplementation(async(_w,_id,_hash,document)=>({...current,document,revision:3}));
 localStorage.setItem('verilex:editor-product:w:documents',product.id);
 render(<EditableProducts workspaceId="w" kind="documents" visible canWrite onActive={()=>{}}/>);
 const textbox=await screen.findByRole('textbox',{name:'文书正文'});
 await act(async()=>{textbox.textContent='本次新补充的事实';fireEvent.input(textbox);});
 await screen.findByText('未保存');
 fireEvent.click(screen.getByText('更多',{selector:'summary'}));
 fireEvent.click(screen.getByRole('button',{name:'历史'}));
 fireEvent.change(await screen.findByRole('combobox',{name:'编辑历史版本'}),{target:{value:'1'}});
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('历史正文');
 expect(screen.getByRole('textbox',{name:'文书正文'}).getAttribute('contenteditable')).toBe('false');
 await waitFor(()=>expect(editorClient.draft).toHaveBeenCalled());
 expect(JSON.stringify(vi.mocked(editorClient.draft).mock.calls.at(-1)?.[3])).toContain('本次新补充的事实');
 if(returnVia==='current')fireEvent.change(screen.getByRole('combobox',{name:'编辑历史版本'}),{target:{value:''}});
 else fireEvent.click(screen.getByRole('button',{name:'关闭历史'}));
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('本次新补充的事实');
 fireEvent.click(screen.getByRole('button',{name:'保存',exact:true}));
 await screen.findByText('已保存');
 expect(JSON.stringify(vi.mocked(editorClient.save).mock.calls.at(-1)?.[3])).toContain('本次新补充的事实');
 expect(vi.mocked(editorClient.draft).mock.calls.every(call=>!JSON.stringify(call[3]).includes('历史正文'))).toBe(true);
});
