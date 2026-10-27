import { expect, test, vi } from "vitest";
import { caseCardId, caseSourcesFromItems, distinctCaseRows, focusCaseSource, renderCaseCitationText } from "../src/timeline/caseCitations";
import type { ToolItem } from "../src/timeline/items";
function tool(id: string, sourceId = "returned-id"): ToolItem {
  return { kind: "tool", id, name: "mcp__example", status: "succeeded", resultSummary: {
    kind: "case_results", count: 1, truncated: false, sourceName: "元典智库", fetchedAt: "2026-09-07T00:00:00Z",
    cases: [{ sourceId, title: "虚构示例案件", caseNumber: null, court: null, cause: null, judgedAt: null, gist: null, excerpt: null, level: null }],
  } };
}
test("same returned id has one numbered source and one card across tools", () => {
  const items = [tool("search"), tool("read"), tool("next", "other-id")];
  const sources = caseSourcesFromItems(items, 1);
  expect(sources.map((s) => s.number)).toEqual([2, 3]);
  expect(sources[0].cardId).toBe(caseCardId("search", "returned-id"));
  expect(distinctCaseRows(items).get("read")).toEqual([]);
});
test("failed or malformed tools cannot authorize model citations", () => {
  expect(caseSourcesFromItems([{ ...tool("failed"), status: "failed" }])).toEqual([]);
  expect(renderCaseCitationText("说明 [[case:not-returned]]", caseSourcesFromItems([tool("s")]))).toEqual([
    { kind: "text", text: "说明 " }, { kind: "text", text: "〔案例引用未验证〕" },
  ]);
});
test("resolved id binds only stored local source and streaming suppresses markers", () => {
  const sources = caseSourcesFromItems([tool("s")]);
  expect(renderCaseCitationText("[[case:returned-id]]", sources)).toEqual([{ kind: "case", source: sources[0] }]);
  expect(renderCaseCitationText("说明 [[case:return", sources, true)).toEqual([{ kind: "text", text: "说明 " }]);
});
test("click expands and focuses the source card with no network destination", () => {
  const source = caseSourcesFromItems([tool("s")])[0];
  const article = document.createElement("article"); article.id = source.cardId; article.tabIndex = -1;
  article.innerHTML = "<details><summary>片段</summary>内容</details>";
  article.scrollIntoView = vi.fn(); document.body.append(article);
  expect(focusCaseSource(source)).toBe(true); expect(article.querySelector("details")!.open).toBe(true);
  expect(document.activeElement).toBe(article); article.remove();
});

test("later details enrich first card without moving its citation anchor", () => {
  const first = tool("search"); const detail = tool("details");
  detail.resultSummary!.fetchedAt = "2026-09-07T01:00:00Z";
  (detail.resultSummary!.cases as Array<Record<string, unknown>>)[0].gist = "后读取的裁判要旨";
  const rows = distinctCaseRows([first, detail]);
  expect(rows.get("search")![0].gist).toBe("后读取的裁判要旨");
  expect(rows.get("search")![0].fetchedAt).toBe("2026-09-07T01:00:00Z");
  const sources = caseSourcesFromItems([first, detail]);
  expect(sources[0].cardId).toBe(caseCardId("search", "returned-id"));
  expect(sources[0].fetchedAt).toBe("2026-09-07T01:00:00Z");
});

test("真实来源超过显示阈值仍能解析，未知来源不能冒用", () => {
  const sources = caseSourcesFromItems(Array.from({ length: 41 }, (_, i) => tool(`call-${i}`, `source-${i}`)));
  expect(sources).toHaveLength(41);
  expect(renderCaseCitationText("[[case:source-40]]", sources)).toEqual([{ kind: "case", source: sources[40] }]);
  expect(renderCaseCitationText("[[case:invented]]", sources)).toEqual([{ kind: "text", text: "〔案例引用未验证〕" }]);
});
