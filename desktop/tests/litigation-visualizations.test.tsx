import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {LitigationVisualizations} from '../src/views/LitigationVisualizations';
import {runtimeRequest} from '../src/runtime/api';
import {diagramViewport} from '../src/platform/diagramViewport';
import {diagramFilename,saveDiagramExport} from '../src/platform/diagramExport';
vi.mock('../src/platform/diagramViewport',()=>({diagramViewport:vi.fn(async content=>({content,width:800,height:600,minFont:16}))}));
vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
vi.mock('../src/components/preview/DiagramSources',()=>({DiagramSources:()=>null}));
vi.mock('../src/platform/diagramExport',async original=>({...await original<typeof import('../src/platform/diagramExport')>(),saveDiagramExport:vi.fn()}));
const first='文书起草/图表-1234abcd.svg',second='文书起草/图表-abcd1234.svg';
const diagram={path:first,title:'案件大事记',kind:'chronicle',pages:[first,second],updated_at:1};
const props={workspaceId:'w',visible:true,busy:false,canWrite:true,ready:true,refreshKey:0,onGenerate:vi.fn()};
const content=btoa('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200"><text x="20" y="40" textLength="420" lengthAdjust="spacing">Long timeline paragraph</text></svg>');
beforeEach(()=>{vi.mocked(diagramViewport).mockImplementation(async content=>({content,width:800,height:600,minFont:16}));vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}});localStorage.clear();vi.mocked(runtimeRequest).mockImplementation(async path=>path.endsWith('/diagrams')?{diagrams:[diagram],errors:[],truncated:false}:path.includes('/diagram-archive')?{content:btoa('PKarchive')}:{path:path.includes(encodeURIComponent(second))?second:first,content,file_hash:'a'.repeat(64)});vi.mocked(saveDiagramExport).mockResolvedValue('/saved/chart.svg');});
afterEach(()=>{cleanup();vi.resetAllMocks();});
test('empty workspace exposes chart types and sends the selected request without inventing content',async()=>{
 vi.mocked(runtimeRequest).mockResolvedValue({diagrams:[],errors:[],truncated:false});render(<LitigationVisualizations {...props}/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'生成时间轴'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:'关系图'}));
 expect(screen.getByRole('textbox',{name:'出图要求'}).closest('details')).toBeNull();
 fireEvent.change(screen.getByRole('textbox',{name:'出图要求'}),{target:{value:'只画合同主体'}});
 fireEvent.click(screen.getByRole('button',{name:'生成关系图'}));
 expect(props.onGenerate).toHaveBeenCalledWith('生成关系图',expect.stringContaining('diagram.kind 为 relationship'));
 expect(props.onGenerate).toHaveBeenCalledWith('生成关系图',expect.stringContaining('只画合同主体'));
});
test('current page SVG and complete group archive use different verified endpoints',async()=>{
 render(<LitigationVisualizations {...props}/>);await screen.findByRole('img',{name:'案件大事记'});
 fireEvent.click(screen.getByRole('button',{name:'下一页'}));
 await waitFor(()=>expect(vi.mocked(runtimeRequest).mock.calls.some(([path])=>path.includes('/diagram-preview')&&path.includes(encodeURIComponent(second)))).toBe(true));
 fireEvent.click(screen.getByText('导出',{exact:true}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'导出 SVG'})).toHaveProperty('disabled',false));
 fireEvent.click(screen.getByRole('button',{name:'导出 SVG'}));
 await waitFor(()=>expect(saveDiagramExport).toHaveBeenCalledWith('案件大事记-第2页.svg',expect.any(Uint8Array)));
 expect(new TextDecoder().decode(vi.mocked(saveDiagramExport).mock.calls[0][1])).toBe(atob(content));
 await screen.findByText('已保存：/saved/chart.svg');
 fireEvent.click(screen.getByText('导出',{exact:true}));fireEvent.click(screen.getByRole('button',{name:'导出整组 SVG'}));
 await waitFor(()=>expect(saveDiagramExport).toHaveBeenCalledWith('案件大事记.zip',expect.any(Uint8Array)));
 expect(vi.mocked(runtimeRequest).mock.calls.some(([path])=>path.includes('/diagram-archive'))).toBe(true);
});
test('cancel and export failures never display a successful save',async()=>{
 vi.mocked(saveDiagramExport).mockResolvedValueOnce(null).mockRejectedValueOnce(new Error('磁盘不可写'));
 render(<LitigationVisualizations {...props}/>);await screen.findByRole('img');fireEvent.click(screen.getByText('导出',{exact:true}));fireEvent.click(screen.getByRole('button',{name:'导出 SVG'}));
 await waitFor(()=>expect(saveDiagramExport).toHaveBeenCalledTimes(1));await waitFor(()=>expect(screen.queryByText('正在导出…')).toBeNull());expect(screen.queryByText(/已保存/)).toBeNull();
 fireEvent.click(screen.getByText('导出',{exact:true}));fireEvent.click(screen.getByRole('button',{name:'导出 SVG'}));expect(await screen.findByRole('alert')).toHaveProperty('textContent','磁盘不可写');expect(screen.queryByText(/已保存/)).toBeNull();
});
test('redraw requires an explicit change request and preserves the exact source hash',async()=>{
 render(<LitigationVisualizations {...props}/>);await screen.findByRole('img');fireEvent.click(screen.getByLabelText('图表操作'));fireEvent.click(screen.getByRole('button',{name:'修改当前图'}));
 expect(screen.getByRole('button',{name:'重画当前图'})).toHaveProperty('disabled',true);
 fireEvent.change(screen.getByRole('textbox',{name:'修改要求'}),{target:{value:'突出付款节点'}});fireEvent.click(screen.getByRole('button',{name:'重画当前图'}));
 expect(props.onGenerate).toHaveBeenCalledWith('重画当前图',expect.stringContaining(`source_diagram_path 为 ${first}`));
 expect(props.onGenerate).toHaveBeenCalledWith('重画当前图',expect.stringContaining('a'.repeat(64)));
});
test('malformed list fails locally and cannot start generation',async()=>{
 vi.mocked(runtimeRequest).mockResolvedValue({});render(<LitigationVisualizations {...props}/>);
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('图表列表格式无效'));expect(screen.getByRole('button',{name:'生成时间轴'})).toHaveProperty('disabled',true);
});
test('safe filenames remove path syntax and keep UTF-8 names bounded',()=>{expect(diagramFilename('../案件/关系:图','svg')).toBe('案件关系图.svg');expect(diagramFilename('','png')).toBe('诉讼图表.png');expect(new TextEncoder().encode(diagramFilename('文'.repeat(100),'svg')).length).toBeLessThan(240);});
