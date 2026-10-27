import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {EvidenceDirectory} from '../src/components/editor/EvidenceDirectory';
import {runtimeRequest} from '../src/runtime/api';
import type {LitigationMaterialControls} from '../src/views/LitigationWorkspaceView';
vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
let saved:any,controls:LitigationMaterialControls;
const materials=['甲.pdf','乙.docx','照片.png'].map(path=>({path,name:path,supported:path!=='照片.png',book_supported:true,reason:''}));
const props={workspaceId:'w1',canWrite:true,visible:true,busy:false,materials,workRevision:3,onStart:vi.fn(),onOpen:vi.fn(),onControlsChange:(v:LitigationMaterialControls)=>{controls=v;}};
beforeEach(()=>{
 saved={revision:0,data:{title:'证据目录',entries:[]}};
 vi.mocked(runtimeRequest).mockImplementation(async (url,options)=>{
  const body=options?.body?JSON.parse(String(options.body)):null;
  if(options?.method==='PUT'){saved={revision:saved.revision+1,data:body.data};return structuredClone(saved);}
  if(url.endsWith('/prepare')){saved={...saved,revision:saved.revision+1,data:{...saved.data,entries:saved.data.entries.map((e:any)=>({...e,page_count:2}))}};return structuredClone(saved);}
  if(url.includes('/export/'))return {path:'文书起草/证据册-test.pdf',record:structuredClone(saved)};
  return structuredClone(saved);
 });
});
afterEach(()=>{cleanup();vi.clearAllMocks();});
test('explicit selection order, up/down and removal persist, booklet export never starts AI',async()=>{
 const view=render(<EvidenceDirectory {...props}/>);
 await screen.findByText('在左侧依次勾选文件。选中顺序就是装册顺序，可在这里拖动调整。');
 await waitFor(()=>expect(controls.locked).toBe(false));
 act(()=>controls.toggle('乙.docx'));act(()=>controls.toggle('照片.png'));act(()=>controls.toggle('甲.pdf'));
 expect(controls.selected).toEqual(['乙.docx','照片.png','甲.pdf']);
 expect(controls.materials.find(m=>m.path==='照片.png')?.supported).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'上移证据 3'}));
 expect(controls.selected).toEqual(['乙.docx','甲.pdf','照片.png']);
 fireEvent.click(screen.getByRole('button',{name:'移除证据 2'}));
 await waitFor(()=>expect(saved.data.entries.map((e:any)=>e.path)).toEqual(['乙.docx','照片.png']));
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成 PDF 证据册'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:'生成 PDF 证据册'}));
 expect(await screen.findByText('证据册-test.pdf')).toBeTruthy();expect(props.onStart).not.toHaveBeenCalled();
 view.unmount();render(<EvidenceDirectory {...props}/>);
 await waitFor(()=>expect(controls.selected).toEqual(['乙.docx','照片.png']));
});
test('directory starts a bound sequential task and manual text survives reorder',async()=>{
 saved={revision:1,data:{title:'证据目录',entries:materials.slice(0,2).map(m=>({name:m.name,path:m.path,first:1,last:null,status:'pending'}))}};
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(2));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 fireEvent.click(screen.getByRole('button',{name:/1.*甲.pdf/}));
 fireEvent.change(screen.getByRole('textbox',{name:'证据 1 证明目的'}),{target:{value:'律师已核对的证明目的'}});
 await waitFor(()=>expect(saved.data.entries[0].manual).toBe(true));
 await waitFor(()=>expect(screen.getByRole('button',{name:/AI 填写目录/})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:/AI 填写目录/}));
 await waitFor(()=>expect(props.onStart).toHaveBeenCalledWith(expect.objectContaining({evidence_revision:3,materials:['甲.pdf','乙.docx'],expected_revision:3})));
 expect(saved.data.entries[0].purpose).toBe('律师已核对的证明目的');
 expect(props.onStart.mock.calls[0][0].message).toContain('litigation_evidence_next');
});
test('conversion error does not show a successful download',async()=>{
 saved={revision:1,data:{title:'证据目录',entries:[{name:'甲',path:'甲.pdf',first:1,last:null}]}};
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(1));
 vi.mocked(runtimeRequest).mockRejectedValueOnce(Error('甲：PDF 已加密'));
 fireEvent.click(screen.getByRole('button',{name:'生成 PDF 证据册'}));
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('PDF 已加密'));
 expect(screen.queryByText('证据册-test.pdf')).toBeNull();
});


