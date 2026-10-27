import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { invoke } from '@tauri-apps/api/core';
import BrowserPanel from '../src/components/BrowserPanel';
import { browserUrl, openBrowserPanel, BROWSER_PANEL_EVENT } from '../src/platform/browserPanel';
vi.mock('@tauri-apps/api/core', () => ({isTauri: () => true, invoke: vi.fn(async () => null)}));
vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}});
beforeEach(() => { vi.mocked(invoke).mockReset().mockResolvedValue(null); });
afterEach(() => {cleanup();vi.clearAllMocks();});
test('human action validates schemes and exposes one callable event', () => {
  expect(browserUrl('example.com')).toBe('https://example.com/');
  for (const value of ['file:///etc/passwd','https://user:pass@example.com','data://test','javascript://test','']) expect(() => browserUrl(value)).toThrow();
  const listener=vi.fn(); window.addEventListener(BROWSER_PANEL_EVENT,listener);
  openBrowserPanel('https://example.com'); expect(listener).toHaveBeenCalledOnce();
  window.removeEventListener(BROWSER_PANEL_EVENT,listener);
});
test('does not browse automatically; sends only user-entered URL and hides on unmount', async () => {
  const view=render(<BrowserPanel/>);
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',expect.objectContaining({action:'status'})));
  expect(invoke).not.toHaveBeenCalledWith('browser_action',expect.objectContaining({action:'open'}));
  fireEvent.change(view.getByLabelText('网页地址'),{target:{value:'https://example.com'}});
  fireEvent.click(view.getByRole('button',{name:'打开',exact:true}));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',{action:'open',url:'https://example.com/'}));
  view.unmount();
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',{action:'hide'}));
});
test('shows native rejection without losing the address', async () => {
  vi.mocked(invoke).mockImplementation(async (_cmd,args) => {if ((args as {action:string}).action==='open') throw '不支持本机地址'; return null;});
  const view=render(<BrowserPanel/>);
  fireEvent.change(view.getByLabelText('网页地址'),{target:{value:'http://localhost'}});
  fireEvent.click(view.getByRole('button',{name:'打开',exact:true}));
  expect((await view.findByRole('alert')).textContent).toContain('不支持本机地址');
});

test('restores the same page without navigating again and handles a new citation', async () => {
  const request={url:'https://example.com/a',id:101};
  const view=render(<BrowserPanel request={request}/>);
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',{action:'open',url:request.url}));
  view.unmount();
  const again=render(<BrowserPanel request={request}/>);
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',{action:'hide'}));
  expect(vi.mocked(invoke).mock.calls.filter(([,args]) => (args as {action:string}).action === 'open')).toHaveLength(1);
  again.rerender(<BrowserPanel request={{url:'https://example.com/b',id:102}}/>);
  await waitFor(() => expect(invoke).toHaveBeenCalledWith('browser_action',{action:'open',url:'https://example.com/b'}));
});
