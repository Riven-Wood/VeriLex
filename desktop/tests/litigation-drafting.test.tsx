import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {LitigationDrafting} from '../src/components/editor/LitigationDrafting';
import {EditorDiscussionProvider} from '../src/components/editor/DiscussionContext';
import {openCaseDatabase} from '../src/platform/caseDatabase';
const wordFlush=vi.hoisted(()=>vi.fn());
const wordState=vi.hoisted(()=>({ready:true,dirty:false}));
vi.mock('../src/platform/caseDatabase',()=>({openCaseDatabase:vi.fn()}));
import {editorClient} from '../src/components/editor/client';
import {litigationWorkClient} from '../src/views/LitigationClient';
vi.mock('../src/components/editor/client',()=>({editorClient:{list:vi.fn(),read:vi.fn(),create:vi.fn(),save:vi.fn(),draft:vi.fn(),proposals:vi.fn(),decide:vi.fn(),export:vi.fn(),openWord:vi.fn(),saveWord:vi.fn(),delete:vi.fn(),rename:vi.fn(),templates:vi.fn(),saveTemplate:vi.fn(),importCopy:vi.fn(),importTemplate:vi.fn()}}));
vi.mock('../src/components/wordEditor/WordDocumentEditor',async()=>{const React=await import('react');return {WordDocumentEditor:React.forwardRef((props:any,ref:any)=>{React.useImperativeHandle(ref,()=>({flush:async()=>{await wordFlush();return {document:props.document,bytes:new Uint8Array([65])};},isDirty:()=>wordState.dirty}));React.useEffect(()=>{props.onReady(wordState.ready);},[]);return <div role="textbox" aria-label="文书正文">用户正文</div>;})};});
vi.mock('../src/views/LitigationClient',()=>({litigationWorkClient:{read:vi.fn()}}));
const blank={type:'doc',content:[{type:'paragraph'}]},body={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'用户正文'}]}]};
const product={id:'doc',title:'答辩状',kind:'documents' as const,revision:1,file_hash:'h',path:'文书起草/答辩状.docx',document:blank,external_update:false,updated_at:'now'};
const props={workspaceId:'w',materials:[{name:'合同.txt',path:'合同.txt',supported:true,reason:''}],visible:true,busy:false,canWrite:true,onStart:vi.fn(),onControlsChange:vi.fn()};
beforeEach(()=>{wordState.ready=true;wordState.dirty=false;localStorage.clear();vi.mocked(editorClient.templates).mockResolvedValue({templates:[]});vi.mocked(editorClient.openWord).mockImplementation(async(_w,id)=>({product:await editorClient.read('w',id),content:'QQ=='}));vi.mocked(editorClient.list).mockResolvedValue({products:[],errors:[]});vi.mocked(editorClient.create).mockResolvedValue(product);vi.mocked(editorClient.read).mockResolvedValue(product);vi.mocked(editorClient.draft).mockResolvedValue({});vi.mocked(editorClient.proposals).mockResolvedValue({proposals:[]});vi.mocked(litigationWorkClient.read).mockResolvedValue({revision:0,data:null});});
afterEach(()=>{cleanup();vi.clearAllMocks();localStorage.clear();});
function mount(){return render(<EditorDiscussionProvider workspaceId="w" threadId="t"><LitigationDrafting {...props}/></EditorDiscussionProvider>);}
test('selected material order and explicit blank editor snapshot travel together',async()=>{
 mount();fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'答辩状'}});
 act(()=>props.onControlsChange.mock.lastCall![0].toggle('合同.txt'));
 fireEvent.change(screen.getByRole('textbox',{name:'起草或修改要求'}),{target:{value:'根据合同起草答辩状'}});
 fireEvent.click(screen.getByRole('button',{name:'生成正文'}));
 await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 expect(props.onStart.mock.lastCall![0]).toMatchObject({kind:'documents',materials:['合同.txt'],editorContext:{product_id:'doc',base_hash:'h',document:blank,operation:'generate',selection:null}});
 expect(screen.getByRole('textbox',{name:'文书正文'})).toBeTruthy();
});
test('manual path exports current body to workspace and refreshes file tree',async()=>{
 vi.mocked(editorClient.create).mockResolvedValue({...product,document:body});vi.mocked(editorClient.read).mockResolvedValue({...product,document:body});
 vi.mocked(editorClient.export).mockResolvedValue({filename:'答辩状.pdf',content:'JVBERi0=',path:'答辩状.pdf',product:{...product,document:body,revision:2,file_hash:'h2'}});
 const changed=vi.fn();window.addEventListener('verilex:workspace-files-changed',changed);
 mount();fireEvent.click(screen.getByRole('button',{name:'直接手动撰写'}));
 await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByRole('button',{name:'导出 PDF'}));
 await screen.findByText('已导出到当前工作区：答辩状.pdf');
 expect(changed).toHaveBeenCalledTimes(2);window.removeEventListener('verilex:workspace-files-changed',changed);
 expect(editorClient.export).toHaveBeenCalledWith('w','doc','h',body,'答辩状','pdf');
 expect(props.onStart).not.toHaveBeenCalled();expect(screen.getByRole('group',{name:'导出到当前工作区'})).toBeTruthy();
});
test('whole rewrite stays a proposal; selection request is scoped to selected text',async()=>{
 vi.mocked(editorClient.create).mockResolvedValue({...product,document:body});vi.mocked(editorClient.read).mockResolvedValue({...product,document:body});
 mount();fireEvent.change(screen.getByRole('textbox',{name:'起草或修改要求'}),{target:{value:'语句简洁'}});
 fireEvent.click(screen.getByRole('button',{name:'直接手动撰写'}));await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByRole('button',{name:'AI辅助起草'}));
 fireEvent.click(screen.getByRole('button',{name:'重新生成全文'}));await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 const request=props.onStart.mock.lastCall![0];expect(request.editorContext.operation).toBeUndefined();expect(request.message).toContain('litigation_editor_propose');expect(request.editorContext.document).toEqual(body);
 expect(screen.getByRole('textbox',{name:'文书正文'}).textContent).toBe('用户正文');
});

