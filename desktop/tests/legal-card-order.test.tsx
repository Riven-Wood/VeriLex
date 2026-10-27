import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import { LegalStatuteCard } from "../src/components/LegalStatuteCard";
import { lawResultSummary } from "../src/timeline/lawResults";
import { clauseOrdinal } from "../src/timeline/lawClause";
import { orderLegalCards } from "../src/timeline/legalOrder";
import type { ToolItem } from "../src/timeline/items";

function item(clause: string, bbbs = "same-law"): ToolItem {
  return {
    kind: "tool", id: `${bbbs}-${clause}`, turnId: "turn", name: "law_read", status: "succeeded",
    resultPreview: "tool output", durationMs: 3,
    resultSummary: {
      kind: "law_read", bbbs, title: "测试法规", clause, heading: "", clauseText: [`${clause} 正文。`, "第二款完整保留。"],
      before: [], after: [], source: "国家法律法规数据库", effectivity: "现行有效",
      gbrq: "2020-01-01", sxrq: "2020-02-01", fetchedAt: "2026-09-07T00:00:00Z", cached: false, truncated: false,
    },
  };
}

afterEach(cleanup);

for (const active of [true, false]) {
  test(`法律页运行/历史状态 ${active}：只显示卡片且按条序排列`, () => {
    const items = [item("第一千二百二十八条"), item("第一千二百一十九条"), item("第一千二百一十八条")];
    render(<TurnGroup presentation="legal" active={active} items={items} renderItem={(tool) => {
      if (tool.kind !== "tool") return null;
      const summary = lawResultSummary(tool);
      return <div key={tool.id} data-testid="law-result">
        <ToolCallCard item={tool} presentation="legal" />
        {summary?.kind === "law_read" && <LegalStatuteCard summary={summary} />}
      </div>;
    }} />);
    const cards = screen.getAllByTestId("law-result");
    expect(cards.map((card) => card.querySelector(".legal-statute-clause")?.textContent)).toEqual([
      "第一千二百一十八条", "第一千二百一十九条", "第一千二百二十八条",
    ]);
    expect(screen.queryByText("查看结果")).toBeNull();
    expect(screen.queryByText("成功")).toBeNull();
    expect(screen.queryByText("3 ms")).toBeNull();
  });
}

test("旧快照保留全部原文，不按章标题猜测边界", () => {
  const tool = item("第一千二百二十八条");
  tool.resultSummary!.clauseText = ["第一千二百二十八条 正文。", "第二款完整保留。", "第七章 环境污染和生态破坏责任"];
  const read = lawResultSummary(tool);
  expect(read?.kind).toBe("law_read");
  if (read?.kind !== "law_read") throw new Error("missing summary");
  render(<LegalStatuteCard summary={read} />);
  expect(screen.getByText("第二款完整保留。")).toBeTruthy();
  expect(screen.getByText(/第七章/)).toBeTruthy();
  expect((tool.resultSummary!.clauseText as string[])).toHaveLength(3);
});

test("不同法规版本分组，增补条文排在本条后面", () => {
  const items = [item("第十条"), item("第二条", "other"), item("第九条之一"), item("第九条")];
  expect(orderLegalCards(items).map((tool) => tool.id)).toEqual([
    "same-law-第九条", "same-law-第九条之一", "same-law-第十条", "other-第二条",
  ]);
  expect(items[0].id).toBe("same-law-第十条");
  expect(clauseOrdinal("第一千零三十二条")).toEqual([1032, 0]);
  expect(clauseOrdinal("第一万零三条")).toEqual([10003, 0]);
  expect(clauseOrdinal("第１２１９条")).toEqual([1219, 0]);
});

test("失败或摘要无效时保留错误与结果入口", () => {
  const failed = { ...item("第一条"), status: "failed" as const, resultSummary: undefined };
  render(<ToolCallCard item={failed} presentation="legal" />);
  expect(screen.getByText("查看结果")).toBeTruthy();
});
