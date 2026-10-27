import { TurnGroup } from '../src/components/timeline/TurnGroup';
import { AgentWorkspaceView } from '../src/views/AgentWorkspaceView';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { AgentComposer } from '../src/components/AgentComposer';
import { KnowledgePicker } from '../src/components/KnowledgePicker';
import { KnowledgeQuoteCard } from '../src/components/timeline/KnowledgeQuoteCard';
import { knowledgeApi } from '../src/runtime/knowledgeClient';
vi.mock('../src/runtime/knowledgeClient', () => ({knowledgeApi:{list:vi.fn(),quote:vi.fn()}}));
afterEach(() => {cleanup(); vi.restoreAllMocks();});
test('selection is explicit and discloses sending snippets to the current model', async () => {
  vi.mocked(knowledgeApi.list).mockResolvedValue([{id:'one',name:'资料库',status:'ready'}] as never);
  const change = vi.fn(); render(<KnowledgePicker selected={[]} onChange={change} disabled={false} />);
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('menuitem',{name:'知识库'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'资料库'}));
  expect(change).toHaveBeenCalledWith(['one']);
  expect(screen.getByText(/发送给当前模型服务/)).toBeTruthy();
});
test('quote card renders and copies the saved string including CRLF and punctuation', async () => {
  const text = '第一条：“原样”；\r\n第二行😀。';
  vi.mocked(knowledgeApi.quote).mockResolvedValue({text,source_name:'法条.md',line_start:1,line_end:2});
  const copy = vi.fn(async () => undefined);
  Object.defineProperty(navigator, 'clipboard', {configurable:true,value:{writeText:copy}});
  render(<KnowledgeQuoteCard quoteId="quote" threadId="thread" />);
  fireEvent.click(await screen.findByRole('button',{name:'复制原文'}));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(text));
  expect(document.querySelector('pre')?.textContent).toBe(text);
  expect(knowledgeApi.quote).toHaveBeenCalledWith('quote','thread');
});

test('composer selects knowledge only through plus menu and dismisses the panel', async () => {
  vi.mocked(knowledgeApi.list).mockResolvedValue([{id:'one',name:'资料库',status:'ready'}] as never);
  const change = vi.fn();
  render(<AgentComposer onApprovalModeChange={vi.fn()} hasModel value="" isRunning={false} onChange={vi.fn()} onOpenSettings={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} knowledgeSelection={{selected:[],onChange:change,disabled:false}} />);
  expect(screen.queryByRole('menuitem',{name:'知识库'})).toBeNull();
  expect(screen.queryByRole('group',{name:'本次对话知识库'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'更多选项'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'知识库'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'资料库'}));
  expect(change).toHaveBeenCalledWith(['one']);
  fireEvent.keyDown(document, {key:'Escape'});
  expect(screen.queryByRole('group',{name:'本次对话知识库'})).toBeNull();
});

test('a new unsent task clears the selected knowledge libraries', async () => {
  vi.mocked(knowledgeApi.list).mockResolvedValue([{id:'one',name:'资料库',status:'ready'}] as never);
  const props = {hasModel:true,thread:null,workspaceName:'测试',draft:'',onDraftChange:vi.fn(),pendingAgentMessage:null,onPendingAgentMessageSent:vi.fn(),onSendWithoutThread:vi.fn(),onOpenSettings:vi.fn(),onThreadsRefresh:vi.fn(),onUserTurnAccepted:vi.fn()};
  const view = render(<AgentWorkspaceView {...props} mcpTaskGeneration={1} />);
  fireEvent.click(screen.getByRole('button',{name:'更多选项'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'知识库'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'资料库'}));
  expect((screen.getByRole('checkbox',{name:'资料库'}) as HTMLInputElement).checked).toBe(true);
  view.rerender(<AgentWorkspaceView {...props} mcpTaskGeneration={2} />);
  await waitFor(()=>expect(screen.queryByText('知识库 · 1')).toBeNull());
  const checkbox = screen.queryByRole('checkbox',{name:'资料库'}) as HTMLInputElement | null;
  if (checkbox) expect(checkbox.checked).toBe(false);
});


test('program location button focuses the saved quote card', async () => {
  vi.mocked(knowledgeApi.quote).mockResolvedValue({text:'完整原文。',source_name:'book.md',line_start:4,line_end:4});
  const scroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {configurable:true,value:scroll});
  render(<TurnGroup active={false} items={[
    {kind:'tool',id:'tool',turnId:'turn',name:'knowledge_quote',status:'succeeded',resultSummary:{kind:'knowledge_quote',quoteId:'quote',threadId:'thread',sourceName:'book.md',lineStart:4,lineEnd:4}},
    {kind:'assistant',id:'answer',turnId:'turn',isStreaming:false,segments:[{type:'text',text:'查看原文卡。',isStreaming:false}]},
  ]} renderItem={item => item.kind==='tool' ? <KnowledgeQuoteCard key={item.id} quoteId="quote" threadId="thread" /> : null} />);
  await screen.findByRole('button',{name:'复制原文'});
  fireEvent.click(screen.getByRole('button',{name:'book.md · 第 4 行'}));
  expect(scroll).toHaveBeenCalledWith({block:'center',behavior:'smooth'});
  expect(document.activeElement?.id).toBe('knowledge-quote-thread-quote');
});


test.each(['chat', 'legal', 'litigation'] as const)('%s discussion sends only explicitly selected library ids', async variant => {
  vi.mocked(knowledgeApi.list).mockResolvedValue([{id:'one',name:'资料库',status:'ready'}] as never);
  const send = vi.fn();
  render(<AgentWorkspaceView variant={variant} ordinaryDiscussion hasModel thread={null} workspaceName="测试" draft="查看所选资料"
    onDraftChange={vi.fn()} pendingAgentMessage={null} onPendingAgentMessageSent={vi.fn()} onSendWithoutThread={send}
    onOpenSettings={vi.fn()} onThreadsRefresh={vi.fn()} onUserTurnAccepted={vi.fn()} />);
  if (variant === 'legal') fireEvent.click(screen.getByRole('button', {name:'继续追问'}));
  fireEvent.click(screen.getByRole('button',{name:'更多选项'}));
  fireEvent.click(screen.getByRole('menuitem',{name:'知识库'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'资料库'}));
  fireEvent.keyDown(document, {key:'Escape'});
  fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(() => expect(send).toHaveBeenCalledWith(expect.objectContaining({text:'查看所选资料',knowledgeLibraryIds:['one']})));
});
