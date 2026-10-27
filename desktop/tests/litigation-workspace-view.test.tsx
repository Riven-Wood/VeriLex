import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {LitigationWorkspaceView} from '../src/views/LitigationWorkspaceView';
import {litigationClient} from '../src/views/LitigationClient';
vi.mock('../src/views/LitigationClient', () => ({litigationClient:{snapshot:vi.fn(),materials:vi.fn(),saveCase:vi.fn()}}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const record={revision:0,data:null};
const snapshot={case:{revision:2,data:{title:'案件',cause:'',plaintiff:'',defendant:'',position:'',theory:'原理论'}},facts:record,evidence:record,issues:record,strategy:record};
const props={workspaces:[],selectedWorkspaceId:'workspace',onSelectWorkspace:vi.fn(),onChooseWorkspace:vi.fn(),onStartWorkflow:vi.fn(),busy:false};
test('first successful retry leaves materials unselected; later refresh preserves selection and unsaved case edits',async()=>{
 vi.mocked(litigationClient.snapshot).mockResolvedValue(snapshot);
 vi.mocked(litigationClient.materials).mockRejectedValueOnce(new Error('读取失败')).mockResolvedValue({materials:[{path:'材料.txt',name:'材料.txt',supported:true,reason:''}],truncated:false});
 render(<LitigationWorkspaceView {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'重新读取'}));
 const material=await screen.findByRole('checkbox',{name:'材料.txt'});
 await waitFor(()=>expect((material as HTMLInputElement).checked).toBe(false));
 fireEvent.click(material);
 fireEvent.click(screen.getByText(/^案件信息/));
 fireEvent.change(screen.getByRole('textbox',{name:'一句话案件理论'}),{target:{value:'尚未保存的修改'}});
 fireEvent.click(screen.getByRole('button',{name:'刷新'}));
 await waitFor(()=>expect(litigationClient.materials).toHaveBeenCalledTimes(3));
 expect((material as HTMLInputElement).checked).toBe(true);
 expect((screen.getByRole('textbox',{name:'一句话案件理论'}) as HTMLInputElement).value).toBe('尚未保存的修改');
});

test('legacy evidence remains readable while retired issue page returns to facts',async()=>{
 const source={path:'材料.txt',page:null,quote:'原文'};
 const data={...snapshot,evidence:{revision:1,data:{evidence:[{id:'e1',name:'合同',group:'成立',kind:'书证',purpose:'证明成立',sources:[source],strength:'weak',assessment:'需要原件'}],gaps:['缺少转账凭证'],contradictions:['签订日期不一致'],suggestions:['补充原件']}},issues:{revision:1,data:{issues:[{title:'是否成立',elements:[{name:'合意',supporting:[source],opposing:[],strength:'weak',gap:'缺少签字'}],laws:[]}]}}};
 vi.mocked(litigationClient.snapshot).mockResolvedValue(data);
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[],truncated:false});
 const view=render(<LitigationWorkspaceView {...props} page="evidence"/>);
 expect(await screen.findByText('缺少转账凭证')).toBeTruthy();
 expect(screen.getByText('签订日期不一致')).toBeTruthy();
 expect(screen.getByText('证明力：弱')).toBeTruthy();
 view.rerender(<LitigationWorkspaceView {...props} page="issues"/>);
 expect(screen.queryByText('支持程度：弱 · 请律师复核')).toBeNull();
 expect(screen.getByRole('button',{name:'开始事实梳理'})).toBeTruthy();
});

test('case suggestions require individual adoption and explicit save; refresh preserves edits',async()=>{
 const source={path:'材料.txt',page:null,quote:'应支付货款'};
 const data={...snapshot,case_suggestions:{revision:1,data:{fields:[{key:'cause' as const,value:'买卖合同纠纷',sources:[source],reason:'依据货款请求'}],gaps:['当事人姓名待补'],summary:'候选',case_revision:2}}};
 vi.mocked(litigationClient.snapshot).mockResolvedValue(data);
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[{path:'材料.txt',name:'材料.txt',supported:true,reason:''}],truncated:false});
 vi.mocked(litigationClient.saveCase).mockResolvedValue({});
 render(<LitigationWorkspaceView {...props} workspaces={[{id:'workspace',allow_read:true,allow_write:true,display_name:'虚构案'} as any]} page="overview"/>);
 fireEvent.click(await screen.findByRole('checkbox',{name:'材料.txt'}));
 fireEvent.click(await screen.findByRole('button',{name:'从所选材料识别案件信息'}));
 expect(props.onStartWorkflow).toHaveBeenCalledWith(expect.stringContaining('case_suggestions'));
 expect((screen.getByRole('textbox',{name:'案由'}) as HTMLInputElement).value).toBe('');
 fireEvent.click(screen.getByRole('button',{name:'采用案由'}));
 expect(litigationClient.saveCase).not.toHaveBeenCalled();
 fireEvent.change(screen.getByRole('textbox',{name:'案由'}),{target:{value:'人工修改案由'}});
 fireEvent.click(screen.getByRole('button',{name:'刷新'}));
 await waitFor(()=>expect(litigationClient.snapshot).toHaveBeenCalledTimes(2));
 expect((screen.getByRole('textbox',{name:'案由'}) as HTMLInputElement).value).toBe('人工修改案由');
 fireEvent.click(screen.getByRole('button',{name:'保存案件信息'}));
 await waitFor(()=>expect(litigationClient.saveCase).toHaveBeenCalledWith('workspace',expect.objectContaining({cause:'人工修改案由',theory:'原理论'}),2));
});

