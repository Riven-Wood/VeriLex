import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

const editorSource=readFileSync(resolve('src/documents/word-editor.js'),'utf8');
const reviewSource=editorSource.slice(editorSource.indexOf("const pane=document.getElementById('review-pane');"),editorSource.indexOf("document.getElementById('tracking').addEventListener"));
const viewportSource=editorSource.slice(editorSource.indexOf('let readingPositionEpoch='),editorSource.indexOf('function runEditingCommand('));
const namespace='http://schemas.openxmlformats.org/wordprocessingml/2006/main';

function harness(revisions:any[]=[],comments:any[]=[]){
  document.body.innerHTML='<button id="show-review"></button><div id="workarea"><div id="review-surface"><main id="paper"><p data-source-anchor-id="p:body:first" data-anchor="first">相同条款</p><p data-source-anchor-id="p:body:second" data-anchor="second">相同条款</p></main><aside id="review-pane"><span id="review-count"></span><button id="close-review"></button><div id="comment-form"></div><div id="review-scroll"><div id="review-list"></div></div></aside><svg id="review-connectors"></svg></div></div><p id="status"></p>';
  vi.stubGlobal('ResizeObserver',class{observe(){} disconnect(){}});
  vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));
  vi.stubGlobal('cancelAnimationFrame',vi.fn());
  const rect=(top:number,height=24)=>({top,bottom:top+height,left:12,right:210,width:198,height,x:12,y:top,toJSON(){}});
  const scrollport=document.getElementById('workarea')!;
  for(const [index,node] of [...document.querySelectorAll('#paper p')].entries())node.getBoundingClientRect=()=>rect(120+index*100-scrollport.scrollTop);
  const paper=document.getElementById('paper')!;
  paper.getBoundingClientRect=()=>rect(0,600);
  document.getElementById('review-scroll')!.getBoundingClientRect=()=>rect(35,565);
  scrollport.getBoundingClientRect=()=>rect(0,600);
  document.getElementById('review-surface')!.getBoundingClientRect=()=>rect(-scrollport.scrollTop,600);
  document.getElementById('review-list')!.getBoundingClientRect=()=>rect(35-scrollport.scrollTop,565);
  document.getElementById('review-pane')!.getBoundingClientRect=()=>({...rect(0,600),left:250,right:450});
  const xml:Record<string,string>={
    'cmt:cmt:known':`<w:comment xmlns:w="${namespace}" w:id="0"><w:p><w:r><w:t>批注</w:t></w:r></w:p></w:comment>`,
    'cmt:cmt:orphan':`<w:comment xmlns:w="${namespace}" w:id="9"/>`,
    'p:body:first':`<w:p xmlns:w="${namespace}"><w:r><w:t>相同条款</w:t></w:r></w:p>`,
    'p:body:second':`<w:p xmlns:w="${namespace}"><w:r><w:t>相</w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>同条</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:t>款</w:t><w:commentReference w:id="0"/></w:r></w:p>`,
  };
  const bridge={GetVersion:vi.fn(()=>'0'),RawGetXml:vi.fn((_:number,anchor:string)=>xml[anchor]),ListRevisions:vi.fn(()=>JSON.stringify(revisions)),AcceptRevision:vi.fn(),RejectRevision:vi.fn(),RemoveComment:vi.fn()};
  const editor={handle:7,exports:{DocxSessionBridge:bridge},listComments:()=>comments,anchorIdOf:(element:HTMLElement)=>element.getAttribute('data-source-anchor-id'),setCommentResolved:vi.fn(),reconcile:vi.fn()};
  const snapshot=vi.fn(()=>true),flush=vi.fn();
  const ui=new Function('editor','paper','status','snapshot','flushFocused','scheduleZoom','refreshZoom',`let composing=false,fatal=false;const scrollport=document.getElementById('workarea'),reviewSurface=document.getElementById('review-surface');${viewportSource}
${reviewSource};return {reviewAction,reviewSourceRect,refreshReview,layoutReview,selectReview,showReview,hideReview,reviewLineRects,get items(){return reviewItems;}};`)(editor,paper,document.getElementById('status'),snapshot,flush,vi.fn(),vi.fn());
  ui.refreshReview();
  for(const item of ui.items){Object.defineProperty(item.row,'offsetHeight',{value:60});item.row.getBoundingClientRect=()=>({...rect((parseFloat(item.row.style.top)||0)+35-scrollport.scrollTop,60),left:260,right:450});}
  ui.layoutReview();
  return {ui,editor,bridge,paper,scrollport,snapshot,flush};
}