test('document tabs switch saved products and new tab keeps creation independent of exports',async()=>{
 const second={...product,id:'doc2',title:'起诉状',document:body};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product,second],errors:[]});
 vi.mocked(editorClient.read).mockImplementation(async(_w,id)=>id==='doc2'?second:{...product,document:body});
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));
 await waitFor(()=>expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true'));
 expect(screen.queryByRole('combobox',{name:'打开已有文书'})).toBeNull();
 fireEvent.keyDown(screen.getByRole('tab',{name:'答辩状'}),{key:'ArrowRight'});
 await waitFor(()=>expect(screen.getByRole('tab',{name:'起诉状'}).getAttribute('aria-selected')).toBe('true'));
 fireEvent.click(screen.getByRole('tab',{name:'新建文书'}));
 await screen.findByRole('button',{name:'直接手动撰写'});
 expect(screen.getByRole('tab',{name:'新建文书'}).getAttribute('aria-selected')).toBe('true');
 expect(screen.queryByRole('button',{name:'导出 Word'})).toBeNull();
 expect(props.onStart).not.toHaveBeenCalled();
});


test('custom filename and document type stay independent for generation',async()=>{
 mount();fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'上诉状'}});
 fireEvent.change(screen.getByRole('textbox',{name:'文件名称'}),{target:{value:'张某诉李某-二稿'}});
 fireEvent.change(screen.getByRole('textbox',{name:'起草或修改要求'}),{target:{value:'撤销原判'}});
 fireEvent.click(screen.getByRole('button',{name:'生成正文'}));
 await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 expect(editorClient.create).toHaveBeenCalledWith('w','张某诉李某-二稿','documents',blank,'上诉状');
 expect(props.onStart.mock.lastCall![0].message).toContain('文书类型：上诉状');
});

test('invalid filename cannot create a document',async()=>{
 mount();fireEvent.change(screen.getByRole('textbox',{name:'文件名称'}),{target:{value:'../起诉状'}});
 expect(screen.getByRole('button',{name:'直接手动撰写'}).hasAttribute('disabled')).toBe(true);
 expect(editorClient.create).not.toHaveBeenCalled();
});