test('outdated case suggestions cannot replace current information',async()=>{
 vi.mocked(litigationClient.snapshot).mockResolvedValue({...snapshot,case_suggestions:{revision:1,data:{fields:[{key:'cause',value:'旧候选',sources:[],reason:''}],gaps:[],summary:'候选',case_revision:1}}});
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[],truncated:false});
 render(<LitigationWorkspaceView {...props} page="overview"/>);
 expect((await screen.findByRole('button',{name:'采用案由'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.getByText('案件信息已有更新，请重新识别后再采用候选。')).toBeTruthy();
});

test('retired issue page no longer exposes analysis or export actions',async()=>{
 vi.mocked(litigationClient.snapshot).mockResolvedValue({...snapshot,issues:{revision:1,data:{issues:[],dependencies:{case:2,facts:1,evidence:0}}}});
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[],truncated:false});
 render(<LitigationWorkspaceView {...props} page="issues"/>);
 await waitFor(()=>expect(litigationClient.snapshot).toHaveBeenCalled());
 expect(screen.queryByRole('button',{name:'导出产物'})).toBeNull();
 expect(screen.queryByRole('button',{name:'开始争议焦点'})).toBeNull();
});

test('facts start without selected materials or saved case edits',async()=>{
 vi.mocked(litigationClient.snapshot).mockResolvedValue(snapshot);
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[],truncated:false});
 const view=render(<LitigationWorkspaceView {...props} page="overview"/>);
 fireEvent.change(await screen.findByRole('textbox',{name:'案件名称'}),{target:{value:'未保存名称'}});
 view.rerender(<LitigationWorkspaceView {...props} page="facts"/>);
 const start=await screen.findByRole('button',{name:'开始事实梳理'});
 expect(start).toHaveProperty('disabled',false);
 fireEvent.click(start);
 expect(props.onStartWorkflow).toHaveBeenCalledOnce();
 expect(props.onStartWorkflow.mock.lastCall![0]).not.toContain('未选择材料');
 expect(props.onStartWorkflow).toHaveBeenCalledWith(expect.stringContaining('不自动保存旧结构化记录'));
 expect(litigationClient.saveCase).not.toHaveBeenCalled();
});

test('guide edits survive overview and other workflow pages and are isolated by workspace',async()=>{
 vi.mocked(litigationClient.snapshot).mockResolvedValue(snapshot);
 vi.mocked(litigationClient.materials).mockResolvedValue({materials:[],truncated:false});
 const view=render(<LitigationWorkspaceView {...props} page="facts"/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'开始事实梳理'})).toHaveProperty('disabled',false));
 fireEvent.change(screen.getByRole('textbox',{name:'本次要求'}),{target:{value:'仅梳理付款'}});
 fireEvent.click(screen.getAllByText('查看或调整工作指引')[0]);
 fireEvent.change(screen.getByRole('textbox',{name:'工作指引'}),{target:{value:'只列有来源的日期'}});
 view.rerender(<LitigationWorkspaceView {...props} page="overview"/>);
 view.rerender(<LitigationWorkspaceView {...props} page="evidence"/>);
 expect(screen.getByRole('textbox',{name:'本次要求'})).toHaveProperty('value','');
 view.rerender(<LitigationWorkspaceView {...props} page="facts"/>);
 expect(screen.getByRole('textbox',{name:'本次要求'})).toHaveProperty('value','仅梳理付款');
 fireEvent.click(screen.getByRole('button',{name:'开始事实梳理'}));
 expect(props.onStartWorkflow).toHaveBeenCalledWith(expect.stringContaining('只列有来源的日期'));
 fireEvent.click(screen.getByRole('button',{name:'恢复默认指引'}));
 expect(screen.getByRole('textbox',{name:'本次要求'})).toHaveProperty('value','仅梳理付款');
 view.rerender(<LitigationWorkspaceView {...props} selectedWorkspaceId="other" page="facts"/>);
 expect(screen.getByRole('textbox',{name:'本次要求'})).toHaveProperty('value','');
});
