import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import * as React from "react";
import { ContractReviewView } from "../src/views/ContractReviewView";
import { contractReviewApi } from "../src/runtime/contractReviewClient";
import { wordDocumentApi } from "../src/runtime/wordDocumentClient";
import { saveContractReview } from "../src/platform/contractReviewExport";

vi.mock("../src/runtime/contractReviewClient", async () => {
  const actual = await vi.importActual<typeof import("../src/runtime/contractReviewClient")>(
    "../src/runtime/contractReviewClient",
  );
  return {
    ...actual,
    contractReviewApi: {
      create: vi.fn(),
      remove: vi.fn().mockResolvedValue({deleted:true}),
      list: vi.fn().mockResolvedValue([]),
      get: vi.fn().mockResolvedValue(null),
      patchOpinion: vi.fn(),
      stop: vi.fn(),
      export: vi.fn(),
      report: vi.fn(),
      listSkills: vi.fn().mockResolvedValue([
        { name: "通用合同审查", description: "四层审核与违约责任专项。" },
      ]),
    },
  };
});
vi.mock("../src/platform/contractReviewExport", () => ({ saveContractReview: vi.fn() }));
vi.mock("../src/components/ContractAnnotationPreview", () => ({ ContractAnnotationPreview: ({onQuote, opinions}: {onQuote: (text: string) => void; opinions: unknown[]}) => <button data-opinion-count={opinions.length} onClick={() => onQuote("合同选区")}>选中合同文字</button> }));

vi.mock("../src/runtime/wordDocumentClient", () => ({wordDocumentApi:{remove:vi.fn().mockResolvedValue(undefined),list:vi.fn().mockResolvedValue([]),get:vi.fn(),open:vi.fn(),aiEdits:vi.fn().mockResolvedValue([]),aiConflict:vi.fn(),forReview:vi.fn().mockResolvedValue({id:'wd',filename:'合同.docx',content:'QQ==',revision:0})}}));
vi.mock("../src/components/wordEditor/WordDocumentEditor", () => ({WordDocumentEditor: React.forwardRef(({document,onReady,onSelection,headerActions}:any,ref:any)=>{ React.useEffect(()=>onReady?.(true),[]); React.useImperativeHandle(ref,()=>({history:editHistory,flush:async()=>({document,bytes:new Uint8Array([66])}),applyAiEdit,isDirty:()=>false})); return <div>{headerActions}<div>完整 Word 文档</div>{onSelection&&<button onClick={()=>onSelection({texts:['甲乙丙','丁戊己'],start:0,end:1,from:1,to:2})}>选中测试文字</button>}</div>; })}));
const wordApi=vi.mocked(wordDocumentApi);
const editHistory=vi.hoisted(()=>vi.fn());
const applyAiEdit=vi.hoisted(()=>vi.fn().mockResolvedValue(undefined));

const nativeDrop = vi.hoisted(() => ({ listen: vi.fn().mockResolvedValue(() => {}), read: vi.fn(), dispose: vi.fn() }));
vi.mock("@tauri-apps/api/webview", () => ({ getCurrentWebview: () => ({ onDragDropEvent: nativeDrop.listen }) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: nativeDrop.read }));

const api = vi.mocked(contractReviewApi);

function renderView(overrides: Partial<React.ComponentProps<typeof ContractReviewView>> = {}) {
  return render(
    <ContractReviewView
      thread={null}
      hasModel
      renderAssistant={() => <div>讨论区</div>}
      onStartReview={vi.fn()}
      onSelectReview={vi.fn()}
      {...overrides}
    />,
  );
}

