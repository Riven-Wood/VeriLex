import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { LegalStatuteCard } from "../src/components/LegalStatuteCard";
import { lawResultSummary } from "../src/timeline/lawResults";
import type { LawReadSummary } from "../src/timeline/lawResults";
import type { ToolItem } from "../src/timeline/items";

function toolItem(summary: LawReadSummary, overrides: Record<string, unknown> = {}): ToolItem {
  return {
    kind: "tool",
    id: "call-1",
    turnId: "turn-1",
    name: "law_read",
    status: "succeeded",
    resultSummary: summary as unknown as Record<string, unknown>,
    ...overrides,
  } as unknown as ToolItem;
}

function readSummary(overrides: Record<string, unknown> = {}): LawReadSummary {
  return {
    kind: "law_read",
      bbbs: "b1",
      title: "中华人民共和国民法典",
      clause: "第一条",
      heading: "第一章 一般规定",
      clauseText: ["第一条 为了保护民事主体的合法权益，调整民事关系……"],
      before: ["第一编 总则"],
      after: ["第二条 民法主体从事民事活动……"],
      effectivity: "现行有效",
      gbrq: "2020-05-28",
      sxrq: "2021-01-01",
      fetchedAt: "2026-09-04T06:00:00Z",
      cached: true,
      truncated: false,
    source: "国家法律法规数据库",
    ...overrides,
  } as LawReadSummary;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("条文卡字段齐全渲染：法规名、条款号、原文、来源、效力、日期、读取时间", () => {
  render(<LegalStatuteCard summary={readSummary()} />);

  expect(screen.getByText("中华人民共和国民法典")).toBeTruthy();
  expect(screen.getByText("第一条")).toBeTruthy();
  expect(
    screen.getByText("第一条 为了保护民事主体的合法权益，调整民事关系……"),
  ).toBeTruthy();
  expect(screen.getByText("国家法律法规数据库")).toBeTruthy();
  expect(screen.getByText("现行有效")).toBeTruthy();
  expect(screen.getByText(/2020-05-28/)).toBeTruthy();
  expect(screen.getAllByText(/2026-09-04/).length).toBeGreaterThan(0);
});

test("缓存命中显示本机缓存角标与重新读取动作", () => {
  const onRefresh = vi.fn();
  render(<LegalStatuteCard summary={readSummary()} onRefresh={onRefresh} />);

  expect(screen.getByText(/本机缓存 · 读取于/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));

  expect(onRefresh).toHaveBeenCalledWith({
    bbbs: "b1",
    title: "中华人民共和国民法典",
    clause: "第一条",
  });
});

test("非缓存读取不显示本机缓存角标也不显示重新读取", () => {
  render(<LegalStatuteCard summary={readSummary({ cached: false })} />);

  expect(screen.queryByText(/本机缓存/)).toBeNull();
  expect(screen.queryByRole("button", { name: "重新读取" })).toBeNull();
});

test("未知效力如实显示未能确认效力", () => {
  render(<LegalStatuteCard summary={readSummary({ effectivity: "未能确认效力" })} />);

  expect(screen.getByText("未能确认效力")).toBeTruthy();
});

test("解析器拒绝畸形 resultSummary 并容忍未知工具", () => {
  expect(lawResultSummary(toolItem(readSummary()))?.kind).toBe("law_read");
  expect(
    lawResultSummary(toolItem(readSummary(), { resultSummary: { kind: "web_read" } })),
  ).toBeUndefined();
  expect(
    lawResultSummary(toolItem(readSummary(), { name: "read_file", resultSummary: undefined })),
  ).toBeUndefined();

  const searchItem = toolItem(readSummary(), {
    name: "law_search",
    resultSummary: {
      kind: "law_search",
      rows: [
        {
          bbbs: "b1",
          title: "中华人民共和国民法典",
          flxz: "法律",
          zdjg: "全国人民代表大会",
          effectivity: "现行有效",
          gbrq: "2020-05-28",
          sxrq: "2021-01-01",
        },
      ],
      total: 1,
      page: 1,
    },
  });
  const summary = lawResultSummary(searchItem);
  expect(summary?.kind).toBe("law_search");
  if (summary?.kind === "law_search") {
    expect(summary.rows[0].title).toBe("中华人民共和国民法典");
  }
});


test("长条文可展开，末段仍可查看", () => {
  const text = "第一条 " + "较长条文。".repeat(150) + "末段内容";
  render(<LegalStatuteCard summary={readSummary({ clauseText: [text] })} />);
  expect(screen.queryByText(/末段内容/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
  expect(screen.getByText(/末段内容/)).toBeTruthy();
  expect(screen.queryByText("正文较长，已截断")).toBeNull();
});

test.each([true, false])("法律回答原文在运行/完成后都保留，不按条文前缀删行 %s", (active) => {
  const summary = readSummary();
  const quote = summary.clauseText![0];
  const analysis = "效力状态：现行有效并不意味着可以直接适用于本案。";
  render(<TurnGroup active={active} presentation="legal" items={[
    toolItem(summary),
    { kind: "assistant", id: "answer", turnId: "turn-1", isStreaming: false,
      segments: [{ type: "text", text: analysis, isStreaming: false }] },
  ]} renderItem={(item) => <LegalStatuteCard key={item.id} summary={summary} />} />);
  expect(screen.getByText(analysis)).toBeTruthy();
  expect(screen.getByText("中华人民共和国民法典")).toBeTruthy();
});

test("截断预览从本机读取完整条文后展开", async () => {
  const api = await import("../src/runtime/api");
  const read = vi.spyOn(api, "getCachedLawText").mockResolvedValue({ paragraphs: ["完整条文末段"] });
  render(<LegalStatuteCard summary={readSummary({ truncated: true })} />);
  fireEvent.click(screen.getByRole("button", { name: "展开全文" }));
  await waitFor(() => expect(screen.getByText("完整条文末段")).toBeTruthy());
  expect(read).toHaveBeenCalledWith("b1", "第一条");
  read.mockRestore();
});


test("引用其他条文和章节的法条原文不会被切断", () => {
  const paragraphs = ["第一条 修改如下：", "第二条 改为以下规定。", "第三章 补充规定", "修正决定末段仍为同一原文。"];
  const summary = lawResultSummary(toolItem(readSummary({ clauseText: paragraphs })));
  expect(summary).toMatchObject({ clauseText: paragraphs });
  render(<LegalStatuteCard summary={readSummary({ clauseText: paragraphs })} />);
  for (const paragraph of paragraphs) expect(screen.getByText(paragraph)).toBeTruthy();
});