afterEach(()=>{document.body.innerHTML='';vi.restoreAllMocks();vi.unstubAllGlobals();});

describe('Word 修订与批注旁注',()=>{
  it('处理批注后即使纸张重建也保持阅读位置',()=>{
    const {ui,editor,scrollport}=harness();scrollport.scrollTop=150;scrollport.scrollLeft=40;
    editor.reconcile.mockImplementation(()=>{scrollport.scrollTop=0;scrollport.scrollLeft=0;});
    expect(ui.reviewAction(()=>{})).toBe(true);
    expect(scrollport.scrollTop).toBe(150);expect(scrollport.scrollLeft).toBe(40);
  });
  it('上方内容改变后按仍可见的正文定位，保留相对阅读位置',()=>{
    const {ui,editor,scrollport,paper}=harness();scrollport.scrollTop=150;
    editor.reconcile.mockImplementation(()=>{
      scrollport.scrollTop=0;
      const node=paper.lastElementChild!;
      const old=node.getBoundingClientRect.bind(node);
      node.getBoundingClientRect=()=>({...old(),top:old().top-60,bottom:old().bottom-60});
    });
    ui.reviewAction(()=>{});expect(scrollport.scrollTop).toBe(90);
  });
  it('跨页段落按当前分页片段恢复，不跳到该段第一页',()=>{
    const {ui,editor,scrollport,paper}=harness();
    paper.lastElementChild!.setAttribute('data-source-anchor-id','p:body:first');
    scrollport.scrollTop=150;editor.reconcile.mockImplementation(()=>{scrollport.scrollTop=0;});
    ui.reviewAction(()=>{});expect(scrollport.scrollTop).toBe(150);
  });
  it('同段相同删除文字按各自原生修订编号定位，不误连整段',()=>{
    const {ui,paper,bridge}=harness(['10','11'].map(id=>({id,type:'delete',text:'重复',constituentIds:[id],anchorId:'p:body:second'})));
    paper.lastElementChild!.innerHTML='<del>重复</del>甲方<del>重复</del>乙方';
    bridge.GetVersion.mockReturnValue('1');
    bridge.RawGetXml.mockReturnValue(`<w:p xmlns:w="${namespace}"><w:del w:id="10"><w:r><w:delText>重复</w:delText></w:r></w:del><w:r><w:t>甲方</w:t></w:r><w:del w:id="11"><w:r><w:delText>重复</w:delText></w:r></w:del><w:r><w:t>乙方</w:t></w:r></w:p>`);
    ui.layoutReview();
    expect(ui.items.map((i:any)=>i.target)).toEqual([...paper.querySelectorAll('del')]);
    const reads=bridge.RawGetXml.mock.calls.length;ui.layoutReview();expect(bridge.RawGetXml.mock.calls.length).toBe(reads);
  });
  it('同段后面的批注不能因段落顶部更高而排到前面的删除记录之前',()=>{
    const {ui,paper,bridge}=harness([{id:'early-delete',type:'delete',text:'旧内容',anchorId:'p:body:second'}],[{anchorId:'cmt:cmt:known',text:'后面的批注'}]);
    paper.lastElementChild!.innerHTML='<del>旧内容</del>相同条款';
    const originalXml=bridge.RawGetXml.getMockImplementation()!;
    bridge.GetVersion.mockReturnValue('1');
    bridge.RawGetXml.mockImplementation((handle:number,anchor:string)=>anchor==='p:body:second'?`<w:p xmlns:w="${namespace}"><w:del><w:r><w:delText>旧内容</w:delText></w:r></w:del><w:r><w:t>相</w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>同条</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:t>款</w:t></w:r></w:p>`:originalXml(handle,anchor));
    const createRange=document.createRange.bind(document);
    vi.spyOn(document,'createRange').mockImplementation(()=>{
      const range=createRange();
      range.getClientRects=()=>{
        const top=range.toString()==='相'?240:320;
        return (range.toString()==='旧内容'?[]:[{top,bottom:top+24,left:12,right:210,width:198,height:24}]) as unknown as DOMRectList;
      };
      return range;
    });
    for(const item of ui.items)item.row.getBoundingClientRect=()=>({top:parseFloat(item.row.style.top)+35,bottom:parseFloat(item.row.style.top)+95,left:250,right:450,width:200,height:60});
    ui.layoutReview();
    const [deletion,comment]=ui.items;
    expect(parseFloat(deletion.row.style.top)).toBeLessThan(parseFloat(comment.row.style.top));
    expect([...document.getElementById('review-list')!.children]).toEqual([deletion.row,comment.row]);
    const paths=[...document.querySelectorAll('#review-connectors path')].map(path=>path.getAttribute('d'));
    expect(paths[0]).toContain(' 264 H ');expect(paths[1]).toContain(' 344 H ');
  });
  it('混排数字与中文按同一行合并等高底色，不依赖字体框顶部顺序',()=>{
    const {ui}=harness();
    const boxes=[{left:0,right:15,top:6,bottom:25,width:15,height:19},{left:15,right:90,top:0,bottom:30,width:75,height:30},{left:90,right:112,top:6,bottom:25,width:22,height:19},{left:112,right:190,top:0,bottom:30,width:78,height:30},{left:0,right:50,top:45,bottom:75,width:50,height:30}];
    expect(ui.reviewLineRects(boxes)).toEqual([{left:0,right:190,top:0,bottom:30,width:190,height:30},{left:0,right:50,top:45,bottom:75,width:50,height:30}]);
  });
  it('同作者使用相同颜色，不同作者的正文和旁注颜色分别对应',()=>{
    const {ui}=harness([],[{anchorId:'cmt:a',author:'张律师',text:'A'},{anchorId:'cmt:b',author:'李律师',text:'B'},{anchorId:'cmt:c',author:'张律师',text:'C'}]);
    expect(ui.items[0].color).toBe(ui.items[2].color);expect(ui.items[0].color).not.toBe(ui.items[1].color);
    for(const item of ui.items)expect(item.row.style.getPropertyValue('--review-color')).toBe(item.color);
  });

  it('新增修订仅在正文显示，真实批注和删除修订仍保留旁注',()=>{
    const {ui}=harness([{id:'insert',type:'insert',text:'新增'}, {id:'delete',type:'delete',text:'删除'}],[{anchorId:'cmt:cmt:known',text:'实际批注'}]);
    expect(ui.items.map((item:any)=>item.value.id||item.value.anchorId)).toEqual(['delete','cmt:cmt:known']);
    expect(document.getElementById('show-review')!.textContent).toBe('修订 2 · 批注 1');
    expect(document.getElementById('review-count')!.textContent).toBe('2 条');
  });
  it.each(['start','middle','end'])('WebKit空光标无几何信息时，%s删除精确连接相邻字边界',position=>{
    const {ui,paper}=harness();const block=paper.lastElementChild!;
    block.innerHTML=position==='start'?'<del>乙方</del>向甲方付款。':position==='end'?'向甲方付款。<del>乙方</del>':'验收后，<del>乙方</del>向甲方付款。';
    const target=block.querySelector('del')!;
    target.getBoundingClientRect=()=>({top:0,bottom:0,left:0,right:0,width:0,height:0} as DOMRect);
    const createRange=document.createRange.bind(document);
    vi.spyOn(document,'createRange').mockImplementation(()=>{
      const range=createRange();range.getClientRects=()=> (range.collapsed?[]:[{top:240,bottom:260,left:80,right:100,width:20,height:20}]) as any;return range;
    });
    const rect=ui.reviewSourceRect({target});
    expect(rect.top).toBe(240);expect(rect.bottom).toBe(260);
    expect(rect.left).toBe(position==='end'?100:80);expect(rect.right).toBe(rect.left);
  });
  it('隐藏删除文字的旁注仍定位原段落，不跳到页面原点',()=>{
    const {ui,paper}=harness([{id:'deleted',type:'delete',text:'旧内容',anchorId:'p:body:second',resolutionStatus:'supported'}]);
    const block=paper.lastElementChild!;block.innerHTML='<del class="rev-del">旧内容</del>';
    ui.layoutReview();
    expect(ui.items[0].target.tagName).toBe('DEL');
    expect(parseFloat(ui.items[0].row.style.top)).toBe(185);
    expect(ui.items[0].row.textContent).toContain('删除：旧内容');
    ui.selectReview(ui.items[0]);
    expect(document.getElementById('workarea')!.scrollTop).toBe(156);
    expect(block.querySelector('del')!.textContent).toBe('旧内容');
  });

  it('仅有新增内容时不生成卡片，保留正文标色提示',()=>{
    const {ui}=harness([{id:'insert',type:'insert',text:'新增'}]);
    expect(ui.items).toHaveLength(0);
    expect(document.querySelector('.review-empty')!.textContent).toContain('新增内容已在正文中标色');
  });

  it('跨段批注保留每一段的准确范围',()=>{
    const {ui,bridge}=harness([],[{anchorId:'cmt:cmt:known',text:'跨段批注'}]);
    bridge.GetVersion.mockReturnValue('1');
    bridge.RawGetXml.mockImplementation((_:number,anchor:string)=>anchor.startsWith('cmt:')?`<w:comment xmlns:w="${namespace}" w:id="0"/>`:anchor==='p:body:first'?`<w:p xmlns:w="${namespace}"><w:r><w:t>相</w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>同条款</w:t></w:r></w:p>`:`<w:p xmlns:w="${namespace}"><w:r><w:t>相同</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:t>条款</w:t></w:r></w:p>`);
    ui.layoutReview();expect(ui.items[0].ranges.map((range:Range)=>range.toString())).toEqual(['同条款','相同']);
  });
  it('精确高亮批注范围，不把整段当作选中文字，重复展开不重复查询修订',()=>{
    const {ui,bridge,paper}=harness([],[{anchorId:'cmt:cmt:known',author:'测试人',text:'核对'}]);
    expect(ui.items[0].ranges[0].toString()).toBe('同条');
    expect(document.querySelector('#review-connectors')!.getAttribute('viewBox')).toBe('0 0 198 600');
    ui.refreshReview();ui.refreshReview();expect(bridge.ListRevisions).toHaveBeenCalledOnce();
    paper.lastElementChild!.textContent='已改变的正文';ui.layoutReview();
    expect(ui.items[0].ranges).toEqual([]);
  });
  it('批注通过原生 id 对应真实段落，相同文字不会误定位；缺失位置保留说明',()=>{
    const {ui,paper,snapshot,flush,bridge}=harness([],[
      {anchorId:'cmt:cmt:known',author:'张律师',date:'2026-10-22T06:32:00Z',text:'核对履行期限'},
      {anchorId:'cmt:cmt:orphan',author:'王律师',text:'仍需核对'},
    ]);
    expect(ui.items[0].target?.getAttribute('data-source-anchor-id')).toBe('p:body:second');
    expect(ui.items[0].positionNote.hidden).toBe(true);
    expect(ui.items[1].target).toBeNull();
    expect(ui.items[1].positionNote.hidden).toBe(false);
    expect(document.querySelector('time')?.getAttribute('datetime')).toBe('2026-10-22T06:32:00Z');
    expect(ui.items[1].row.querySelector('time')).toBeNull();
    const before=paper.innerHTML;
    ui.selectReview(ui.items[0]);
    expect(paper.innerHTML).toBe(before);
    expect(snapshot).not.toHaveBeenCalled();
    expect(flush).not.toHaveBeenCalled();
    expect(bridge.AcceptRevision).not.toHaveBeenCalled();
  });

  it('同一段落的多条修订逐条避让，接受和拒绝始终使用各自原生 id',()=>{
    const revisions=Array.from({length:12},(_,index)=>({id:`revision-${index}`,type:'delete',author:'审查人',text:`文字 ${index}`,anchorId:'p:body:second',affectedAnchors:[],resolutionStatus:'supported'}));
    const {ui,bridge,snapshot}=harness(revisions);
    const positions=ui.items.map((item:any)=>parseFloat(item.row.style.top));
    expect(positions.every((top:number,index:number)=>index===0||top-positions[index-1]>=72)).toBe(true);
    expect(parseFloat(document.getElementById('review-list')!.style.height)).toBeGreaterThan(positions[11]+60);
    ui.items[7].row.querySelector('[aria-label="接受此处修订"]').click();
    expect(bridge.AcceptRevision.mock.calls).toEqual([[7,'revision-7']]);
    ui.items[9].row.querySelector('[aria-label="拒绝此处修订"]').click();
    expect(bridge.RejectRevision.mock.calls).toEqual([[7,'revision-9']]);
    expect(snapshot).toHaveBeenCalledTimes(2);
  });

  it('批注菜单保留解决、重新打开和删除操作，并可用键盘选择旁注',()=>{
    const {ui,editor,bridge}=harness([],[{anchorId:'cmt:cmt:known',author:'张律师',text:'核对付款',resolved:true}]);
    const row=ui.items[0].row;
    expect(row.querySelector('.review-avatar')?.textContent).toBe('张');
    row.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    expect(row.classList.contains('is-selected')).toBe(true);
    row.querySelector('[aria-label="重新打开"]').click();
    expect(editor.setCommentResolved.mock.calls).toEqual([['cmt:cmt:known',false]]);
    row.querySelector('[aria-label="删除批注"]').click();
    expect(bridge.RemoveComment.mock.calls).toEqual([[7,'cmt:cmt:known']]);
  });

  it('翻页只滚动公共容器，卡片与连线不靠滚动事件重新定位',()=>{
    const {ui,scrollport}=harness([],[{anchorId:'cmt:cmt:known',author:'张律师',text:'核对付款'}]);
    const row=ui.items[0].row,top=row.style.top,list=document.getElementById('review-list')!,svg=document.getElementById('review-connectors')!;
    const before=list.innerHTML,paths=svg.innerHTML;
    for(const offset of [300,900,40,0]){
      scrollport.scrollTop=offset;scrollport.dispatchEvent(new Event('scroll'));
      expect(list.innerHTML).toBe(before);expect(svg.innerHTML).toBe(paths);
      ui.layoutReview();expect(row.style.top).toBe(top);
    }
    ui.hideReview();scrollport.scrollTop=900;ui.showReview();ui.layoutReview();
    expect(row.style.top).toBe(top);expect(scrollport.scrollTop).toBe(900);
    expect(document.getElementById('review-scroll')!.scrollTop).toBe(0);
  });
});

