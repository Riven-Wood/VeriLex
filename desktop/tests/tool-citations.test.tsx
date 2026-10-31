import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import {MarkdownBody} from '../src/components/timeline/MarkdownBody';
import {invoke, isTauri} from '@tauri-apps/api/core';
import {runtimeRequest} from '../src/runtime/api';
vi.mock('@tauri-apps/api/core',()=>({isTauri:vi.fn(()=>false),invoke:vi.fn()}));
vi.mock('../src/runtime/api',()=>({runtimeRequest:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});

test('正文只链接已成功读取的来源，点击查看真实工具返回',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({text:'虚构合同：付款期限三十日。',truncated:false});
  render(<MarkdownBody threadId="thread-1" text="付款期限[[source:receipt-1]]；未知[[source:fake]]。" toolSources={[{sourceId:'receipt-1',title:'文件读取',number:1}]}/>);
  fireEvent.click(screen.getByRole('button',{name:'工具来源 1：文件读取'}));
  expect(await screen.findByText('虚构合同：付款期限三十日。')).toBeTruthy();
  expect(vi.mocked(runtimeRequest)).toHaveBeenCalledWith('/v1/threads/thread-1/sources/receipt-1');
  expect(screen.getByText(/来源未验证/)).toBeTruthy();
});


test('无明确地址的插件来源不显示引注',()=>{
  render(<MarkdownBody threadId="thread-1" text="结论[[source:mcp-1]]。" toolSources={[{sourceId:'mcp-1',title:'插件来源',number:1,plugin:true,hidden:true}]}/>);
  expect(screen.queryByRole('link',{name:/来源/})).toBeNull();
  expect(screen.queryByText(/来源未验证/)).toBeNull();
});

test('桌面应用通过系统浏览器打开明确的插件来源',()=>{
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(invoke).mockResolvedValue(null);
  render(<MarkdownBody threadId="thread-1" text="结论[[source:mcp-1]]。" toolSources={[{sourceId:'mcp-1',title:'虚构法规',number:1,plugin:true,url:'https://example.org/law/1'}]}/>);
  fireEvent.click(screen.getByRole('link',{name:'来源 1：虚构法规'}));
  expect(invoke).toHaveBeenCalledWith('browser_action',{action:'external',url:'https://example.org/law/1'});
});

test('插件调用有唯一地址才显示可打开的引注',async()=>{
  vi.mocked(runtimeRequest).mockImplementation(async path => {
    if (path.endsWith('/one')) return {links:[{title:'虚构法规',url:'https://example.org/law/1'}]} as never;
    if (path.endsWith('/many')) return {links:[{title:'甲',url:'https://example.org/a'},{title:'乙',url:'https://example.org/b'}]} as never;
    return {links:[]} as never;
  });
  const {TurnGroup} = await import('../src/components/timeline/TurnGroup');
  const items = [
    ...['one','many','none'].map(id=>({kind:'tool' as const,id,turnId:'t1',name:'mcp__laws__read',status:'succeeded' as const,riskLevel:'low' as const})),
    {kind:'assistant' as const,id:'answer',turnId:'t1',segments:[{type:'text' as const,text:'甲[[source:one]]；乙[[source:many]]；丙[[source:none]]。',isStreaming:false}],isStreaming:false},
  ];
  render(<TurnGroup threadId="thread-1" items={items} active={false} renderItem={()=>null}/>);
  expect(await screen.findByRole('link',{name:/虚构法规/})).toBeTruthy();
  expect(screen.queryAllByRole('link')).toHaveLength(1);
  expect(screen.queryByText(/来源未验证/)).toBeNull();
});

test('法规读取的引注打开对应法规原文地址',async()=>{
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(invoke).mockResolvedValue(null);
  vi.mocked(runtimeRequest).mockResolvedValue({links:[{title:'虚构法规',url:'https://flk.npc.gov.cn/detail?id=12345678901234567890123456789012'}]});
  const {TurnGroup} = await import('../src/components/timeline/TurnGroup');
  const items = [
    {kind:'tool' as const,id:'law-1',turnId:'t1',name:'law_read',status:'succeeded' as const,riskLevel:'low' as const},
    {kind:'assistant' as const,id:'answer',turnId:'t1',segments:[{type:'text' as const,text:'第一条[[source:law-1]]',isStreaming:false}],isStreaming:false},
  ];
  render(<TurnGroup threadId="thread-1" items={items} active={false} renderItem={()=>null}/>);
  fireEvent.click(await screen.findByRole('link',{name:/虚构法规/}));
  expect(invoke).toHaveBeenCalledWith('browser_action',{action:'external',url:'https://flk.npc.gov.cn/detail?id=12345678901234567890123456789012'});
});

test('法律检索无法确定原文地址时明确提示',async()=>{
  vi.mocked(runtimeRequest).mockResolvedValue({links:[]});
  const {TurnGroup} = await import('../src/components/timeline/TurnGroup');
  const items = [
    {kind:'tool' as const,id:'many',turnId:'t1',name:'mcp__laws__read',status:'succeeded' as const,riskLevel:'low' as const},
    {kind:'assistant' as const,id:'answer',turnId:'t1',segments:[{type:'text' as const,text:'第一条[[source:many]]',isStreaming:false}],isStreaming:false},
  ];
  render(<TurnGroup presentation="legal" threadId="thread-1" items={items} active={false} renderItem={()=>null}/>);
  expect(await screen.findByText(/原文链接未取得/)).toBeTruthy();
  expect(screen.queryByRole('link')).toBeNull();
});
