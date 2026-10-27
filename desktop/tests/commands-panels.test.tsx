import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {PreviewRail} from '../src/components/PreviewRail';
import {PanelCommandContext} from '../src/commands/PanelCommandContext';
vi.mock('../src/components/TerminalPanel',()=>({default:()=> <div>终端面板测试</div>}));
vi.mock('../src/components/WorkspaceFilesPanel',()=>({WorkspaceFilesPanel:()=> <div>文件面板测试</div>}));
vi.mock('../src/runtime/api',async original=>({...await original<typeof import('../src/runtime/api')>(),listThreadArtifacts:vi.fn().mockResolvedValue([])}));
afterEach(cleanup);
it('routes repeated file and terminal requests to the existing rail tabs',async()=>{
 const rail=<PreviewRail terminalEnabled open target={{source:'artifacts-list'}} threadId={null} workspaceId={null} onClose={()=>{}} onOpenList={()=>{}} onOpenWorkspaceFile={()=>{}}/>;
 const view=render(<PanelCommandContext.Provider value={{panel:'terminal',sequence:1}}>{rail}</PanelCommandContext.Provider>);
 expect(await screen.findByText('终端面板测试')).toBeTruthy();
 view.rerender(<PanelCommandContext.Provider value={{panel:'files',sequence:2}}>{rail}</PanelCommandContext.Provider>);
 await waitFor(()=>expect(screen.queryByText('终端面板测试')).toBeNull());
 expect(screen.getByRole('button',{name:'文件',exact:true}).getAttribute('aria-pressed')).toBe('true');
 view.rerender(<PanelCommandContext.Provider value={{panel:'terminal',sequence:3}}>{rail}</PanelCommandContext.Provider>);
 expect(await screen.findByText('终端面板测试')).toBeTruthy();
});