test('batch grouping, per-group numbering and group removal preserve evidence and manual text',async()=>{
 saved={revision:1,data:{title:'目录',entries:materials.map(m=>({name:m.name,path:m.path,first:1,last:null,purpose:'人工目的',manual_fields:['purpose']})),groups:[]}};
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(3));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 fireEvent.click(screen.getByRole('checkbox',{name:'选择证据 2'}));fireEvent.click(screen.getByRole('checkbox',{name:'选择证据 3'}));
 fireEvent.click(screen.getByRole('button',{name:'新建分组'}));
 expect(controls.selected).toEqual(['甲.pdf','乙.docx','照片.png']);
 fireEvent.change(screen.getByRole('textbox',{name:'第1组证据 共同证明目的'}),{target:{value:'证明合同履行'}});
 fireEvent.change(screen.getByRole('combobox',{name:'第1组证据 编号方式'}),{target:{value:'restart'}});
 fireEvent.change(screen.getByRole('spinbutton',{name:'第1组证据 起始编号'}),{target:{value:'40'}});
 await waitFor(()=>expect(saved.data.groups[0]).toMatchObject({numbering:'restart',start_number:40,purpose:'证明合同履行'}));
 expect(saved.data.entries.every((row:any)=>row.purpose==='人工目的')).toBe(true);
 await waitFor(()=>expect(screen.getByRole('button',{name:'删除第1组证据分组'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:'删除第1组证据分组'}));
 await waitFor(()=>expect(saved.data.groups).toHaveLength(1));expect(saved.data.entries).toHaveLength(3);
 expect(screen.queryByRole('textbox',{name:/共同证明目的/})).toBeNull();
});


test('card drag preserves edits, moves across groups including empty groups and persists the booklet order',async()=>{
 const capture=vi.fn(),release=vi.fn();
 Object.defineProperty(HTMLElement.prototype,'setPointerCapture',{configurable:true,value:capture});
 Object.defineProperty(HTMLElement.prototype,'releasePointerCapture',{configurable:true,value:release});
 const original=window.PointerEvent;window.PointerEvent=MouseEvent as typeof PointerEvent;
 const hit=vi.fn();Object.defineProperty(document,'elementFromPoint',{configurable:true,value:hit});
 saved={revision:1,data:{title:'目录',entries:materials.map(m=>({name:m.name,path:m.path,first:1,last:null,purpose:'人工证明目的',manual_fields:['purpose'],group:'g1'})),groups:[{id:'g1',name:'合同组',purpose:'',numbering:'continue',start_number:1},{id:'g2',name:'付款组',purpose:'',numbering:'restart',start_number:20}]}};
 const view=render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(3));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 const cards=()=>Array.from(document.querySelectorAll('.evidence-card'));
 vi.spyOn(cards()[0],'getBoundingClientRect').mockReturnValue({top:10,bottom:30,height:20,left:0,right:100,width:100} as DOMRect);
 hit.mockReturnValue(cards()[0]);
 let handle=screen.getByRole('button',{name:'拖动目录证据 3'});
 fireEvent.pointerDown(handle,{button:0,pointerId:1,clientX:0,clientY:0});fireEvent.pointerMove(window,{pointerId:1,clientX:10,clientY:11});
 expect(cards()[0].getAttribute('data-drop-edge')).toBe('before');
 expect(cards()[2].getAttribute('data-dragging')).toBe('true');
 fireEvent.pointerUp(window,{button:0,pointerId:1,clientX:10,clientY:11});
 expect(controls.selected).toEqual(['照片.png','甲.pdf','乙.docx']);
 hit.mockReturnValue(screen.getByRole('region',{name:'付款组'}));
 handle=screen.getByRole('button',{name:'拖动目录证据 2'});
 fireEvent.pointerDown(handle,{button:0,pointerId:2,clientY:0});fireEvent.pointerMove(window,{pointerId:2,clientX:10,clientY:50});fireEvent.pointerUp(window,{button:0,pointerId:2,clientX:10,clientY:50});
 expect(controls.selected).toEqual(['照片.png','乙.docx','甲.pdf']);
 await waitFor(()=>expect(saved.data.entries[2]).toMatchObject({path:'甲.pdf',group:'g2',purpose:'人工证明目的'}));
 expect(cards()[2].querySelector('.evidence-card-number')?.textContent).toBe('20');
 view.unmount();render(<EvidenceDirectory {...props}/>);
 await waitFor(()=>expect(controls.selected).toEqual(['照片.png','乙.docx','甲.pdf']));
 expect(screen.getByRole('button',{name:'上移证据 3'})).toBeTruthy();
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 fireEvent.click(screen.getByRole('button',{name:/20.*甲.pdf/}));
 expect(screen.getByRole('textbox',{name:'证据 3 证明目的'})).toHaveProperty('value','人工证明目的');
 fireEvent.click(screen.getByRole('button',{name:/20.*甲.pdf/}));
 expect(screen.queryByRole('textbox',{name:'证据 3 证明目的'})).toBeNull();
 window.PointerEvent=original;
});


