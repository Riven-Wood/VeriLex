import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MessageActions } from "../src/components/timeline/MessageActions";
import { MarkdownBody } from "../src/components/timeline/MarkdownBody";
import { UserMessageEdit } from "../src/components/timeline/UserMessageEdit";
import { useAgentDraft, readDraft, moveDraft, flushDraft, clearDraft } from "../src/app/agentDrafts";
import { exportMarkdown, exportFilename } from "../src/timeline/exportMarkdown";
import { TimelineNotice } from "../src/components/timeline/TimelineNotice";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { Sidebar } from "../src/components/Sidebar";
import * as api from "../src/runtime/api";
afterEach(() => {cleanup(); vi.restoreAllMocks(); localStorage.clear();});

function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", {value: {writeText}, configurable: true});
  return writeText;
}
test("copies source only after clipboard success, with independent code copy", async () => {
  const write = clipboard();
  render(<><MessageActions text="**原文**" /><MarkdownBody text={'```js\nconst x = 1;\n```'} /></>);
  fireEvent.click(screen.getByRole("button", {name: "复制"}));
  await screen.findByRole("button", {name: "已复制"});
  expect(write).toHaveBeenCalledWith("**原文**");
  fireEvent.click(screen.getByRole("button", {name: "复制代码"}));
  await waitFor(() => expect(write).toHaveBeenCalledWith("const x = 1;\n"));
});
test("clipboard rejection is visible and never says copied", async () => {
  clipboard(vi.fn().mockRejectedValue(new Error("denied")));
  render(<MessageActions text="正文" />);
  fireEvent.click(screen.getByRole("button", {name: "复制"}));
  await screen.findByRole("button", {name: "复制失败"});
  expect(screen.queryByRole("button", {name: "已复制"})).toBeNull();
});
test("collapsed answers have copy and regenerate controls", () => {
  const resend = vi.fn();
  render(<TurnGroup active={false} items={[{kind: "assistant", id: "a", turnId: "t", isStreaming: false, segments: [{type: "text", text: "回答", isStreaming: false}]}]} renderItem={() => null} regenerateItemId="a" onRegenerate={resend} />);
  fireEvent.click(screen.getByRole("button", {name: "重新生成"}));
  expect(resend).toHaveBeenCalledOnce();
});
test("editing stops first, waits for terminal state, and retains the draft on rejection", () => {
  const send = vi.fn().mockReturnValue(false);
  const begin = vi.fn().mockReturnValue(true);
  const cancel = vi.fn();
  const {rerender} = render(<UserMessageEdit text="旧消息" running onBegin={begin} onCancel={cancel} onSend={send} />);
  fireEvent.click(screen.getByRole("button", {name: "编辑"}));
  expect(begin).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByRole("textbox"), {target: {value: "新消息"}});
  expect((screen.getByRole("button", {name: "重新发送"}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("正在停止当前工作…")).toBeTruthy();
  rerender(<UserMessageEdit text="旧消息" running={false} onBegin={begin} onCancel={cancel} onSend={send} />);
  fireEvent.click(screen.getByRole("button", {name: "重新发送"}));
  expect(send).toHaveBeenCalledWith("新消息");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("新消息");
  fireEvent.keyDown(screen.getByRole("textbox"), {key: "Escape"});
  expect(cancel).toHaveBeenCalledOnce();
  expect(send).toHaveBeenCalledTimes(1);
});
test("edit accepts attachment-only prompts and waits for durable acceptance", () => {
  const send = vi.fn().mockReturnValue(true);
  const props = {text: "", hasAttachments: true, onBegin: () => true, onCancel: vi.fn(), onSend: send};
  const {rerender} = render(<UserMessageEdit {...props} running={false} />);
  fireEvent.click(screen.getByRole("button", {name: "编辑"}));
  fireEvent.click(screen.getByRole("button", {name: "重新发送"}));
  expect(send).toHaveBeenCalledOnce();
  expect((screen.getByRole("button", {name: "取消"}) as HTMLButtonElement).disabled).toBe(true);
  rerender(<UserMessageEdit {...props} running />);
  rerender(<UserMessageEdit {...props} running={false} />);
  expect((screen.getByRole("button", {name: "重新发送"}) as HTMLButtonElement).disabled).toBe(false);
});
test("drafts survive switching before debounce and remount, then clear", () => {
  const hook = renderHook(({id}) => useAgentDraft(id), {initialProps: {id: "draft-a"}});
  act(() => hook.result.current[1]("A草稿"));
  expect(localStorage.getItem("verilex:agent-draft:v1:draft-a")).toBe("A草稿");
  hook.rerender({id: "draft-b"});
  expect(hook.result.current[0]).toBe("");
  act(() => hook.result.current[1]("B草稿"));
  hook.rerender({id: "draft-a"});
  expect(hook.result.current[0]).toBe("A草稿");
  hook.unmount();
  expect(readDraft("draft-b")).toBe("B草稿");
  const reopened = renderHook(() => useAgentDraft("draft-a"));
  expect(reopened.result.current[0]).toBe("A草稿");
  act(() => reopened.result.current[1](""));
  reopened.unmount();
  expect(readDraft("draft-a")).toBe("");
  clearDraft("draft-b");
});
test("new-task draft migrates once without carrying another task", () => {
  const hook = renderHook(() => useAgentDraft("new-test"));
  act(() => hook.result.current[1]("待发送"));
  act(() => moveDraft("new-test", "materialized"));
  expect(readDraft("materialized")).toBe("待发送");
  expect(readDraft("new-test")).toBe("");
  hook.unmount(); clearDraft("materialized");
});
test("export preserves full text and reports failure while omitting tool arguments", () => {
  const result = exportMarkdown("标题", "2026-09-15", [
    {kind: "user", id: "u", text: "问题"},
    {kind: "assistant", id: "a", isStreaming: false, segments: [{type: "text", text: "**回答**", isStreaming: false}]},
    {kind: "tool", id: "tool", name: "read_file", status: "failed", argumentsSummary: {secret: "不应导出"}},
    {kind: "notice", id: "n", notice: "turn_failed"},
    {kind: "notice", id: "c", notice: "turn_cancelled"},
  ]);
  expect(result).toContain("本轮已中断或取消"); expect(result).toContain("**回答**"); expect(result).toContain("本轮失败");
  expect(result).toContain("read_file — 失败"); expect(result).not.toContain("不应导出");
  expect(exportFilename("a/b:c")).toMatch(/^VeriLex-a_b_c-/);
});
test("retry and degraded compaction notices describe actual states", () => {
  render(<><TimelineNotice item={{kind: "notice", id: "1", notice: "turn_retry", attempt: 2}} /><TimelineNotice item={{kind: "notice", id: "2", notice: "turn_compacted", degraded: true}} /></>);
  expect(screen.getByText(/第 2\/2 次/)).toBeTruthy();
  expect(screen.getByText(/摘要失败/)).toBeTruthy();
});
const updates = {state: {phase: "up-to-date", currentVersion: "0.3.0", supported: true}} as never;
test("sidebar ignores late search responses, highlights body and clears results", async () => {
  let resolveOld!: (value: api.ThreadSearchResult[]) => void;
  const query = vi.spyOn(api, "searchThreads").mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve;})).mockResolvedValue([{thread: {id: "b", title: "新结果"} as never, snippet: {text: "涉及违约金", start: 2, end: 5}}]);
  const select = vi.fn();
  render(<Sidebar activeThreadId={null} updates={updates} onNewThread={() => {}} onSelectThread={select} />);
  fireEvent.click(screen.getByRole("button", {name: "搜索会话"}));
  fireEvent.change(screen.getByRole("searchbox"), {target: {value: "合同"}});
  await waitFor(() => expect(query).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByRole("searchbox"), {target: {value: "违约金"}});
  await screen.findByText("新结果");
  act(() => resolveOld([{thread: {id: "a", title: "过时结果"} as never, snippet: {text: "", start: 0, end: 0}}]));
  expect(screen.queryByText("过时结果")).toBeNull();
  expect(screen.getByText("违约金").tagName).toBe("MARK");
  fireEvent.click(screen.getByRole("button", {name: /新结果/}));
  expect(select).toHaveBeenCalledWith("b");
  fireEvent.change(screen.getByRole("searchbox"), {target: {value: ""}});
  expect(screen.queryByText("新结果")).toBeNull();
});
