import { afterEach, expect, test, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import TerminalPanel from '../src/components/TerminalPanel';
import * as api from '../src/runtime/api';
import * as client from '../src/runtime/terminalClient';

const xterm = vi.hoisted(() => ({input: (_: string) => {}, dispose: vi.fn()}));
vi.mock('@xterm/xterm', () => ({Terminal: class {
  options = {}; cols = 70; rows = 20; textarea = document.createElement('textarea');
  attachCustomKeyEventHandler() {}
  loadAddon() {} open() {} write(_: unknown, cb?: () => void) {cb?.();} clear() {} focus() {}
  onData(fn: (text: string) => void) { xterm.input = fn; return {dispose() {}}; }
  dispose() {xterm.dispose();}
}}));
vi.mock('@xterm/addon-fit', () => ({FitAddon: class {fit() {}}}));
vi.mock('../src/runtime/api', () => ({listWorkspaces: vi.fn()}));
vi.mock('../src/runtime/terminalClient', () => ({listTerminals: vi.fn(), readTerminal: vi.fn(), createTerminal: vi.fn(), closeTerminal: vi.fn(), writeTerminal: vi.fn(), resizeTerminal: vi.fn()}));
vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}});
const session: client.TerminalSession = {id: 'terminal-1', workspaceId: 'w', status: 'running', exitCode: null, columns: 80, rows: 24, inputSequence: 4};
function setup(sessions = [session]) {
  vi.mocked(api.listWorkspaces).mockResolvedValue([{id: 'w', display_name: '测试目录', root_path: '/tmp/test', allow_read:true, allow_write:true,created_at:'',last_used_at:''}]);
  vi.mocked(client.listTerminals).mockResolvedValue({supported:true,sessions});
  vi.mocked(client.readTerminal).mockImplementation(() => new Promise(() => {}));
  vi.mocked(client.readTerminal).mockResolvedValueOnce({...session, data:btoa('prompt'),cursor:6,truncated:false});
  vi.mocked(client.writeTerminal).mockImplementation(async (_w, _id, _text, sequence) => ({inputSequence:sequence}));
  vi.mocked(client.resizeTerminal).mockResolvedValue(session);
  return render(<TerminalPanel preferredWorkspaceId="w"/>);
}
afterEach(() => {cleanup();vi.clearAllMocks();});
test('reconnects existing session, serializes Unicode input, does not close on hiding', async () => {
  const view = setup();
  await view.findByText('运行中');
  act(() => xterm.input('你好\r'));
  act(() => xterm.input('pwd\r'));
  await waitFor(() => expect(client.writeTerminal).toHaveBeenCalledTimes(2));
  expect(client.writeTerminal).toHaveBeenNthCalledWith(1,'w','terminal-1','你好\r',5);
  expect(client.writeTerminal).toHaveBeenNthCalledWith(2,'w','terminal-1','pwd\r',6);
  view.unmount();
  expect(client.closeTerminal).not.toHaveBeenCalled();
  expect(client.createTerminal).not.toHaveBeenCalled();
  expect(xterm.dispose).toHaveBeenCalled();
});
test('ambiguous input failure stops later queued input and requires reconnect', async () => {
  const view = setup(); await view.findByText('运行中');
  vi.mocked(client.writeTerminal).mockRejectedValueOnce(new Error('输入未能确认'));
  act(() => {xterm.input('first');xterm.input('second');});
  await view.findByText('输入未能确认');
  expect(client.writeTerminal).toHaveBeenCalledTimes(1);
  expect(view.getByText('重新连接')).toBeTruthy();
});
test('creates only after user action and explicitly closes', async () => {
  const view = setup([]);
  vi.mocked(client.createTerminal).mockResolvedValue(session);
  vi.mocked(client.closeTerminal).mockResolvedValue({...session,status:'closed'});
  await view.findByText('在此文件夹中运行命令');
  expect(client.createTerminal).not.toHaveBeenCalled();
  vi.mocked(client.listTerminals).mockResolvedValue({supported:true,sessions:[session]});
  fireEvent.click(view.getByText('新建终端'));
  await waitFor(() => expect(client.createTerminal).toHaveBeenCalledTimes(1));
  fireEvent.click(await view.findByText('关闭会话'));
  await waitFor(() => expect(client.closeTerminal).toHaveBeenCalledWith('w','terminal-1'));
});
test('retains chosen folder when panel is hidden and reopened', async () => {
  const view = setup(); await view.findByText('运行中');
  const workspace = {display_name:'第二个目录',root_path:'/tmp/second',allow_read:true,allow_write:true,created_at:'',last_used_at:''};
  vi.mocked(api.listWorkspaces).mockResolvedValue([{...workspace,id:'w'},{...workspace,id:'second'}]);
  view.unmount();
  const second = render(<TerminalPanel preferredWorkspaceId={null}/>);
  await waitFor(() => expect(second.getByLabelText('终端文件夹').querySelectorAll('option')).toHaveLength(2));
  fireEvent.change(second.getByLabelText('终端文件夹'),{target:{value:'second'}});
  await waitFor(() => expect(client.listTerminals).toHaveBeenCalledWith('second',expect.anything()));
  second.unmount();
  const reopened = render(<TerminalPanel preferredWorkspaceId={null}/>);
  await waitFor(() => expect((reopened.getByLabelText('终端文件夹') as HTMLSelectElement).value).toBe('second'));
});
