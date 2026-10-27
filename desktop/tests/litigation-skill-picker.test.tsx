import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentComposer } from "../src/components/AgentComposer";
import { AgentWorkspaceView } from "../src/views/AgentWorkspaceView";
import * as api from "../src/runtime/api";
import type { SkillSummary } from "../src/runtime/contracts";

Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
  configurable: true, writable: true, value() { this.setAttribute("open", ""); },
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const noop = () => undefined;
const base = { hasModel: true, value: "", isRunning: false, onChange: noop, onOpenSettings: noop, onSend: noop, onStop: noop };
const skill = (changes: Partial<SkillSummary> = {}): SkillSummary => ({
  name: "evidence-evaluation", description: "审查证据的真实性、合法性、关联性。", category: "litigation-work",
  enabled: true, valid: true, instructions: "Instructions", error: null, path: "/synthetic/skills/evidence/SKILL.md", ...changes,
});

test("the workbench entry belongs only to the dedicated litigation discussion, including a new unsent discussion", () => {
  const props = { hasModel: true, thread: null, workspaceName: "测试", draft: "",
    onDraftChange: noop, pendingAgentMessage: null, onPendingAgentMessageSent: noop,
    onSendWithoutThread: noop, onOpenSettings: noop, onThreadsRefresh: noop, onUserTurnAccepted: noop };
  const view = render(<AgentWorkspaceView {...props} variant="litigation" ordinaryDiscussion />);
  expect(screen.getByRole("button", { name: "诉讼技能" })).toBeTruthy();
  view.rerender(<AgentWorkspaceView {...props} variant="litigation" ordinaryDiscussion={false} />);
  expect(screen.queryByRole("button", { name: "诉讼技能" })).toBeNull();
  view.rerender(<AgentWorkspaceView {...props} variant="chat" ordinaryDiscussion />);
  expect(screen.queryByRole("button", { name: "诉讼技能" })).toBeNull();
});

test("the entry is opt-in and fetches only on open; all valid skills are available regardless of category or global enablement", async () => {
  const list = vi.spyOn(api, "listSkills").mockResolvedValue([
    skill(), skill({ name: "争议焦点", category: "诉讼工作", description: "整理案件争议焦点。" }),
    skill({ name: "disabled", enabled: false }), skill({ name: "invalid", valid: false }),
    skill({ name: "诉讼合同", category: "contract-review" }), skill({ name: "litigation-unknown", category: undefined }),
  ]);
  const view = render(<AgentComposer {...base} />);
  expect(screen.queryByRole("button", { name: "诉讼技能" })).toBeNull();
  view.rerender(<AgentComposer {...base} litigationSkillScope="case-a:new" />);
  expect(list).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "诉讼技能" }));
  const nav = await screen.findByRole("navigation", { name: "可用诉讼技能" });
  expect(within(nav).getAllByRole("button").map(button => button.textContent).sort()).toEqual(["evidence-evaluation", "争议焦点", "disabled", "诉讼合同", "litigation-unknown"].sort());
  fireEvent.change(screen.getByRole("textbox", { name: "搜索诉讼技能" }), { target: { value: "真实性" } });
  expect(within(nav).getAllByRole("button")).toHaveLength(4);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索诉讼技能" }), { target: { value: "诉讼合同" } });
  expect(within(nav).getByRole("button", { name: "诉讼合同" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "加入输入框" })).toBeTruthy();
});

test("choosing shows the matching description and appends without sending, clearing, or duplicating the draft", async () => {
  vi.spyOn(api, "listSkills").mockResolvedValue([skill(), skill({ name: "争议焦点", description: "整理案件争议焦点。" })]);
  const send = vi.fn();
  function Harness() {
    const [value, onChange] = useState("已有问题\n请保留原文");
    return <AgentComposer {...base} value={value} onChange={onChange} onSend={send} litigationSkillScope="case-a:new" />;
  }
  render(<Harness />);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    fireEvent.click(screen.getByRole("button", { name: "诉讼技能" }));
    fireEvent.click(await screen.findByRole("button", { name: "争议焦点" }));
    expect(within(screen.getByRole("region", { name: "技能介绍" })).getByText("整理案件争议焦点。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "加入输入框" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    const input = screen.getByRole("textbox", { name: "向智能体提问" }) as HTMLTextAreaElement;
    expect(input.value).toBe("已有问题\n请保留原文\n\n请先读取并使用「争议焦点」技能。");
    await waitFor(() => expect(document.activeElement).toBe(input));
  }
  expect(send).not.toHaveBeenCalled();
});

test("errors are explicit and retry reloads the catalog; an empty catalog is not an error", async () => {
  const list = vi.spyOn(api, "listSkills").mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
  render(<AgentComposer {...base} litigationSkillScope="case-a:new" />);
  fireEvent.click(screen.getByRole("button", { name: "诉讼技能" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "无法加载诉讼技能，请重试。重试");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText(/暂无有效技能/)).toBeTruthy();
  expect(list).toHaveBeenCalledTimes(2);
});

test("Escape restores the trigger and a new case or thread closes the picker and aborts its request", async () => {
  const list = vi.spyOn(api, "listSkills").mockResolvedValue([skill()]);
  const view = render(<AgentComposer {...base} litigationSkillScope="case-a:new" />);
  const trigger = screen.getByRole("button", { name: "诉讼技能" });
  fireEvent.click(trigger);
  await screen.findByRole("navigation");
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: false, cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  await screen.findByRole("navigation");
  const signal = list.mock.calls.at(-1)![0]!;
  view.rerender(<AgentComposer {...base} litigationSkillScope="case-b:thread-b" />);
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
});