describe("ContractReviewView", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.localStorage.setItem("verilex:contract-assistant-open:v1", "true");
    wordApi.open.mockResolvedValue({id:"wd",filename:"合同.docx",content:"QQ==",revision:0,updated_at:"t"});
    wordApi.list.mockResolvedValue([]);
    api.list.mockResolvedValue([]);
    api.get.mockResolvedValue(null as never);
    api.listSkills.mockResolvedValue([
      { name: "通用合同审查", description: "四层审核与违约责任专项。" },
    ]);
  });

  it("审查要求上传与启动失败后保留", async () => {
    const start = vi.fn().mockRejectedValue(new Error("审查没有启动"));
    renderView({onStartReview: start});
    const input = screen.getByLabelText("审查要求（选填）") as HTMLTextAreaElement;
    fireEvent.change(input, {target: {value: "关注付款\n保留原条款"}});
    fireEvent.change(screen.getByLabelText("选择合同文件"), {target: {files: [new File(["fixture"], "合同.docx")]}});
    await screen.findByText("完整 Word 文档");
    expect(input.value).toBe("关注付款\n保留原条款");
    expect(input.closest('[hidden]')).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "开始审查"}));
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({requirements: "关注付款\n保留原条款"})));
    await screen.findByText("审查没有启动");
    expect(input.value).toBe("关注付款\n保留原条款");

  });

  it("恢复历史要求，修改仅用于重新审查，不覆盖原记录", async () => {
    const row = {id: "requirements-review", thread_id: "requirements-thread", filename: "合同.docx", skill: "通用合同审查", status: "completed", created_at: "t", updated_at: "t", opinion_count: 0, party: "neutral", strictness: "normal", signature: "V", retrieval_enabled: false, requirements: "原审查要求", opinions: []};
    api.list.mockResolvedValue([row]); api.get.mockResolvedValue(row);
    const start = vi.fn();
    renderView({thread: {id: row.thread_id, status: "idle"} as never, onStartReview: start});
    await screen.findByText("完整 Word 文档");
    const input = screen.getByLabelText("审查要求（选填）") as HTMLTextAreaElement;
    await waitFor(() => expect(input.value).toBe("原审查要求"));
    fireEvent.change(input, {target: {value: "调整后的要求"}});
    expect(screen.getByText("修改后的要求将在重新审查时使用。")).toBeTruthy();
    expect(screen.getByText("原审查要求")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "按当前文档重新审查"}));
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({requirements: "调整后的要求"})));
    expect(row.requirements).toBe("原审查要求");
    expect(api.patchOpinion).not.toHaveBeenCalled();
  });

  it("直接拖入另一份合同也会清空上一份要求", async () => {
    renderView();
    fireEvent.drop(screen.getByLabelText("合同审查"), {dataTransfer: {files: [new File(["first"], "第一份.docx")]}});
    await screen.findByText("完整 Word 文档");
    const input = screen.getByLabelText("审查要求（选填）") as HTMLTextAreaElement;
    fireEvent.change(input, {target: {value: "第一份合同的要求"}});
    wordApi.open.mockResolvedValueOnce({id:"wd-next", filename:"第二份.docx", content:"QQ==", revision:0, updated_at:"t"});
    fireEvent.drop(screen.getByLabelText("合同审查"), {dataTransfer: {files: [new File(["second"], "第二份.docx")]}});
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("审查进行中锁定本次要求", async () => {
    renderView({thread: {id: "busy", status: "running"} as never});
    expect((screen.getByLabelText("审查要求（选填）") as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("未选合同时中央是唯一动作的拖放空态", async () => {
    renderView();
    expect(await screen.findByText("把合同拖到这里打开编辑")).toBeTruthy();
    expect(screen.getByText("支持 Word（.doc / .docx）文件")).toBeTruthy();
    expect(screen.queryByText("讨论区")).toBeNull();
  });

  it("文档标题不再放撤销重做，编辑器启用下方工具栏",async()=>{
    renderView();fireEvent.drop(screen.getByLabelText('合同审查'),{dataTransfer:{files:[new File(['fixture'],'合同.docx')]}});
    await screen.findByText('完整 Word 文档');
    expect(screen.queryByRole('button',{name:'撤销'})).toBeNull();
    expect(screen.getByRole('button',{name:'另存 Word'})).toBeTruthy();
  });

  it("技能菜单默认选中内置通用合同审查", async () => {
    renderView();
    const select = await screen.findByRole("combobox", { name: "审查技能" });
    expect(select.textContent).toBe("通用合同审查");
    fireEvent.click(select);
    expect(await screen.findByText("四层审核与违约责任专项。")).toBeTruthy();
  });

  it("设置上移与要求并列，要求展开收起保留输入和当前编辑器", async () => {
    const start = vi.fn();
    renderView({onStartReview: start});
    const settings = screen.getByRole("group", {name: "审查设置"});
    expect(settings.closest('.contract-review-heading')).toBeTruthy();
    const menu = settings.querySelector<HTMLDetailsElement>('.contract-requirements-menu')!;
    expect(menu.open).toBe(false);
    fireEvent.click(menu.querySelector('summary')!);
    const requirements = screen.getByLabelText('审查要求（选填）') as HTMLTextAreaElement;
    fireEvent.change(requirements, {target:{value:'重点核对付款'}});
    fireEvent.keyDown(requirements,{key:'Escape'});
    expect(menu.open).toBe(false);
    expect(requirements.value).toBe('重点核对付款');
    fireEvent.change(screen.getByRole("combobox", {name: "代表方"}), {target: {value: "custom"}});
    fireEvent.change(screen.getByLabelText("自定义代表方"), {target: {value: "丙方和担保人"}});
    fireEvent.drop(screen.getByLabelText("合同审查"), {dataTransfer: {files: [new File(["fixture"], "合同.docx")]}});
    const editor = await screen.findByText("完整 Word 文档");
    expect((screen.getByRole("combobox", {name: "代表方"}) as HTMLSelectElement).value).toBe("custom");
    fireEvent.click(menu.querySelector('summary')!);
    expect(screen.getByText("完整 Word 文档")).toBe(editor);
    fireEvent.click(screen.getByRole("button", {name: "开始审查"}));
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(start.mock.calls[0][1]).toMatchObject({party: "neutral", customParty: "丙方和担保人", documentId: "wd", requirements:'重点核对付款'});
  });

  it("历史记录点开回到那次审查", async () => {
    api.list.mockResolvedValue([
      {
        id: "rev-1", thread_id: "th-1", filename: "技术服务合同.docx", skill: "通用合同审查",
        status: "completed", created_at: "2026-09-15T08:00:00Z", updated_at: "2026-09-15T08:05:00Z",
        opinion_count: 3,
      },
    ]);
    const onSelect = vi.fn();
    renderView({ onSelectReview: onSelect });
    const item = await screen.findByText("技术服务合同.docx");
    item.click();
    expect(onSelect).toHaveBeenCalledWith("th-1");
    expect(screen.getByText("已完成 · 3 条意见")).toBeTruthy();
  });

  it("开启检索首次出现接收方披露", async () => {
    renderView();
    const select = await screen.findByLabelText("审查技能");
    expect(select).toBeTruthy();
    screen.getByLabelText("法条检索").click();
    expect(
      screen.getByText(/只发概念词，不发送合同正文、当事人名称或金额/),
    ).toBeTruthy();
    cleanup();
    window.localStorage.setItem("verilex:contract-retrieval-notice:v1", "1");
    renderView();
    await screen.findByLabelText("审查技能");
    screen.getByLabelText("法条检索").click();
    expect(screen.queryByText(/只发概念词/)).toBeNull();
  });

  it("上传等待期间阻止重复拖放并在失败后允许重试", async () => {
    let reject!: (cause: Error) => void;
    const start = vi.fn();
    wordApi.open.mockImplementationOnce(() => new Promise<any>((_, no) => { reject = no; }));
    renderView({onStartReview: start});
    const file = new File(["fixture"], "合同.docx");
    fireEvent.drop(screen.getByLabelText("合同审查"), {dataTransfer: {files: [file]}});
    fireEvent.drop(screen.getByLabelText("合同审查"), {dataTransfer: {files: [file]}});
    expect(wordApi.open).toHaveBeenCalledTimes(1);
    expect(start).not.toHaveBeenCalled();
    expect((screen.getByRole("button", {name: "正在打开合同…"}) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => reject(new Error("上传失败")));
    expect(await screen.findByText("上传失败")).toBeTruthy();
    expect((screen.getByRole("button", {name: "选择合同"}) as HTMLButtonElement).disabled).toBe(false);
  });

  it("导出取消时不显示提示，保存成功和错误仍可见", async () => {
    const row = {id: "r", thread_id: "t", filename: "合同.docx", skill: "通用合同审查", status: "completed", created_at: "t", updated_at: "t", opinion_count: 1, party: "neutral", strictness: "normal", signature: "V", retrieval_enabled: false, opinions: [{id: "o", adopted: true, clause_label: "条款", clause_excerpt: "原文", anchor_text: "原文", occurrence: null, level: "低", category: "建议协商", body: "意见", suggestion: "", edited_text: null, citations: []}]};
    api.list.mockResolvedValue([row]); api.get.mockResolvedValue(row);
    api.export.mockResolvedValue({filename: "合同（批注版）.docx", content: "dGVzdA=="});
    vi.mocked(saveContractReview).mockResolvedValueOnce(null).mockResolvedValueOnce("/tmp/合同（批注版）.docx").mockRejectedValueOnce("所选位置已有同名文件，请改用新文件名。原文件未修改。");
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    try {
      renderView({thread: {id: "t", status: "idle"} as never});
      await screen.findByRole("region", {name: "合同.docx 的审查文档"});
      fireEvent.click(screen.getByText("导出审查成果"));
      fireEvent.click(screen.getByText("导出批注版"));
      await waitFor(() => expect(saveContractReview).toHaveBeenCalledTimes(1));
      await waitFor(() => expect((screen.getByText("导出批注版") as HTMLButtonElement).disabled).toBe(false));
      expect(screen.queryByText("已取消保存。")).toBeNull();
      fireEvent.click(screen.getByText("导出审查成果"));
      fireEvent.click(screen.getByText("导出批注版"));
      expect(await screen.findByText("批注版已保存：/tmp/合同（批注版）.docx")).toBeTruthy();
      fireEvent.click(screen.getByText("导出审查成果"));
      fireEvent.click(screen.getByText("导出批注版"));
      expect(await screen.findByText("所选位置已有同名文件，请改用新文件名。原文件未修改。")).toBeTruthy();
    } finally { vi.unstubAllGlobals(); }
  });

  it("新建会话到达后重新加载历史，详情不再停留空态", async () => {
    const row = {id: "rev-new", thread_id: "th-new", filename: "新合同.docx", skill: "通用合同审查", status: "completed", created_at: "t", updated_at: "t", opinion_count: 0};
    const props = {hasModel: true, renderAssistant: () => <div>讨论区</div>, onStartReview: vi.fn(), onSelectReview: vi.fn()};
    const view = render(<ContractReviewView {...props} thread={null} />);
    await waitFor(() => expect(api.list).toHaveBeenCalled());
    api.list.mockResolvedValue([row]);
    api.get.mockResolvedValue({...row, party: "party_b", strictness: "strict", signature: "张律师", retrieval_enabled: false, opinions: []});
    view.rerender(<ContractReviewView {...props} thread={{id: "th-new", status: "idle"} as never} />);
    expect(await screen.findByRole("region", {name: "新合同.docx 的审查文档"})).toBeTruthy();
    expect((screen.getByLabelText("代表方") as HTMLSelectElement).value).toBe("party_b");
    expect((screen.getByLabelText("代表方") as HTMLSelectElement).disabled).toBe(false);

  });

  it("切换历史后旧请求迟到不会覆盖当前合同", async () => {
    const make = (id: string) => ({id, thread_id: id, filename: `${id}.docx`, skill: "通用合同审查", status: "completed", created_at: "t", updated_at: "t", opinion_count: 0, party: "neutral", strictness: "normal", signature: "V", retrieval_enabled: false, opinions: []});
    const a = make("a"), b = make("b");
    let finishA!: (row: typeof a) => void;
    api.list.mockResolvedValue([a, b]);
    api.get.mockImplementation(id => id === "a" ? new Promise(resolve => {finishA = resolve;}) : Promise.resolve(b));
    const props = {hasModel: true, renderAssistant: () => null, onStartReview: vi.fn(), onSelectReview: vi.fn()};
    const view = render(<ContractReviewView {...props} thread={{id: "a", status: "idle"} as never} />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("a"));
    view.rerender(<ContractReviewView {...props} thread={{id: "b", status: "idle"} as never} />);
    expect(await screen.findByRole("region", {name: "b.docx 的审查文档"})).toBeTruthy();
    await act(async () => finishA(a));
    expect(screen.queryByRole("region", {name: "a.docx 的审查文档"})).toBeNull();
  });

  it("不再显示独立意见栏，旧的展开偏好也不会挤占编辑区", async () => {
    localStorage.setItem("verilex:contract-assistant-open:v1", "true");
    const assistant = vi.fn(() => <div>讨论区</div>);
    const props = {hasModel: true, renderAssistant: assistant, onStartReview: vi.fn(), onSelectReview: vi.fn()};
    const row = {id: "r", thread_id: "t", filename: "合同.docx", skill: "通用合同审查", status: "running", created_at: "t", updated_at: "t", opinion_count: 0, party: "neutral", strictness: "normal", signature: "V", retrieval_enabled: false, opinions: []};
    api.list.mockResolvedValue([row]); api.get.mockResolvedValue(row);
    const view = render(<ContractReviewView {...props} thread={{id: "t", status: "running"} as never} />);
    await screen.findByText("完整 Word 文档");
    expect(screen.queryByRole("button", {name: "展开意见栏"})).toBeNull();
    expect(screen.queryByRole("tab", {name: "追问讨论"})).toBeNull();
    view.rerender(<ContractReviewView {...props} thread={{id: "t", status: "awaiting_approval"} as never} />);
    expect(screen.getByText("完整 Word 文档")).toBeTruthy();
    expect(assistant).toHaveBeenCalled();
  });

  it("合同选区和模型回答分别作为引用交给讨论区", async () => {
    const assistant = vi.fn((quote:string) => <div><span className="turn-narrative-text">模型给出的修改意见</span><span data-testid="quote">{quote}</span></div>);
    const row = {id:"r",thread_id:"t",filename:"合同.docx",skill:"通用合同审查",status:"completed",created_at:"t",updated_at:"t",opinion_count:0,party:"neutral",strictness:"normal",signature:"V",retrieval_enabled:false,opinions:[]};
    api.list.mockResolvedValue([row]);api.get.mockResolvedValue(row);
    renderView({thread:{id:'t',status:'idle'} as never,renderAssistant:assistant});
    fireEvent.click(await screen.findByRole('button',{name:'选中测试文字'}));
    expect(screen.getByTestId('quote').textContent).toBe('合同原文：\n乙丙\n丁戊');
    const answer=screen.getByText('模型给出的修改意见');const range=document.createRange();range.setStart(answer.firstChild!,0);range.setEnd(answer.firstChild!,4);
    const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);fireEvent.mouseUp(answer);
    fireEvent.click(screen.getByRole('button',{name:'引用选中的回答'}));
    expect(screen.getByTestId('quote').textContent).toBe('模型回答：\n模型给出');
    selection.removeAllRanges();
  });

  it('未开始整份审查时引用选区，只创建合同协作会话',async()=>{
    const start=vi.fn().mockResolvedValue(undefined);
    renderView({onStartReview:start});
    fireEvent.change(screen.getByLabelText('选择合同文件'),{target:{files:[new File(['fixture'],'合同.docx')]}});
    fireEvent.click(await screen.findByRole('button',{name:'选中测试文字'}));
    await waitFor(()=>expect(start).toHaveBeenCalledWith(expect.any(File),expect.objectContaining({discussionOnly:true,documentId:'wd',documentRevision:0})));
  });

  it('AI 提交修改后交给当前 Word 写入',async()=>{
    const row={id:'r',thread_id:'t',filename:'合同.docx',skill:'通用合同审查',status:'completed',created_at:'t',updated_at:'t',opinion_count:0,party:'neutral',strictness:'normal',signature:'V',retrieval_enabled:false,opinions:[]};
    api.list.mockResolvedValue([row]);api.get.mockResolvedValue(row);
    const edit={id:'edit',review_id:'r',document_id:'wd',revision:0,selection:{texts:['原文'],start:0,end:0,from:0,to:2},before_text:'原文',after_text:'改后',reason:'',status:'pending'};
    wordApi.aiEdits.mockResolvedValueOnce([edit]).mockResolvedValue([]);
    renderView({thread:{id:'t',status:'idle'} as never});
    await waitFor(()=>expect(applyAiEdit).toHaveBeenCalledWith(edit));
    expect(await screen.findByText('AI 已将修改写入当前文档。')).toBeTruthy();
  });

});

it('移除独立意见栏后仍可导出已有审查意见书', async () => {
  const opinion = {id: 'o', adopted: true, clause_label: '第三条', clause_excerpt: '原文', anchor_text: '原文', occurrence: null, level: '中', category: '建议协商', body: '意见正文', suggestion: '', edited_text: null, citations: []};
  const row = {id: 'r', thread_id: 't', filename: '合同.docx', skill: '通用合同审查', status: 'completed', created_at: 't', updated_at: 't', opinion_count: 1, party: 'neutral', strictness: 'normal', signature: 'V', retrieval_enabled: false, opinions: [opinion]};
  api.list.mockResolvedValue([row]); api.get.mockResolvedValue(row);
  api.report.mockResolvedValue({filename: '合同（审查意见书）.docx', content: 'UEsDBA=='});
  vi.mocked(saveContractReview).mockResolvedValue('合同（审查意见书）.docx');
  try {
    localStorage.setItem('verilex:contract-assistant-open:v1', 'true');
    renderView({thread: {id: 't', status: 'idle'} as never});
    await screen.findByText('完整 Word 文档');
    fireEvent.click(screen.getByText('导出审查成果'));
    fireEvent.click(screen.getByRole('button',{name:'出具审查意见 Word'}));
    expect(await screen.findByText('已开始下载审查意见书。')).toBeTruthy();
    expect(api.report).toHaveBeenCalledWith('r');
    expect(saveContractReview).toHaveBeenCalledWith({filename:'合同（审查意见书）.docx',content:'UEsDBA=='});
  } finally { cleanup(); vi.clearAllMocks(); }
});

describe("合同原生拖放", () => {
  let drop: (event: { payload: { type: string; paths: string[] } }) => void;
  beforeEach(() => {
    Object.defineProperty(globalThis, "__TAURI_INTERNALS__", { value: {}, configurable: true });
    window.localStorage.clear();
    wordApi.open.mockResolvedValue({id:"wd",filename:"合同.docx",content:"QQ==",revision:0,updated_at:"t"});
    nativeDrop.listen.mockImplementation(callback => { drop = callback; return Promise.resolve(nativeDrop.dispose); });
    wordApi.list.mockResolvedValue([]);
    api.list.mockResolvedValue([]);
    api.listSkills.mockResolvedValue([]);
    nativeDrop.read.mockResolvedValue([...new Uint8Array(8), 65, 66]);
  });
  afterEach(() => { cleanup(); Reflect.deleteProperty(globalThis, "__TAURI_INTERNALS__"); vi.clearAllMocks(); });
  it.each(["合同.docx", "旧合同.doc"])("原生拖入 %s 使用当前条件并去除传输头", async name => {
    const start = vi.fn();
    const view = renderView({ onStartReview: start });
    fireEvent.change(screen.getByRole("combobox", { name: "代表方" }), { target: { value: "party_b" } });
    await act(async () => { drop({ payload: { type: "drop", paths: [`/tmp/${name}`] } }); });
    expect(nativeDrop.read).toHaveBeenCalledWith("read_dropped_file", { path: `/tmp/${name}` });
    expect(start).not.toHaveBeenCalled();
    expect(wordApi.open).toHaveBeenCalledTimes(1);
    expect(wordApi.open.mock.calls[0][0].name).toBe(name);
    expect(wordApi.open.mock.calls[0][0].size).toBe(2);
    fireEvent.click(screen.getByRole("button", {name:"开始审查"}));
    await waitFor(()=>expect(start).toHaveBeenCalledTimes(1));
    expect(start.mock.calls[0][1]).toMatchObject({party:"party_b",documentId:"wd",documentRevision:0});
    view.unmount();
    expect(nativeDrop.dispose).toHaveBeenCalledTimes(1);
  });
  it("多文件和非 Word 文件不读取，读取失败明确提示", async () => {
    const start = vi.fn(); renderView({ onStartReview: start });
    await act(async () => { drop({ payload: { type: "drop", paths: ["/tmp/a.docx", "/tmp/b.docx"] } }); });
    expect(screen.getByRole("alert").textContent).toContain("一次请审查一份合同");
    await act(async () => { drop({ payload: { type: "drop", paths: ["/tmp/a.pdf"] } }); });
    expect(nativeDrop.read).not.toHaveBeenCalled();
    nativeDrop.read.mockRejectedValueOnce("dropped_file_unreadable");
    await act(async () => { drop({ payload: { type: "drop", paths: ["/tmp/a.docx"] } }); });
    expect(screen.getByRole("alert").textContent).toContain("无法读取拖入的文件");
    expect(start).not.toHaveBeenCalled();
  });
  it("读取期间重复拖入或点击选择不会重复开始，离开页面后不开始", async () => {
    let finish!: (bytes: number[]) => void;
    nativeDrop.read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const start = vi.fn(); const view = renderView({ onStartReview: start });
    await act(async () => {
      drop({ payload: { type: "drop", paths: ["/tmp/a.docx"] } });
      drop({ payload: { type: "drop", paths: ["/tmp/a.docx"] } });
    });
    fireEvent.change(screen.getByLabelText("选择合同文件"), { target: { files: [new File(["a"], "a.docx")] } });
    expect(nativeDrop.read).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => { finish([...new Uint8Array(8), 65]); });
    expect(start).not.toHaveBeenCalled();
  });
});

it("删除文档先确认，取消保留，成功后移出列表；点击外部收回菜单", async () => {
  wordApi.list.mockResolvedValue([{id:"local-delete",filename:"这是需要换行的长合同名称.docx"}]);
  renderView();
  const remove = await screen.findByRole("button",{name:"删除文档：这是需要换行的长合同名称.docx"});
  const menu=screen.getByLabelText("审查历史",{selector:"summary"}).closest("details")!;
  menu.open=true;
  fireEvent.pointerDown(screen.getByRole("heading",{name:"合同审查"}));
  expect(menu.open).toBe(false);
  fireEvent.click(remove);
  expect(wordApi.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"取消"}));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(remove);
  fireEvent.click(screen.getByRole("button",{name:"确认删除"}));
  await waitFor(()=>expect(wordApi.remove).toHaveBeenCalledWith("local-delete"));
  await waitFor(()=>expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.queryByRole("button",{name:"删除文档：这是需要换行的长合同名称.docx"})).toBeNull();
  cleanup();
});
