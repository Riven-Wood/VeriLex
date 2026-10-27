import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { App } from '../src/app/App';
vi.mock('../src/runtime/startup', () => ({ waitForRuntime: vi.fn().mockRejectedValue(new Error('示例后台未连接')) }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: () => false }));
afterEach(() => { cleanup(); window.localStorage.clear(); });
test('local help and a safe template remain reachable when runtime startup fails', async () => {
  render(<App updaterClient={{supported:false,getCurrentVersion:async()=> '0.3.0',check:async()=>null,relaunch:async()=>{}}}/>);
  await screen.findAllByText('示例后台未连接');
  fireEvent.click(screen.getByRole('button',{name:'设置',exact:true}));
  fireEvent.click(screen.getByRole('button',{name:'帮助与反馈',exact:true}));
  expect(screen.getByRole('searchbox',{name:'搜索使用说明'})).toBeTruthy();
  expect((screen.getByRole('textbox',{name:'问题反馈模板'}) as HTMLTextAreaElement).value).toContain('未连接或状态不可用');
  expect((screen.getByRole('textbox',{name:'问题反馈模板'}) as HTMLTextAreaElement).value).not.toContain('示例后台未连接');
});
