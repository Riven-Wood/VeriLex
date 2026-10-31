import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HelpCenter } from '../src/help/HelpCenter';
import { feedbackTemplate, FEEDBACK_URL, openFeedback } from '../src/help/feedback';
import { invoke, isTauri } from '@tauri-apps/api/core';
vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: vi.fn(() => false) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.mocked(isTauri).mockReturnValue(false); vi.mocked(invoke).mockReset(); });
const diagnostics = { appVersion: '0.3.0', runtimeVersion: '0.3.0', runtimeConnected: true };
test('searches beginner steps and recovers from an empty result', () => {
  render(<HelpCenter {...diagnostics}/>);
  fireEvent.change(screen.getByRole('searchbox'), {target: {value: 'Ollama'}});
  expect(screen.getByText('连接模型，开始第一次对话')).toBeTruthy();
  expect(screen.queryByText('对文件脱敏')).toBeNull();
  fireEvent.change(screen.getByRole('searchbox'), {target: {value: 'unmatched-here'}});
  expect(screen.getByText(/没有找到相关说明/)).toBeTruthy();
  fireEvent.click(screen.getByText('显示全部'));
  expect(screen.getByText('对文件脱敏')).toBeTruthy();
});
test('rejects raw runtime data and can omit all diagnostics', () => {
  const text = feedbackTemplate({appVersion:'/Users/example/private.docx',runtimeVersion:'https://model.invalid?token=secret',runtimeConnected:false},true);
  expect(text).not.toMatch(/\/Users|model.invalid|token=secret/);
  expect(text).toContain('未连接或状态不可用');
  expect(feedbackTemplate(diagnostics,false)).not.toContain('基础诊断');
});
test('only copies after clicking and permits removing diagnostics', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}});
  render(<HelpCenter {...diagnostics}/>);
  expect(writeText).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button',{name:'复制问题模板'}));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  expect(writeText.mock.calls[0][0]).not.toContain('基础诊断');
  expect(screen.getByRole('textbox',{name:'问题反馈模板'}).getAttribute('readonly')).not.toBeNull();
});
test('copy failure offers visible manual selection instead of claiming success', async () => {
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockRejectedValue(new Error('private raw error'))}});
  render(<HelpCenter {...diagnostics}/>);
  fireEvent.click(screen.getByRole('button',{name:'复制问题模板'}));
  expect(await screen.findByText(/未能复制/)).toBeTruthy();
  expect(screen.queryByText('private raw error')).toBeNull();
});
test('opens only the verified fixed URL with no feedback content', async () => {
  const open = vi.spyOn(window,'open').mockReturnValue(null);
  await openFeedback();
  expect(open).toHaveBeenCalledWith(FEEDBACK_URL,'_blank','noopener,noreferrer');
  vi.mocked(isTauri).mockReturnValue(true);
  await openFeedback();
  expect(invoke).toHaveBeenCalledWith('open_case_database',{database:'verilex-feedback'});
});
test('opening failure does not expose the raw error', async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(invoke).mockRejectedValue(new Error('/Users/private/token'));
  render(<HelpCenter {...diagnostics}/>);
  fireEvent.click(screen.getByRole('button',{name:'打开项目问题页'}));
  expect(await screen.findByText(/系统浏览器未能打开/)).toBeTruthy();
  expect(screen.queryByText('/Users/private/token')).toBeNull();
});
