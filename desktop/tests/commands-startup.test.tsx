import {afterEach,expect,test,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {App} from '../src/app/App';
vi.mock('../src/runtime/startup',()=>({waitForRuntime:vi.fn().mockRejectedValue(new Error('示例后台未连接'))}));
vi.mock('@tauri-apps/api/core',()=>({invoke:vi.fn(),isTauri:()=>false}));
afterEach(()=>{cleanup();window.localStorage.clear();});
test('shortcut controls remain reachable when the backend cannot start',async()=>{
 render(<App updaterClient={{supported:false,getCurrentVersion:async()=> '0.3.0',check:async()=>null,relaunch:async()=>{}}}/>);
 await screen.findAllByText('示例后台未连接');
 fireEvent.click(screen.getByRole('button',{name:'设置',exact:true}));
 expect(await screen.findByRole('heading',{name:'快捷键',exact:true})).toBeTruthy();
});