test('delete has a cancellable confirmation and removes only its tab and remembered target',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.delete).mockResolvedValue({id:'doc',path:'文书起草/已删除文书/doc/答辩状.docx'});
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByRole('button',{name:'删除文书'}));fireEvent.click(screen.getByRole('button',{name:'取消'}));
 expect(editorClient.delete).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'删除文书'}));
 vi.mocked(editorClient.list).mockResolvedValue({products:[],errors:[]});
 fireEvent.click(screen.getByRole('button',{name:'确认删除'}));
 await waitFor(()=>expect(editorClient.delete).toHaveBeenCalledWith('w','doc','h'));
 await waitFor(()=>expect(screen.queryByRole('tab',{name:'答辩状'})).toBeNull());
 expect(screen.getByRole('tab',{name:'新建文书'}).getAttribute('aria-selected')).toBe('true');
 expect(localStorage.getItem('verilex:editor-discussion:w:t')).toBeNull();
 expect(JSON.parse(localStorage.getItem('verilex:drafting:w')!).productId).toBeUndefined();
});

test('failed deletion keeps the current document available',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.delete).mockRejectedValue(new Error('文书已有更新'));
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByRole('button',{name:'删除文书'}));fireEvent.click(screen.getByRole('button',{name:'确认删除'}));
 await screen.findByText('文书已有更新');
 expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
});


test('right click renames an existing tab and export uses the updated name',async()=>{
 let value={...product,document:body};
 vi.mocked(editorClient.list).mockImplementation(async()=>({products:[value],errors:[]}));
 vi.mocked(editorClient.read).mockImplementation(async()=>value);
 vi.mocked(editorClient.rename).mockImplementation(async(_w,_id,_hash,title)=>{value={...value,title,path:`文书起草/${title}.docx`,file_hash:'renamed'};return value;});
 vi.mocked(editorClient.export).mockImplementation(async()=>({filename:value.title+'.docx',path:value.title+'.docx',content:'QQ==',product:value}));
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.contextMenu(screen.getByRole('tab',{name:'答辩状'}));
 fireEvent.click(screen.getByRole('menuitem',{name:'重命名…'}));
 fireEvent.change(await screen.findByRole('textbox',{name:'新的文件名称'}),{target:{value:'案件甲-答辩稿'}});
 fireEvent.click(screen.getByRole('button',{name:'保存名称'}));
 await screen.findByRole('tab',{name:'案件甲-答辩稿'});
 expect(editorClient.rename).toHaveBeenCalledWith('w','doc','h','案件甲-答辩稿');
 await waitFor(()=>expect(screen.getByRole('button',{name:'导出 Word'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:'导出 Word'}));
 await waitFor(()=>expect(editorClient.export).toHaveBeenCalledWith('w','doc','renamed',body,'案件甲-答辩稿','docx'));
});


test('official template shortcut remains available and reports browser failure',async()=>{
 vi.mocked(openCaseDatabase).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('unavailable'));
 mount();fireEvent.click(screen.getByRole('button',{name:'最高法文书模板'}));
 await waitFor(()=>expect(openCaseDatabase).toHaveBeenCalledWith('templates'));
 fireEvent.click(screen.getByRole('button',{name:'最高法文书模板'}));
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('系统浏览器没有打开'));
 expect(editorClient.create).not.toHaveBeenCalled();
});


test('uploaded templates share the document type menu and create independent copies',async()=>{
 const copy={...product,id:'copy',title:'本案答辩稿',document:body,document_type:'答辩模板'};
 vi.mocked(editorClient.templates).mockResolvedValue({templates:[{path:'我的文书模板/答辩模板.docx',name:'答辩模板'}]});
 vi.mocked(editorClient.importCopy).mockResolvedValue(copy);
 vi.mocked(editorClient.read).mockResolvedValue(copy);
 mount();await screen.findByRole('option',{name:'答辩模板'});
 fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'template:我的文书模板/答辩模板.docx'}});
 fireEvent.change(screen.getByRole('textbox',{name:'文件名称'}),{target:{value:'本案答辩稿'}});
 fireEvent.click(screen.getByRole('button',{name:'使用模板新建'}));
 await screen.findByRole('textbox',{name:'文书正文'});
 expect(editorClient.importCopy).toHaveBeenCalledWith('w','我的文书模板/答辩模板.docx','本案答辩稿','documents',false,'答辩模板');
 expect(editorClient.create).not.toHaveBeenCalled();expect(props.onStart).not.toHaveBeenCalled();
 expect(screen.queryByText('版本历史')).toBeNull();
});

