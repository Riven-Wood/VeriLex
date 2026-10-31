import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { DeliverablesCard } from "../src/components/timeline/DeliverablesCard";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { groupTurns } from "../src/timeline/turnGroups";
import type { TimelineItem, ToolItem } from "../src/timeline/items";

afterEach(() => {
  cleanup();
});

const TURN_ARTIFACTS = [
  {
    kind: "artifact" as const,
    id: "artifact-1",
    turn_id: "t1",
    name: "审查报告.docx",
    published_workspace_id: "ws-a",
    published_relative_path: "审查报告.docx",
    operation: "document_create",
    size_bytes: 4096,
    structure_status: "passed" as const,
    render_status: "not_required" as const,
    vision_status: "not_run" as const,
    created_at: "2026-09-03T00:00:00Z",
  },
];

function tool(id: string, turnId: string, status: ToolItem["status"] = "succeeded"): ToolItem {
  return { kind: "tool", id, turnId, name: "read_file", status, riskLevel: "low" };
}

function assistant(id: string, turnId: string, text: string): TimelineItem {
  return {
    kind: "assistant",
    id,
    turnId,
    segments: [{ type: "text", text, isStreaming: false }],
    isStreaming: false,
  };
}

function renderGroup(
  items: TimelineItem[],
  active: boolean,
  props: { startedAt?: number; endedAt?: number } = {},
) {
  return render(
    <TurnGroup
      items={items}
      active={active}
      startedAt={props.startedAt}
      endedAt={props.endedAt}
      renderItem={(item) =>
        item.kind === "tool" ? (
          <div key={item.id}>TOOL:{item.name}</div>
        ) : item.kind === "notice" ? (
          <div key={item.id}>NOTICE</div>
        ) : null
      }
    />,
  );
}

test("groupTurns groups non-user items by turn and flags active turns", () => {
  const items: TimelineItem[] = [
    { kind: "user", id: "u1", turnId: "t1", text: "你好" },
    tool("c1", "t1"),
    assistant("a1", "t1", "第一版"),
    assistant("a2", "t1", "最终答案"),
    { kind: "user", id: "u2", turnId: "t2", text: "继续" },
    assistant("a3", "t2", "回复二"),
  ];

  const entries = groupTurns(items, null);

  expect(entries).toHaveLength(4);
  expect(entries[0].kind).toBe("user");
  expect(entries[1].kind).toBe("turn");
  if (entries[1].kind === "turn") {
    expect(entries[1].turnId).toBe("t1");
    expect(entries[1].items).toHaveLength(3);
    expect(entries[1].isActive).toBe(false);
  }
  expect(entries[3].kind).toBe("turn");
});

test("groupTurns marks the running turn and awaiting-approval turns active", () => {
  const items: TimelineItem[] = [
    { kind: "user", id: "u1", turnId: "t1", text: "你好" },
    tool("c1", "t1"),
    { kind: "user", id: "u2", turnId: "t2", text: "下一题" },
    tool("c2", "t2", "awaiting_approval"),
  ];

  const entries = groupTurns(items, "t1");

  expect(entries[1].kind === "turn" && entries[1].isActive).toBe(true);
  expect(entries[3].kind === "turn" && entries[3].isActive).toBe(true);
});

test("finished turn folds under a duration header showing only the final answer", () => {
  renderGroup(
    [
      tool("c1", "t1"),
      assistant("a1", "t1", "中间过程说明"),
      assistant("a2", "t1", "最终审查结论"),
    ],
    false,
    { startedAt: 1_000, endedAt: 61_000 },
  );

  // Codex-style header: the elapsed time, collapsed.
  const header = screen.getByRole("button", { name: /用时.*1 分/ });
  expect(header.getAttribute("aria-expanded")).toBe("false");
  // Final answer text renders plainly (no avatar bubble), intermediate hidden.
  expect(screen.getByText(/最终审查结论/)).toBeDefined();
  expect(screen.queryByText(/中间过程说明/)).toBeNull();
  expect(screen.queryByText("TOOL:read_file")).toBeNull();
});

test("expanding the duration header reveals the full work log", async () => {
  renderGroup(
    [
      tool("c1", "t1"),
      assistant("a1", "t1", "中间过程说明"),
      assistant("a2", "t1", "最终审查结论"),
    ],
    false,
    { startedAt: 1_000, endedAt: 31_000 },
  );

  await act(async () => {
    screen.getByRole("button", { name: /用时/ }).click();
  });

  // Narration flows as plain text; tool calls are single quiet step lines.
  expect(screen.getByText(/中间过程说明/)).toBeDefined();
  const steps = screen.getAllByText(/已读取/);
  expect(steps.length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText(/最终审查结论/)).toBeDefined();
});

