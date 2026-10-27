import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {guidePrompt,LitigationGuide,LITIGATION_GUIDES,type GuideKind} from '../src/views/LitigationGuide';
afterEach(cleanup);
test.each(Object.keys(LITIGATION_GUIDES) as GuideKind[])('%s sends edited instructions and exact material scope; running prevents duplicate start',kind=>{
 const onStart=vi.fn();
 const props={kind,materials:['报价 "最终".txt'],disabled:false,onStart};
 const view=render(<LitigationGuide {...props}/>);
 fireEvent.click(screen.getByText('查看或调整工作指引'));
 fireEvent.change(screen.getByRole('textbox',{name:'工作指引'}),{target:{value:'只分析这一项争议'}});
 fireEvent.change(screen.getByRole('textbox',{name:kind==='documents'?'起草要求':'本次要求'}),{target:{value:'以被告立场提出建议'}});
 const name=kind==='documents'?'开始起草':`开始${LITIGATION_GUIDES[kind].name}`;
 fireEvent.click(screen.getByRole('button',{name}));
 expect(onStart).toHaveBeenCalledOnce();
 const prompt=onStart.mock.calls[0][0];
 expect(prompt).toContain('只分析这一项争议');expect(prompt).toContain(JSON.stringify(props.materials));
 expect(prompt).toContain('以被告立场提出建议');
 view.rerender(<LitigationGuide {...props} disabled/>);
 fireEvent.click(screen.getByRole('button',{name}));expect(onStart).toHaveBeenCalledOnce();
});

test('no selection adds no material scope rule',()=>{const prompt=guidePrompt('facts','整理事实','',[]);expect(prompt).not.toContain('本轮选中材料');expect(prompt).not.toContain('未选择材料');expect(prompt).not.toContain('不得自动扩大');});
