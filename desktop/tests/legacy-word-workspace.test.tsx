import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {EditableProducts} from '../src/components/editor/EditableProducts';
import {editorClient} from '../src/components/editor/client';

vi.mock('../src/components/editor/client',()=>({editorClient:{list:vi.fn(),read:vi.fn(),openWord:vi.fn(),openWordHistory:vi.fn(),history:vi.fn(),saveWord:vi.fn(),copy:vi.fn(),draft:vi.fn()}}));
vi.mock('../src/components/editor/LitigationDocumentEditor',()=>({LitigationDocumentEditor:()=> <div>不完整的正文投影</div>}));
vi.mock('../src/components/wordEditor/WordDocumentEditor',async()=>{
  const React=await import('react');
  return {WordDocumentEditor:React.forwardRef(({onReady,tracking,editable}:any,ref:any)=>{
    React.useEffect(()=>{onReady(true);return()=>onReady(false);},[onReady]);
    React.useImperativeHandle(ref,()=>({flush:async()=>{},isDirty:()=>false}));
    return <div>原格式 Word 编辑器：{tracking?'修订开':'修订关'} · {editable?'可编辑':'只读'}</div>;
  })};
});
afterEach(()=>{cleanup();vi.clearAllMocks();localStorage.clear();});

test('复杂文档用原 Word 编辑器打开，并以原包另存副本',async()=>{
 const doc={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'正文'}]}]};
 const product={id:'a'.repeat(32),title:'复杂文档',kind:'facts',document:doc,file_hash:'b'.repeat(64),revision:1,updated_at:'t',external_update:false,path:'文书起草/复杂文档.docx',native_word:true,projection_complete:false} as any;
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(product);
 vi.mocked(editorClient.openWord).mockResolvedValue({product,content:'QQ==',tracking_enabled:true});
 vi.mocked(editorClient.copy).mockResolvedValue({...product,id:'c'.repeat(32),title:'复杂文档（副本）'});
 localStorage.setItem('verilex:editor-product:w:facts',product.id);
 render(<EditableProducts workspaceId="w" kind="facts" visible canWrite onActive={()=>{}}/>);
 expect(await screen.findByText('原格式 Word 编辑器：修订开 · 可编辑')).toBeTruthy();
 expect(screen.getByText(/自动分析暂不使用可能不完整的正文/)).toBeTruthy();
 expect(screen.queryByText('不完整的正文投影')).toBeNull();
 fireEvent.click(screen.getByText('更多',{selector:'summary'}));
 fireEvent.click(screen.getByRole('button',{name:'另存为'}));
 await waitFor(()=>expect(editorClient.copy).toHaveBeenCalledWith('w',product.id,product.file_hash,doc,'复杂文档（副本）'));
});

test('复杂 Word 的历史版本以只读原包打开',async()=>{
 const doc={type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'正文'}]}]};
 const product={id:'d'.repeat(32),title:'复杂文档',kind:'facts',document:doc,file_hash:'e'.repeat(64),revision:2,updated_at:'t',external_update:false,path:'文书起草/复杂文档.docx',native_word:true,projection_complete:false} as any;
 const old={...product,revision:1,file_hash:'f'.repeat(64)};
 vi.mocked(editorClient.list).mockResolvedValue({products:[product],errors:[]});
 vi.mocked(editorClient.read).mockResolvedValue(product);
 vi.mocked(editorClient.openWord).mockResolvedValue({product,content:'QQ==',tracking_enabled:false});
 vi.mocked(editorClient.history).mockResolvedValue({versions:[old]});
 vi.mocked(editorClient.openWordHistory).mockResolvedValue({product:old,content:'QQ==',tracking_enabled:true});
 localStorage.setItem('verilex:editor-product:w:facts',product.id);
 render(<EditableProducts workspaceId="w" kind="facts" visible canWrite onActive={()=>{}}/>);
 await screen.findByText('原格式 Word 编辑器：修订关 · 可编辑');
 fireEvent.click(screen.getByText('更多',{selector:'summary'}));fireEvent.click(screen.getByRole('button',{name:'历史'}));
 fireEvent.change(await screen.findByRole('combobox',{name:'编辑历史版本'}),{target:{value:'1'}});
 expect(await screen.findByText('原格式 Word 编辑器：修订开 · 只读')).toBeTruthy();
 expect(editorClient.openWordHistory).toHaveBeenCalledWith('w',product.id,1,old.file_hash);
});