test("reasoning segments merge into one disclosure inside the log", async () => {
  const items: TimelineItem[] = [
    {
      kind: "assistant",
      id: "a1",
      turnId: "t1",
      segments: [
        { type: "reasoning", text: "先想第一步", isStreaming: false },
        { type: "text", text: "过程叙述", isStreaming: false },
      ],
      isStreaming: false,
    },
    assistant("a2", "t1", "最终结论"),
  ];

  renderGroup(items, false, { startedAt: 0, endedAt: 5_000 });
  await act(async () => {
    screen.getByRole("button", { name: /用时/ }).click();
  });

  const reasoning = screen.getByRole("button", { name: /已深度思考/ });
  expect(reasoning).toBeDefined();
  // Only one merged reasoning row, not one per message.
  expect(screen.getAllByRole("button", { name: /已深度思考/ }).length).toBe(1);
  await act(async () => {
    reasoning.click();
  });
  expect(screen.getByText(/先想第一步/)).toBeDefined();
});

test("active turn streams the full log under a ticking duration header", () => {
  renderGroup(
    [
      tool("c1", "t1", "running"),
      assistant("a1", "t1", "中间过程说明"),
    ],
    true,
    { startedAt: Date.now() - 5_000 },
  );

  expect(screen.getByRole("button", { name: /用时/ })).toBeDefined();
  expect(screen.getByText(/中间过程说明/)).toBeDefined();
});

test("notices stay visible in a collapsed turn", () => {
  renderGroup(
    [
      assistant("a1", "t1", "最终答案"),
      { kind: "notice", id: "n1", turnId: "t1", notice: "turn_failed" },
    ],
    false,
  );

  expect(screen.getByText("NOTICE")).toBeDefined();
});

test("the turn's deliverables attach under its own bubble when collapsed", () => {
  render(
    <TurnGroup
      items={[tool("c1", "t1"), assistant("a1", "t1", "最终审查结论")]}
      active={false}
      artifacts={TURN_ARTIFACTS}
      renderDeliverables={(artifacts) => (
        <DeliverablesCard
          artifacts={artifacts}
          onOpen={() => undefined}
        />
      )}
      renderItem={(item) =>
        item.kind === "tool" ? <div key={item.id}>TOOL:{item.name}</div> : null
      }
    />,
  );

  // Collapsed: final answer and this turn's file card, nothing else.
  expect(screen.getByText(/最终审查结论/)).toBeDefined();
  expect(screen.getByRole("button", { name: /产出的文件/ })).toBeDefined();
  expect(screen.getByRole("button", { name: "审查报告.docx" })).toBeDefined();
  expect(screen.queryByText("TOOL:read_file")).toBeNull();
});

test("a turn without artifacts renders no deliverables card", () => {
  render(
    <TurnGroup
      items={[assistant("a1", "t1", "纯文字回答")]}
      active={false}
      artifacts={[]}
      renderDeliverables={(artifacts) => (
        <DeliverablesCard artifacts={artifacts} onOpen={() => undefined} />
      )}
      renderItem={() => null}
    />,
  );

  expect(screen.queryByRole("button", { name: /产出的文件/ })).toBeNull();
});

test('knowledge quotes remain visible when completed execution is collapsed', () => {
  const quote = {...tool('quote','turn'), name:'knowledge_quote'};
  render(<TurnGroup items={[quote,assistant('answer','turn','完成')]} active={false} renderItem={item => <div key={item.id}>原文卡 {item.id}</div>} />);
  expect(screen.getByText('原文卡 quote')).toBeTruthy();
});


test('knowledge analysis count uses successful saved cards rather than model claims', () => {
  const items: TimelineItem[] = [
    {...tool('read', 't1'), name:'knowledge_read'},
    {...tool('quote', 't1'), name:'knowledge_quote', resultSummary:{kind:'knowledge_quote',quoteId:'q1'}},
    {...tool('failed', 't1', 'failed'), name:'knowledge_quote', resultSummary:{kind:'knowledge_quote',quoteId:'q2'}},
    assistant('a', 't1', '两份资料均已引用。'),
  ];
  const view = renderGroup(items, false);
  expect(screen.getByText('模型分析 · 已生成 1 张原文卡，原文及位置见卡片')).toBeTruthy();
  expect(screen.getByText('两份资料均已引用。')).toBeTruthy();
  view.unmount();
  renderGroup([items[0], items[2], items[3]], true);
  expect(screen.getByText('模型分析 · 尚未生成原文卡')).toBeTruthy();
});