it('触控板捏合调整显示比例，普通滚动保持原行为，双通道不会重复缩放',()=>{
  const paper=document.createElement('main'),editor={zoom:1},setZoom=vi.fn();
  const source=editorSource.slice(editorSource.indexOf('let gestureZoom=null;'),editorSource.indexOf('new ResizeObserver(scheduleZoom).observe(scrollport);'));
  new Function('scrollport','editor','setZoom',source)(paper,editor,setZoom);
  const wheel=new WheelEvent('wheel',{ctrlKey:true,deltaY:-20,cancelable:true});
  paper.dispatchEvent(wheel);expect(wheel.defaultPrevented).toBe(true);expect(setZoom.mock.calls[0][1]).toBeGreaterThan(1);
  setZoom.mockClear();paper.dispatchEvent(new WheelEvent('wheel',{deltaY:40}));expect(setZoom).not.toHaveBeenCalled();
  paper.dispatchEvent(new Event('gesturestart',{cancelable:true}));
  paper.dispatchEvent(new WheelEvent('wheel',{ctrlKey:true,deltaY:-20,cancelable:true}));expect(setZoom).not.toHaveBeenCalled();
  const change=new Event('gesturechange',{cancelable:true});Object.defineProperty(change,'scale',{value:1.5});paper.dispatchEvent(change);
  expect(setZoom).toHaveBeenCalledWith('manual',1.5);
  paper.dispatchEvent(new Event('gestureend',{cancelable:true}));
});