test('one group renders as an ungrouped sequential directory with equally sized fields',async()=>{
 saved={revision:1,data:{title:'目录',groups:[{id:'g',name:'旧第一组',purpose:'旧共同目的',numbering:'restart',start_number:40}],entries:materials.map(m=>({name:m.name,path:m.path,group:'g',first:1,last:null,purpose:'每条证明目的'}))}};
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(3));fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 expect(screen.queryByRole('textbox',{name:/分组名称|共同证明目的/})).toBeNull();
 expect([...document.querySelectorAll('.evidence-card-number')].map(n=>n.textContent)).toEqual(['1','2','3']);
 fireEvent.click(screen.getByRole('button',{name:/1.*甲.pdf/}));
 expect(screen.getByRole('textbox',{name:'证据 1 证明目的'})).toHaveProperty('value','每条证明目的');
 expect([...document.querySelectorAll('.evidence-card-fields textarea')].map(n=>n.getAttribute('rows'))).toEqual(['3','3','3','3']);
 expect(saved.data.groups[0].purpose).toBe('旧共同目的');
});

test('drag supports lower insertion edge and Escape or outside drops never commit',async()=>{
 const original=window.PointerEvent;window.PointerEvent=MouseEvent as typeof PointerEvent;
 const hit=vi.fn();Object.defineProperty(document,'elementFromPoint',{configurable:true,value:hit});
 saved={revision:1,data:{title:'目录',entries:materials.map(m=>({name:m.name,path:m.path,first:1,last:null}))}};
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(3));fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 const target=document.querySelectorAll<HTMLElement>('.evidence-card')[2];
 vi.spyOn(target,'getBoundingClientRect').mockReturnValue({top:10,bottom:30,height:20,left:0,right:100,width:100} as DOMRect);hit.mockReturnValue(target);
 const start=()=>{fireEvent.pointerDown(screen.getByRole('button',{name:'拖动目录证据 1'}),{button:0,clientY:0});fireEvent.pointerMove(window,{clientX:10,clientY:28});};
 start();expect(target.getAttribute('data-drop-edge')).toBe('after');fireEvent.keyDown(window,{key:'Escape'});
 expect(controls.selected).toEqual(['甲.pdf','乙.docx','照片.png']);
 start();hit.mockReturnValue(document.body);fireEvent.pointerUp(window,{clientX:10,clientY:28});
 expect(controls.selected).toEqual(['甲.pdf','乙.docx','照片.png']);
 hit.mockReturnValue(target);start();fireEvent.pointerUp(window,{clientX:10,clientY:28});
 expect(controls.selected).toEqual(['乙.docx','照片.png','甲.pdf']);
 window.PointerEvent=original;
});