test('knowledge locations come from saved card metadata rather than prose line numbers', () => {
  renderGroup([
    {...tool('q', 't1'), name:'knowledge_quote', resultSummary:{kind:'knowledge_quote',quoteId:'saved',threadId:'t1',sourceName:'book.md',lineStart:4,lineEnd:4}},
    {...tool('failed', 't1', 'failed'), name:'knowledge_quote', resultSummary:{kind:'knowledge_quote',quoteId:'no',threadId:'t1',sourceName:'wrong.md',lineStart:3,lineEnd:3}},
    assistant('answer', 't1', '资料在第3行。'),
  ], false);
  expect(screen.getByRole('button', {name:'book.md · 第 4 行'})).toBeTruthy();
  expect(screen.queryByRole('button', {name:/wrong.md/})).toBeNull();
  expect(screen.getByText('资料在第3行。')).toBeTruthy();
});

for (const active of [true, false]) {
  test(`repeated tool failures fold into one expandable row, active=${active}`, async () => {
    const items = [tool('f1','t1','failed'), tool('f2','t1','failed'), tool('f3','t1','cancelled'), tool('approval','t1','awaiting_approval')];
    renderGroup(items, active);
    expect(screen.getAllByText('TOOL:read_file')).toHaveLength(1);
    const toggle = screen.getByRole('button', {name:/执行记录.*2 次失败.*1 次取消/});
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    await act(async () => toggle.click());
    expect(screen.getAllByText('TOOL:read_file')).toHaveLength(4);
    await act(async () => toggle.click());
    expect(screen.getAllByText('TOOL:read_file')).toHaveLength(1);
  });
}

test('new failures update the folded count without reopening details', async () => {
  const renderItem = (item: TimelineItem) => <span key={item.id}>DETAIL:{item.id}</span>;
  const first = [tool('f1', 't1', 'failed')];
  const view = render(<TurnGroup items={first} active renderItem={renderItem} />);
  const next = [...first, tool('f2', 't1', 'failed')];
  view.rerender(<TurnGroup items={next} active renderItem={renderItem} />);
  const toggle = screen.getByRole('button', {name:/执行记录.*2 次失败/});
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByText('DETAIL:f2')).toBeNull();
  await act(async () => toggle.click());
  expect(screen.getByText('DETAIL:f2')).toBeTruthy();
});

test('legal results and background task controls stay outside the failure disclosure', () => {
  const items = [tool('failure', 't1', 'failed'), {...tool('law', 't1'), name:'law_read'}, {...tool('background', 't1'), name:'run_background_command'}];
  render(<TurnGroup presentation="legal" items={items} active={false} renderItem={item => <span key={item.id}>DETAIL:{item.id}</span>} />);
  expect(screen.getByText('DETAIL:law')).toBeTruthy();
  expect(screen.getByText('DETAIL:background')).toBeTruthy();
  expect(screen.queryByText('DETAIL:failure')).toBeNull();
});

test('过程说明与最终回答分层显示，过程不出现消息操作', () => {
  const items = [assistant('step', 't1', '先检索现行条文'), {...tool('search', 't1'), name: 'law_search'}, assistant('answer', 't1', '最终回答')];
  render(<TurnGroup presentation="legal" items={items} active renderItem={() => null} />);
  const process = screen.getByText('先检索现行条文').closest('.turn-narrative');
  const answer = screen.getByText('最终回答').closest('.turn-narrative');
  expect(process?.classList.contains('turn-narrative-process')).toBe(true);
  expect(process?.querySelector('.message-actions')).toBeNull();
  expect(answer?.classList.contains('turn-narrative-process')).toBe(false);
  expect(answer?.querySelector('.message-actions')).not.toBeNull();
});


test('回答开始后自动折叠执行过程，仍可手动展开', () => {
  const running = {...tool('working', 't1'), status:'running' as const};
  const renderItem = (item: TimelineItem) => <span key={item.id}>DETAIL:{item.id}</span>;
  const view = render(<TurnGroup items={[running]} active startedAt={Date.now()} renderItem={renderItem} />);
  const toggle = screen.getByRole('button', {name:/执行过程/});
  if (toggle.getAttribute('aria-expanded') === 'false') fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  view.rerender(<TurnGroup items={[running, assistant('answer','t1','回答')]} active startedAt={Date.now()} renderItem={renderItem} />);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
});

test('回答出现时，展开的失败记录自动收起', () => {
  const failure = tool('failure', 't1', 'failed');
  const renderItem = (item: TimelineItem) => <span key={item.id}>DETAIL:{item.id}</span>;
  const view = render(<TurnGroup items={[failure]} active renderItem={renderItem} />);
  const toggle = screen.getByRole('button', {name:/执行记录/});
  fireEvent.click(toggle);
  expect(screen.getByText('DETAIL:failure')).toBeTruthy();
  view.rerender(<TurnGroup items={[failure, assistant('answer','t1','回答')]} active renderItem={renderItem} />);
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(screen.queryByText('DETAIL:failure')).toBeNull();
});