test('upload dialog saves a chosen file and immediately selects its document type',async()=>{
 HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 vi.mocked(editorClient.importTemplate).mockResolvedValue({path:'我的文书模板/自备模板.docx',name:'自备模板'});
 mount();fireEvent.click(screen.getByRole('button',{name:'上传模板'}));
 expect(screen.getByRole('dialog',{name:'上传模板'})).toBeTruthy();
 const file=new File(['word'],'自备模板.docx');
 fireEvent.change(screen.getByLabelText('选择模板文件'),{target:{files:[file]}});
 fireEvent.click(screen.getByRole('button',{name:'保存为模板'}));
 await screen.findByRole('option',{name:'自备模板'});
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(screen.getByRole('combobox',{name:'文书类型'})).toHaveProperty('value','template:我的文书模板/自备模板.docx');
 expect(editorClient.importTemplate).toHaveBeenCalledWith('w',file,'自备模板');
 expect(editorClient.create).not.toHaveBeenCalled();
});

test('template import failure retains selection and allows retry',async()=>{
 vi.mocked(editorClient.templates).mockResolvedValue({templates:[{path:'我的文书模板/模板.docx',name:'模板'}]});
 vi.mocked(editorClient.importCopy).mockRejectedValue(new Error('模板无法读取，原件已保留。'));
 mount();await screen.findByRole('option',{name:'模板'});
 fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'template:我的文书模板/模板.docx'}});
 fireEvent.click(screen.getByRole('button',{name:'使用模板新建'}));await screen.findByText('模板无法读取，原件已保留。');
 expect(screen.getByRole('button',{name:'使用模板新建'})).toHaveProperty('disabled',false);
});

test('read-only workspaces disable template upload and new documents',async()=>{
 render(<LitigationDrafting {...props} canWrite={false}/>);
 expect(screen.getByRole('button',{name:'上传模板'})).toHaveProperty('disabled',true);
 expect(screen.getByRole('button',{name:'直接手动撰写'})).toHaveProperty('disabled',true);
});

test('AI drafting from a populated template uses reviewable changes and retains its title guidance',async()=>{
 const copy={...product,id:'copy',title:'模板副本',document:body,document_type:'通用格式'};
 vi.mocked(editorClient.templates).mockResolvedValue({templates:[{path:'我的文书模板/通用格式.docx',name:'通用格式'}]});
 vi.mocked(editorClient.importCopy).mockResolvedValue(copy);vi.mocked(editorClient.read).mockResolvedValue(copy);
 mount();await screen.findByRole('option',{name:'通用格式'});
 fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'template:我的文书模板/通用格式.docx'}});
 fireEvent.change(screen.getByRole('textbox',{name:'起草或修改要求'}),{target:{value:'按模板写明请求'}});
 fireEvent.click(screen.getByRole('button',{name:'生成正文'}));
 await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 expect(props.onStart.mock.lastCall![0].editorContext.operation).toBeUndefined();
 expect(props.onStart.mock.lastCall![0].message).toContain('沿用模板正文的文种、标题和格式');
 expect(props.onStart.mock.lastCall![0].message).toContain('litigation_editor_propose');
});

test('close active tab flushes edits, selects neighbour and reopens without deleting a file',async()=>{
 const next={...product,id:'next',title:'起诉状',document:body};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product,next],errors:[]});
 vi.mocked(editorClient.read).mockImplementation(async(_w,id)=>id==='next'?next:product);
 const view=render(<LitigationDrafting {...props}/>);fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 wordFlush.mockClear();fireEvent.click(screen.getByRole('button',{name:'关闭标签：答辩状'}));
 await waitFor(()=>expect(screen.queryByRole('tab',{name:'答辩状'})).toBeNull());
 expect(wordFlush).toHaveBeenCalled();expect(screen.getByRole('tab',{name:'起诉状'}).getAttribute('aria-selected')).toBe('true');
 expect(editorClient.delete).not.toHaveBeenCalled();
 expect(JSON.parse(localStorage.getItem('verilex:drafting:w')!).closedIds).toEqual(['doc']);
 view.rerender(<LitigationDrafting {...props} openDocumentRequest={{id:'doc',sequence:1}}/>);
 await waitFor(()=>expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true'));
 expect(screen.queryByRole('combobox',{name:'打开已关闭文书'})).toBeNull();
});

