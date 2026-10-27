import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {LitigationDocuments} from '../src/views/LitigationDocuments';
import {openCaseDatabase} from '../src/platform/caseDatabase';
vi.mock('../src/platform/caseDatabase',()=>({openCaseDatabase:vi.fn()}));
import {litigationClient,type LitigationDocument,type LitigationRecord} from '../src/views/LitigationClient';
vi.mock('../src/views/LitigationClient',()=>({litigationClient:{documentTemplates:vi.fn(),confirmDocument:vi.fn()}}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const definition={id:'civil-person',name:'普通民事起诉状（自然人）',source_url:'https://www.court.gov.cn/susongyangshi/xiangqing/181.html',version:'1',fields:[{key:'plaintiff_name',label:'原告姓名',hint:'',required:true,kind:'text'}]};
const record:LitigationRecord<LitigationDocument>={revision:1,data:{template_id:'civil-person',plaintiff_count:1,defendant_count:1,fields:[{key:'plaintiff_name',value:'虚构甲',sources:[],pending:['来源待核对']}],laws:[],summary:'合成',confirmed:false,dependencies:{case:0}}};
const props={workspaceId:'w',visible:true,record,disabled:false,selectedMaterials:['合同.txt'],onStart:vi.fn(),onSaved:vi.fn()};
beforeEach(()=>{vi.mocked(litigationClient.documentTemplates).mockResolvedValue({templates:[definition]});});

test('official template button works independently of model and catalogue; open failure can be retried',async()=>{
 vi.mocked(litigationClient.documentTemplates).mockRejectedValue(new Error('目录不可用'));
 vi.mocked(openCaseDatabase).mockRejectedValueOnce(new Error('打开失败')).mockResolvedValueOnce(undefined);
 render(<LitigationDocuments {...props} disabled record={{revision:0,data:null}}/>);
 fireEvent.click(screen.getByRole('button',{name:'最高法文书模板'}));
 expect(await screen.findByText('系统浏览器没有打开，请再次点击“最高法文书模板”重试。')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'最高法文书模板'}));
 await waitFor(()=>expect(openCaseDatabase).toHaveBeenCalledTimes(2));
 expect(openCaseDatabase).toHaveBeenLastCalledWith('templates');
 expect(props.onStart).not.toHaveBeenCalled();
 expect(litigationClient.confirmDocument).not.toHaveBeenCalled();
});

test('upstream change blocks previously confirmed document until prepared again',async()=>{
 const saved={...record,data:{...record.data!,confirmed:true,fields:[{...record.data!.fields[0],pending:[]}]}};
 render(<LitigationDocuments {...props} record={saved} stale/>);
 expect((await screen.findByRole('button',{name:'导出 Word'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.getByText('案件资料已有更新，请点“重新准备要素”，再核对并出稿。')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'重新准备要素'}));
 expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('请准备本案文书要素'));
 expect(litigationClient.confirmDocument).not.toHaveBeenCalled();
});

test('missing review blocks export; failed save keeps content and never starts export',async()=>{
  vi.mocked(litigationClient.confirmDocument).mockRejectedValue(new Error('案件已有更新'));
  render(<LitigationDocuments {...props}/>);
  expect((await screen.findByRole('button',{name:'确认要素并生成 Word'})) as HTMLButtonElement).toHaveProperty('disabled',true);
  fireEvent.click(screen.getByRole('checkbox',{name:'已核对原告姓名'}));
  fireEvent.click(screen.getByRole('button',{name:'确认要素并生成 Word'}));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('案件已有更新'));
  expect(props.onStart).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox',{name:'原告姓名'})).toHaveProperty('value','虚构甲');
});

test('confirmation completes before asking the existing agent to export',async()=>{
  let done!:(v:LitigationRecord<LitigationDocument>)=>void;
  vi.mocked(litigationClient.confirmDocument).mockReturnValue(new Promise(resolve=>{done=resolve;}));
  render(<LitigationDocuments {...props}/>);
  fireEvent.click(await screen.findByRole('checkbox',{name:'已核对原告姓名'}));
  fireEvent.click(screen.getByRole('button',{name:'确认要素并生成 Word'}));
  expect(props.onStart).not.toHaveBeenCalled();
  done({revision:2,data:{...record.data!,confirmed:true,fields:[{...record.data!.fields[0],pending:[]}]}});
  await waitFor(()=>expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('litigation_export，kind=documents')));
  expect(props.onSaved).toHaveBeenCalledOnce();
});

test('edits survive page hiding and newer records; reload is explicit',async()=>{
  const view=render(<LitigationDocuments {...props}/>);
  fireEvent.change(await screen.findByRole('textbox',{name:'原告姓名'}),{target:{value:'人工更正甲'}});
  view.rerender(<LitigationDocuments {...props} visible={false} record={{...record,revision:2}}/>);
  view.rerender(<LitigationDocuments {...props} record={{...record,revision:2}}/>);
  expect(screen.getByRole('textbox',{name:'原告姓名'})).toHaveProperty('value','人工更正甲');
  expect(screen.getByRole('checkbox',{name:'已核对原告姓名'})).toHaveProperty('checked',false);
  fireEvent.click(screen.getByRole('button',{name:'重新载入要素'}));
  expect(screen.getByRole('textbox',{name:'原告姓名'})).toHaveProperty('value','虚构甲');
});

test('catalogue failure has a working retry and selection sends real template ID',async()=>{
  vi.mocked(litigationClient.documentTemplates).mockRejectedValueOnce(new Error('连接失败')).mockResolvedValueOnce({templates:[definition]});
  render(<LitigationDocuments {...props} record={{revision:0,data:null}}/>);
  fireEvent.click(await screen.findByRole('button',{name:'重试读取样式'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'准备文书要素'})).toHaveProperty('disabled',false));
  fireEvent.click(screen.getByRole('button',{name:'准备文书要素'}));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('存在歧义时列出2至3个可用候选'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('合同.txt'));
});