test('directory removes an expanded card and selected cards, preserving remaining edits and persisted selection',async()=>{
 saved={revision:1,data:{title:'目录',entries:materials.map(m=>({name:m.name,path:m.path,first:1,last:null,page_count:2,purpose:'人工证明目的',manual_fields:['purpose']}))}};
 const view=render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(controls.selected).toHaveLength(3));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 expect(screen.getByRole('button',{name:'移除所选'})).toHaveProperty('disabled',true);
 fireEvent.click(screen.getByRole('button',{name:/1.*甲.pdf/}));
 fireEvent.click(screen.getByRole('button',{name:'移除目录证据 1'}));
 expect(screen.queryByRole('textbox',{name:'证据 1 证明目的'})).toBeNull();
 expect(controls.selected).toEqual(['乙.docx','照片.png']);
 expect([...document.querySelectorAll('.evidence-card-number')].map(n=>n.textContent)).toEqual(['1','2']);
 expect(screen.getByRole('button',{name:/1.*乙.docx/}).textContent).toContain('1—2 页');
 await waitFor(()=>expect(saved.data.entries.map((e:any)=>e.path)).toEqual(['乙.docx','照片.png']));
 await waitFor(()=>expect(screen.getByRole('checkbox',{name:'选择证据 2'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('checkbox',{name:'选择证据 2'}));
 fireEvent.click(screen.getByRole('button',{name:'移除所选'}));
 await waitFor(()=>expect(saved.data.entries).toHaveLength(1));
 expect(saved.data.entries[0]).toMatchObject({path:'乙.docx',purpose:'人工证明目的',manual_fields:['purpose']});
 expect(screen.getByRole('button',{name:'移除所选'})).toHaveProperty('disabled',true);
 view.unmount();render(<EvidenceDirectory {...props}/>);
 await waitFor(()=>expect(controls.selected).toEqual(['乙.docx']));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 fireEvent.click(screen.getByRole('checkbox',{name:'选择全部证据'}));
 fireEvent.click(screen.getByRole('button',{name:'移除所选'}));
 await waitFor(()=>expect(saved.data.entries).toEqual([]));
 expect(controls.selected).toEqual([]);
 expect(props.onStart).not.toHaveBeenCalled();
 expect(vi.mocked(runtimeRequest).mock.calls.some(([,options])=>options?.method==='DELETE')).toBe(false);
});

test.each([{canWrite:false,busy:false},{canWrite:true,busy:true}])('directory removal respects write and running locks: %j',async restriction=>{
 saved={revision:1,data:{title:'目录',entries:[{name:'甲.pdf',path:'甲.pdf',first:1,last:null}]}};
 render(<EvidenceDirectory {...props} {...restriction}/>);await waitFor(()=>expect(controls.selected).toHaveLength(1));
 fireEvent.click(screen.getByRole('tab',{name:'证据目录'}));
 expect(screen.getByRole('button',{name:'移除目录证据 1'})).toHaveProperty('disabled',true);
 expect(screen.getByRole('button',{name:'移除所选'})).toHaveProperty('disabled',true);
 fireEvent.click(screen.getByRole('button',{name:'移除目录证据 1'}));
 expect(controls.selected).toEqual(['甲.pdf']);
});

test('evidence preparation only exposes booklet and directory and does not load legacy cross records',async()=>{
 render(<EvidenceDirectory {...props}/>);await waitFor(()=>expect(runtimeRequest).toHaveBeenCalled());
 expect(screen.getAllByRole('tab').map(tab=>tab.textContent)).toEqual(['证据册','证据目录']);
 expect(screen.queryByRole('region',{name:'质证意见工作台'})).toBeNull();
 expect(vi.mocked(runtimeRequest).mock.calls.every(([url])=>!url.includes('cross-examination'))).toBe(true);
});