test('last tab closes to new document and stays closed after remount',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 const view=mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 fireEvent.click(screen.getByRole('button',{name:'关闭标签：答辩状'}));
 await waitFor(()=>expect(screen.queryByRole('tab',{name:'答辩状'})).toBeNull());
 expect(screen.getByRole('tab',{name:'新建文书'}).getAttribute('aria-selected')).toBe('true');
 view.unmount();mount();await waitFor(()=>expect(editorClient.list).toHaveBeenCalled());
 expect(screen.queryByRole('tab',{name:'答辩状'})).toBeNull();expect(screen.queryByRole('textbox',{name:'文书正文'})).toBeNull();
});

test('failed flush keeps active tab open and does not mark it closed',async()=>{
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 wordFlush.mockRejectedValueOnce(new Error('保存未完成'));
 fireEvent.click(screen.getByRole('button',{name:'关闭标签：答辩状'}));await screen.findByText('保存未完成');
 expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
 expect(screen.queryByRole('combobox',{name:'打开已关闭文书'})).toBeNull();expect(editorClient.delete).not.toHaveBeenCalled();
});

test('closing an inactive tab leaves current editor and content alone',async()=>{
 const next={...product,id:'next',title:'起诉状'};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product,next],errors:[]});
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 wordFlush.mockClear();fireEvent.click(screen.getByRole('button',{name:'关闭标签：起诉状'}));
 await waitFor(()=>expect(screen.queryByRole('tab',{name:'起诉状'})).toBeNull());
 expect(wordFlush).not.toHaveBeenCalled();expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
});

test('a failed neighbour load keeps closing tab usable for a later retry',async()=>{
 const next={...product,id:'next',title:'起诉状'};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product,next],errors:[]});
 vi.mocked(editorClient.read).mockImplementation(async(_w,id)=>id==='next'?next:product);
 mount();fireEvent.click(await screen.findByRole('tab',{name:'答辩状'}));await screen.findByRole('textbox',{name:'文书正文'});
 vi.mocked(editorClient.openWord).mockRejectedValueOnce(new Error('暂时无法打开下一份文书'));
 fireEvent.click(screen.getByRole('button',{name:'关闭标签：答辩状'}));await screen.findByText('暂时无法打开下一份文书');
 expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
 fireEvent.click(screen.getByRole('button',{name:'关闭标签：答辩状'}));
 await waitFor(()=>expect(screen.queryByRole('tab',{name:'答辩状'})).toBeNull());
 expect(screen.getByRole('tab',{name:'起诉状'}).getAttribute('aria-selected')).toBe('true');
});


test('file-list open takes precedence over saved restoration and a failed save preserves the current document',async()=>{
 const next={...product,id:'next',title:'起诉状'};
 localStorage.setItem('verilex:drafting:w',JSON.stringify({productId:'next',closedIds:['doc']}));
 vi.mocked(editorClient.list).mockResolvedValue({products:[product,next],errors:[]});
 vi.mocked(editorClient.read).mockImplementation(async(_w,id)=>id==='next'?next:product);
 const view=render(<LitigationDrafting {...props} openDocumentRequest={{id:'doc',sequence:1}}/>);
 await screen.findByRole('textbox',{name:'文书正文'});
 expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
 wordFlush.mockRejectedValueOnce(new Error('保存失败，保留正文'));
 view.rerender(<LitigationDrafting {...props} openDocumentRequest={{id:'next',sequence:2}}/>);
 await screen.findByText('保存失败，保留正文');
 expect(screen.getByRole('tab',{name:'答辩状'}).getAttribute('aria-selected')).toBe('true');
 view.rerender(<LitigationDrafting {...props} openDocumentRequest={{id:'next',sequence:3}}/>);
 await waitFor(()=>expect(screen.getByRole('tab',{name:'起诉状'}).getAttribute('aria-selected')).toBe('true'));
});