test('model-sourced values are not labelled as already human-reviewed',async()=>{
  const sourced={...record,data:{...record.data!,fields:[{...record.data!.fields[0],pending:[],sources:[{path:'合同.txt',page:null,quote:'虚构甲'}]}]}};
  render(<LitigationDocuments {...props} record={sourced}/>);
  expect(await screen.findByRole('checkbox',{name:'已核对原告姓名'})).toHaveProperty('checked',false);
  expect(screen.getByRole('button',{name:'确认要素并生成 Word'})).toHaveProperty('disabled',true);
});

test('lending mediation is chosen by the user and party scope survives confirmation',async()=>{
  const lending={...definition,id:'private-lending-person-2025',name:'民间借贷起诉状（2025要素式·自然人）',fields:[{key:'mediation_choice',label:'是否考虑先行调解',hint:'由当事人选择',required:true,kind:'choice',choices:['是','否','暂不确定，想要了解更多内容']}]};
  vi.mocked(litigationClient.documentTemplates).mockResolvedValue({templates:[definition,lending]});
  const current:LitigationRecord<LitigationDocument>={revision:1,data:{...record.data!,template_id:lending.id,plaintiff_type:'natural',defendant_type:'natural',third_party_count:0,fields:[{key:'mediation_choice',value:'',sources:[],pending:['请当事人选择']}]}};
  vi.mocked(litigationClient.confirmDocument).mockResolvedValue({...current,revision:2,data:{...current.data!,confirmed:true}});
  render(<LitigationDocuments {...props} record={current}/>);
  const choice=await screen.findByRole('combobox',{name:'是否考虑先行调解'});
  expect(choice).toHaveProperty('value','');
  expect(screen.getByRole('button',{name:'确认要素并生成 Word'})).toHaveProperty('disabled',true);
  fireEvent.change(choice,{target:{value:'暂不确定，想要了解更多内容'}});
  fireEvent.click(screen.getByRole('checkbox',{name:'已核对是否考虑先行调解'}));
  fireEvent.click(screen.getByRole('button',{name:'确认要素并生成 Word'}));
  await waitFor(()=>expect(litigationClient.confirmDocument).toHaveBeenCalledWith('w',expect.objectContaining({plaintiff_type:'natural',defendant_type:'natural',third_party_count:0,fields:[{key:'mediation_choice',value:'暂不确定，想要了解更多内容',sources:[],pending:[]}]}),1));
});

test('open drafting starts from the user request and instructs new-file delivery',async()=>{
  render(<LitigationDocuments {...props} record={{revision:0,data:null}}/>);
  const request=await screen.findByRole('textbox',{name:'起草要求'});
  expect(screen.getByRole('button',{name:'开始起草'})).toHaveProperty('disabled',true);
  fireEvent.change(request,{target:{value:'被告有两人的买卖合同起诉状'}});
  fireEvent.click(screen.getByRole('button',{name:'开始起草'}));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('被告有两人的买卖合同起诉状'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('生成 Word 文件'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('document_create'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('合同.txt'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('不改动任何既有文件'));
});

test('open drafting with no materials follows the user request',async()=>{
  render(<LitigationDocuments {...props} selectedMaterials={[]} record={{revision:0,data:null}}/>);
  fireEvent.change(await screen.findByRole('textbox',{name:'起草要求'}),{target:{value:'给对方的催款律师函'}});
  fireEvent.click(screen.getByRole('button',{name:'开始起草'}));
  expect(props.onStart).toHaveBeenCalledWith(expect.not.stringContaining('必须先选择材料'));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining('催款律师函'));
});

test('organization template selection prepares the chosen form and confirmation keeps both party types',async()=>{
  const organization={...definition,id:'private-lending-organization-person-2025',name:'民间借贷起诉状（2025要素式·组织起诉个人）',fields:[{key:'plaintiff_name',label:'原告名称',hint:'',required:true,kind:'text'}]};
  vi.mocked(litigationClient.documentTemplates).mockResolvedValue({templates:[definition,organization]});
  const view=render(<LitigationDocuments {...props} record={{revision:0,data:null}}/>);
  const selector=await screen.findByRole('combobox',{name:'文书样式'});
  await screen.findByRole('option',{name:organization.name});
  fireEvent.change(selector,{target:{value:organization.id}});
  fireEvent.click(screen.getByRole('button',{name:'准备文书要素'}));
  expect(props.onStart).toHaveBeenCalledWith(expect.stringContaining(`template_id=${organization.id}`));
  const current:LitigationRecord<LitigationDocument>={revision:1,data:{...record.data!,template_id:organization.id,plaintiff_type:'organization',defendant_type:'natural',third_party_count:0}};
  vi.mocked(litigationClient.confirmDocument).mockResolvedValue({...current,revision:2,data:{...current.data!,confirmed:true}});
  view.rerender(<LitigationDocuments {...props} record={current}/>);
  fireEvent.change(await screen.findByRole('textbox',{name:'原告名称'}),{target:{value:'虚构原告有限公司'}});
  fireEvent.click(screen.getByRole('checkbox',{name:'已核对原告名称'}));
  fireEvent.click(screen.getByRole('button',{name:'确认要素并生成 Word'}));
  await waitFor(()=>expect(litigationClient.confirmDocument).toHaveBeenCalledWith('w',expect.objectContaining({template_id:organization.id,plaintiff_type:'organization',defendant_type:'natural',third_party_count:0,fields:[{key:'plaintiff_name',value:'虚构原告有限公司',sources:[],pending:[]}]}),1));
});