test('cross examination is drafted as one document from selected opponent files without mandatory extra requirements',async()=>{
 const cross={...product,title:'质证意见',document_type:'质证意见'};
 vi.mocked(editorClient.create).mockResolvedValue(cross);vi.mocked(editorClient.read).mockResolvedValue(cross);
 const materials=['对方证据目录.docx','对方证据册.pdf','答辩状.docx'].map(path=>({name:path,path,supported:true,reason:''}));
 render(<EditorDiscussionProvider workspaceId="w" threadId="t"><LitigationDrafting {...props} materials={materials}/></EditorDiscussionProvider>);
 fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'质证意见'}});
 expect(screen.getByRole('button',{name:'生成正文'})).toHaveProperty('disabled',false);
 expect(props.onControlsChange.mock.lastCall![0].hint).toContain('对方证据目录');
 for(const file of materials)act(()=>props.onControlsChange.mock.lastCall![0].toggle(file.path));
 expect(screen.getByRole('button',{name:'生成正文'})).toHaveProperty('disabled',false);
 fireEvent.click(screen.getByRole('button',{name:'生成正文'}));await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 const request=props.onStart.mock.lastCall![0];
 expect(request).toMatchObject({kind:'documents',materials:materials.map(m=>m.path),editorContext:{operation:'generate',product_id:'doc'}});
 expect(request.cross_revision).toBeUndefined();expect(request.evidence_revision).toBeUndefined();
 expect(request.message).toContain('文书类型：质证意见');
 expect(request.message).toContain('撰写要求：请起草质证意见。');
 expect(request.message).not.toContain('四列表格');
 expect(request.message).not.toContain('litigation_cross_next');
 expect(editorClient.create).toHaveBeenCalledWith('w','质证意见','documents',blank,'质证意见');
});

test('other document types can use default requirements too',async()=>{
 mount();fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'质证意见'}});
 act(()=>props.onControlsChange.mock.lastCall![0].toggle('合同.txt'));
 fireEvent.change(screen.getByRole('combobox',{name:'文书类型'}),{target:{value:'答辩状'}});
 expect(screen.getByRole('button',{name:'生成正文'})).toHaveProperty('disabled',false);
 expect(props.onControlsChange.mock.lastCall![0].hint).toBeUndefined();
 fireEvent.change(screen.getByRole('textbox',{name:'起草或修改要求'}),{target:{value:'针对合同提出答辩'}});
 fireEvent.click(screen.getByRole('button',{name:'生成正文'}));await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 expect(props.onStart.mock.lastCall![0].message).not.toContain('质证意见模板');
});


test('no selected files and no extra request allows drafting from relevant workspace files',async()=>{
 mount();fireEvent.click(screen.getByRole('button',{name:'生成正文'}));
 await waitFor(()=>expect(props.onStart).toHaveBeenCalledOnce());
 expect(props.onStart.mock.lastCall![0].materials).toEqual([]);
 expect(props.onStart.mock.lastCall![0].message).not.toContain('本轮未选择');
 expect(props.onStart.mock.lastCall![0].message).not.toContain('不扩读');
});


test('an unopened clean editor does not block a new document, but failed unsaved input does',async()=>{
 wordState.ready=false;
 mount();fireEvent.click(screen.getByRole('button',{name:'直接手动撰写'}));await screen.findByRole('textbox',{name:'文书正文'});
 wordState.dirty=true;fireEvent.click(screen.getByRole('tab',{name:'新建文书'}));
 await screen.findByText('文书有尚未保存的输入，请先恢复编辑器并保存。');
 expect(screen.getByRole('textbox',{name:'文书正文'})).toBeTruthy();
 wordState.dirty=false;fireEvent.click(screen.getByRole('tab',{name:'新建文书'}));
 await screen.findByRole('button',{name:'直接手动撰写'});
 expect(screen.queryByRole('textbox',{name:'文书正文'})).toBeNull();
});

test('hidden drafting does not restore its native editor until the document page is shown',async()=>{
 localStorage.setItem('verilex:drafting:w',JSON.stringify({productId:'doc'}));
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 const view=render(<LitigationDrafting {...props} visible={false}/>);
 expect(editorClient.openWord).not.toHaveBeenCalled();
 view.rerender(<LitigationDrafting {...props} visible/>);
 await screen.findByRole('textbox',{name:'文书正文'});expect(editorClient.openWord).toHaveBeenCalledOnce();
});
