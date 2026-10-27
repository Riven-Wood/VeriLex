import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { MarkdownBody } from "../src/components/timeline/MarkdownBody";
import { ToolCallCard } from "../src/components/timeline/ToolCallCard";
import type { CitationSource } from "../src/runtime/contracts";
import {
  citationSourcesFrom,
  citationTokenNonceFrom,
} from "../src/timeline/citations";

const SOURCE: CitationSource = {
  number: 1,
  sourceId: "source-a",
  title: "示例来源",
  url: "https://example.com/article",
  site: "example.com",
  fetchedAt: "2026-08-27T10:00:00Z",
  truncated: false,
};

afterEach(cleanup);

test("completed text turns only a Runtime-verified marker into a citation link", () => {
  render(
    <MarkdownBody
      text={"事实[[cite:source-a]]，伪造[[cite:https://evil.test/a]]"}
      sources={[SOURCE]}
      streaming={false}
    />,
  );

  const citation = screen.getByRole("link", { name: "来源 1：示例来源" });
  expect(citation.getAttribute("href")).toBe("https://example.com/article");
  expect(citation.getAttribute("target")).toBe("_blank");
  expect(citation.getAttribute("rel")).toBe("noreferrer noopener");
  expect(screen.getByText(/引用未验证/)).toBeDefined();
  expect(document.body.textContent).not.toContain("source-a");
  expect(document.body.textContent).not.toContain("evil.test");
});

test("model-authored placeholder lookalikes cannot forge extra citation links", () => {
  const predictedTokens = Array.from({ length: 20 }, (_, renderIndex) =>
    Array.from(
      { length: 6 },
      (_, partIndex) => `VERILEX_CITATION_${renderIndex + 1}_${partIndex}_END`,
    ),
  ).flat();
  render(
    <MarkdownBody
      text={[
        ...predictedTokens,
        "VERILEX_CITATION_TOKEN_0_0_END",
        "合法事实[[cite:source-a]]",
      ].join(" ")}
      sources={[SOURCE]}
      streaming={false}
    />,
  );

  expect(screen.getAllByRole("link", { name: "来源 1：示例来源" })).toHaveLength(1);
  expect(document.body.textContent).toContain("VERILEX_CITATION_1_2_END");
  expect(document.body.textContent).toContain("VERILEX_CITATION_TOKEN_0_0_END");
});

test("a citation marker in a Markdown link label becomes only a verified citation link", () => {
  const { container } = render(
    <MarkdownBody
      text="[普通链接[[cite:source-a]]](https://ordinary.test/path)"
      sources={[SOURCE]}
      streaming={false}
    />,
  );

  const links = Array.from(container.querySelectorAll("a"));
  expect(links).toHaveLength(1);
  expect(links[0].getAttribute("aria-label")).toBe("来源 1：示例来源");
  expect(links[0].getAttribute("href")).toBe("https://example.com/article");
  expect(container.textContent).toContain("普通链接[1]");
  expect(container.querySelector('a[href="https://ordinary.test/path"]')).toBeNull();
});

test("citation markers inside Markdown link URLs and code never create marker-derived hrefs", () => {
  const { container } = render(
    <MarkdownBody
      text={[
        "[网址标记](https://ordinary.test/[[cite:source-a]])",
        "`行内[[cite:source-a]]`",
        "```text\n块[[cite:source-a]]\n```",
      ].join("\n\n")}
      sources={[SOURCE]}
      streaming={false}
    />,
  );

  expect(container.querySelectorAll("a.citation-link")).toHaveLength(0);
  const links = Array.from(container.querySelectorAll("a"));
  expect(links).toHaveLength(0);
  expect(links.every((link) => !link.getAttribute("href")?.includes("VERILEX")))
    .toBe(true);
  expect(container.querySelector("code")?.textContent).toContain("行内[1]");
  expect(container.querySelector("pre code")?.textContent).toContain("块[1]");
});

test("near-limit forged nonce text is scanned a bounded number of times", () => {
  const forged = Array.from(
    { length: 7_500 },
    (_, index) => `VERILEX_CITATION_TOKEN_${index}_0_END`,
  ).join("");
  const text = `${forged}${"正文".repeat(6_000)}`;
  const nativeIncludes = String.prototype.includes;
  let nonceScanCount = 0;
  const includesSpy = vi
    .spyOn(String.prototype, "includes")
    .mockImplementation(function (
      this: string,
      searchString: string,
      position?: number,
    ) {
      if (searchString.startsWith("VERILEX_CITATION_TOKEN_")) {
        nonceScanCount += 1;
      }
      return nativeIncludes.call(this, searchString, position);
    });

  try {
    expect(citationTokenNonceFrom(text)).toBe(7_500);
    expect(nonceScanCount).toBeLessThan(64);
  } finally {
    includesSpy.mockRestore();
  }
});

test.each([
  ["事实[[cite:source-a]]仍在生成", "事实仍在生成"],
  ["事实[[cite:source-a", "事实"],
  ["事实[[cite:source-a\n尚未闭合", "事实"],
])("streaming text hides complete or partial internal citation markers", (text, visible) => {
  render(<MarkdownBody text={text} sources={[]} streaming />);
  expect(document.body.textContent).toBe(visible);
  expect(document.body.textContent).not.toContain("source-a");
});

test.each(["[", "[[", "[[c", "[[ci", "[[cit", "[[cite", "[[cite:"])(
  "a trailing citation prefix %s stays hidden while streaming and fails closed when complete",
  (prefix) => {
    const { rerender } = render(
      <MarkdownBody text={`事实${prefix}`} sources={[]} streaming />,
    );
    expect(document.body.textContent).toBe("事实");

    rerender(<MarkdownBody text={`事实${prefix}`} sources={[]} streaming={false} />);
    expect(document.body.textContent).toBe("事实〔引用未验证〕");
  },
);

test("a malformed structured source cannot contribute an href", () => {
  render(
    <MarkdownBody
      text="事实[[cite:source-a]]"
      sources={[{ ...SOURCE, url: "javascript:alert(1)" }]}
      streaming={false}
    />,
  );
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText(/引用未验证/)).toBeDefined();
});

test.each([
  [{ ...SOURCE, number: 2 }, "non-sequential number"],
  [{ ...SOURCE, sourceId: " source-a " }, "untrimmed id"],
  [{ ...SOURCE, sourceId: "bad[id]" }, "bracketed id"],
  [{ ...SOURCE, sourceId: "bad\nid" }, "control character id"],
])("a source block with %s fails closed", (malformedSource) => {
  expect(citationSourcesFrom([malformedSource])).toBeNull();
});

test("source blocks retain all verified sources while enforcing id bounds", () => {
  const seventeenSources = Array.from({ length: 17 }, (_, index) => ({
    ...SOURCE,
    number: index + 1,
    sourceId: `source-${index + 1}`,
    url: `https://example.com/article/${index + 1}`,
  }));
  expect(citationSourcesFrom(seventeenSources)).toHaveLength(17);
  expect(citationSourcesFrom([{ ...SOURCE, sourceId: "s".repeat(129) }])).toBeNull();
});

test("source string limits count Unicode code points like Python", () => {
  const titleAtLimit = "😀".repeat(512);
  const idAtLimit = "😀".repeat(128);
  const urlPrefix = "https://example.com/法律?标题=";
  const urlAtLimit = `${urlPrefix}${"😀".repeat(
    2048 - Array.from(urlPrefix).length,
  )}`;

  expect(citationSourcesFrom([{
    ...SOURCE,
    sourceId: idAtLimit,
    title: titleAtLimit,
    url: urlAtLimit,
  }])).not.toBeNull();
  expect(citationSourcesFrom([{
    ...SOURCE,
    title: "😀".repeat(513),
  }])).toBeNull();
  expect(citationSourcesFrom([{
    ...SOURCE,
    sourceId: "😀".repeat(129),
  }])).toBeNull();
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: `${urlAtLimit}字`,
  }])).toBeNull();
});

test("a Runtime-canonical Unicode URL remains a verified visible citation", () => {
  const unicodeSource = {
    ...SOURCE,
    title: "中文合同来源😀",
    url: "https://example.com/法律?标题=合同",
  };
  render(
    <MarkdownBody
      text="依据[[cite:source-a]]"
      sources={[unicodeSource]}
      streaming={false}
    />,
  );

  const citation = screen.getByRole("link", {
    name: "来源 1：中文合同来源😀",
  });
  expect(decodeURI(citation.getAttribute("href") ?? "")).toBe(
    "https://example.com/法律?标题=合同",
  );
  expect(citationSourcesFrom([unicodeSource])).not.toBeNull();
});

test("a canonical non-default port and Unicode path remain displayable", () => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: "https://example.com:8443/法律?标题=合同",
  }])).not.toBeNull();
});

test.each([
  "HTTPS://example.com/article",
  "https://Example.com/article",
  "https://example.com:443/article",
  "https://example.com./article",
  "https://example.com/article#fragment",
  "https://example.com/a b",
  "https://example.com/a\\b",
])("a non-canonical persisted URL %s still fails closed", (url) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url,
  }])).toBeNull();
});

test.each([
  "https://example.com/../case",
  "https://example.com/./case",
  "https://example.com/%2e%2e/case",
  "https://example.com/.%2E/case",
  "https://example.com/%2e./case",
  "https://example.com/%2F..%2fcase",
  "https://example.com/name%2f.%2Fcase",
])("a single-decoded dot path segment in %s fails closed", (url) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url,
  }])).toBeNull();
});

test.each([
  "https://example.com/法律/合同",
  "https://example.com/%2Ename/case",
  "https://example.com/name../case",
  "https://example.com/%252e%252e/case",
])("a non-dot path in %s remains displayable", (url) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url,
  }])).not.toBeNull();
});

test("a canonical public IPv6 source matches Runtime's unbracketed site", () => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: "https://[2606:4700:4700::1111]/article",
    site: "2606:4700:4700::1111",
  }])).not.toBeNull();
});

test.each([
  "2000::1",
  "2001:20::1",
  "3fff:1000::1",
])("a Runtime-public IPv6 exception %s remains displayable", (host) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: `https://[${host}]/article`,
    site: host,
  }])).not.toBeNull();
});

test.each([
  ["2001:10::1", "IETF protocol assignment"],
  ["2001:2::1", "benchmarking"],
  ["2001:40::1", "unallocated protocol assignment"],
  ["64:ff9b::1", "reserved translation prefix"],
  ["100::1", "discard-only"],
  ["2001:db8::1", "documentation"],
  ["2002::1", "deprecated 6to4"],
  ["3fff::1", "documentation v6"],
  ["fc00::1", "unique-local"],
  ["fe80::1", "link-local"],
  ["ff02::1", "multicast"],
])("a Runtime-nonpublic IPv6 literal %s (%s) fails closed", (host) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: `https://[${host}]/article`,
    site: host,
  }])).toBeNull();
});

test.each([
  "192.0.0.9",
  "192.0.0.10",
  "192.88.99.1",
  "8.8.8.8",
])("a canonical public IPv4 literal %s follows Runtime's address table", (host) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: `https://${host}/article`,
    site: host,
  }])).not.toBeNull();
});

test.each([
  "192.0.0.8",
  "198.51.100.1",
  "203.0.113.1",
  "240.0.0.1",
])("a reserved IPv4 literal %s fails closed", (host) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: `https://${host}/article`,
    site: host,
  }])).toBeNull();
});

test.each([
  "http://2130706433/article",
  "http://127.1/article",
  "http://0x7f000001/article",
  "http://0177.0.0.1/article",
])("a legacy numeric IPv4 URL %s fails closed", (url) => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url,
    site: new URL(url).hostname,
  }])).toBeNull();
});

test("an IPv4-mapped public IPv6 literal follows Runtime's IPv4 classification", () => {
  expect(citationSourcesFrom([{
    ...SOURCE,
    url: "https://[::ffff:808:808]/article",
    site: "::ffff:808:808",
  }])).not.toBeNull();
});

test.each([
  [{ ...SOURCE, site: "other.test" }, "site mismatch"],
  [{ ...SOURCE, title: "标题\n注入" }, "title control character"],
  [{ ...SOURCE, fetchedAt: "2026-08-27T10:00:00+00:00" }, "non-canonical UTC date"],
  [{ ...SOURCE, url: "https://example.com/article#fragment" }, "URL fragment"],
  [{ ...SOURCE, url: "http://127.0.0.1/article", site: "127.0.0.1" }, "non-public URL"],
  [{ ...SOURCE, url: "http://localhost/article", site: "localhost" }, "local hostname"],
  [{ ...SOURCE, url: "https://service.internal/article", site: "service.internal" }, "internal hostname"],
  [{ ...SOURCE, url: "https://bad_host.com/article", site: "bad_host.com" }, "invalid hostname"],
  [{
    ...SOURCE,
    url: "http://[::ffff:7f00:1]/article",
    site: "::ffff:7f00:1",
  }, "IPv4-mapped local address"],
  [{ ...SOURCE, unexpected: true }, "unexpected field"],
])("a source block with Runtime-invalid %s fails closed", (malformedSource) => {
  expect(citationSourcesFrom([malformedSource])).toBeNull();
});

test("one malformed source rejects the whole structured block", () => {
  render(
    <MarkdownBody
      text="事实[[cite:source-a]]"
      sources={[
        SOURCE,
        {
          ...SOURCE,
          number: 2,
          sourceId: "source-b",
          site: "other.test",
        },
      ]}
      streaming={false}
    />,
  );
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText(/引用未验证/)).toBeDefined();
});

test("ordinary Markdown links keep safe external-link attributes", () => {
  render(
    <MarkdownBody
      text="[普通链接](https://example.com/plain?from=answer)"
      sources={[SOURCE]}
      streaming={false}
    />,
  );
  const link = screen.getByRole("link", { name: "普通链接" });
  expect(link.getAttribute("href")).toBe(
    "https://example.com/plain?from=answer",
  );
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noreferrer noopener");
});

test("first web search approval names the task gate, recipient, and data flow", () => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "web-1",
        turnId: "turn-1",
        approvalId: "approval-1",
        name: "web_search",
        status: "awaiting_approval",
        approvalBehavior: "thread_first_use",
        argumentsSummary: { query: "劳动合同 最新规定" },
        externalRecipient: "DuckDuckGo",
        externalValue: "劳动合同 最新规定",
      }}
      onResolve={() => undefined}
    />,
  );

  expect(screen.getByText("允许此任务联网？")).toBeDefined();
  expect(screen.getByText(/DuckDuckGo/)).toBeDefined();
  expect(screen.getByText(/搜索词将发送到外部服务/)).toBeDefined();
  expect(screen.getByText(/当前模型/)).toBeDefined();
  expect(screen.getByText(/系统网络代理/)).toBeDefined();
  expect(screen.getByRole("button", { name: "允许此任务联网" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "允许一次" })).toBeNull();
});

test("first web read approval names the site and never reveals URL query data", () => {
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "web-read-approval",
        turnId: "turn-1",
        approvalId: "approval-1",
        name: "web_read",
        status: "awaiting_approval",
        approvalBehavior: "thread_first_use",
        argumentsSummary: { url: "https://example.com/article" },
        externalRecipient: "example.com",
        externalValue: "https://example.com/article",
      }}
      onResolve={() => undefined}
    />,
  );

  expect(screen.getByText("允许此任务联网？")).toBeDefined();
  expect(screen.getByText("网址：https://example.com/article")).toBeDefined();
  expect(screen.getByText(/example\.com 会收到普通网页请求/)).toBeDefined();
  expect(screen.getByText(/网页文字会交给当前模型/)).toBeDefined();
  expect(screen.getByRole("button", { name: "允许此任务联网" })).toBeDefined();
  expect(document.body.textContent).not.toContain("?");
});

test("web search expands only bounded safe candidates and labels them unread", () => {
  const { rerender } = render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "web-search",
        name: "web_search",
        status: "succeeded",
        argumentsSummary: { query: "最新规则" },
        resultPreview: '{"query":"private-token","results":[',
        resultTruncated: true,
        resultSummary: {
          kind: "web_search",
          resultCount: 3,
          results: [
            {
              title: "候选一",
              url: "https://example.com/a?id=42#section",
              site: "example.com",
              snippet: "摘要一",
            },
            {
              title: "候选二",
              url: "https://second.test/b",
              site: "second.test",
              snippet: "摘要二",
            },
            {
              title: "危险候选",
              url: "javascript:alert(1)",
              site: "evil.test",
              snippet: "不应显示",
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText("找到 2 条候选")).toBeDefined();
  expect(screen.queryByText("https://example.com/a?id=42#section")).toBeNull();
  const toggle = screen.getByRole("button", { name: "查看候选网页" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  const candidates = screen.getByRole("list", { name: "搜索候选网页" });
  expect(candidates.children).toHaveLength(2);
  const first = screen.getByRole("link", { name: "候选一" });
  expect(first.getAttribute("href")).toBe("https://example.com/a?id=42#section");
  expect(first.getAttribute("rel")).toBe("noreferrer noopener");
  expect(screen.getByText("https://example.com/a?id=42#section")).toBeDefined();
  expect(screen.getByText("摘要一")).toBeDefined();
  expect(screen.getAllByText("未读取正文，不可引用")).toHaveLength(2);
  expect(screen.queryByText("危险候选")).toBeNull();
  rerender(<div />);
});

test("a real truncated web-read preview uses separate metadata and never shows body", () => {
  const longPreview = JSON.stringify({
    source_id: "internal-source",
    title: "条例解读",
    url: "https://example.com/article?private-token=yes",
    site: "example.com",
    fetched_at: "2026-08-27T10:00:00Z",
    character_count: 1234,
    truncated: false,
    text: "不应铺满时间线的网页正文".repeat(30),
  }).slice(0, 400);
  render(
    <ToolCallCard
      item={{
        kind: "tool",
        id: "web-read",
        name: "web_read",
        status: "succeeded",
        durationMs: 18,
        resultPreview: longPreview,
        resultTruncated: true,
        resultSummary: {
          kind: "web_read",
          title: "条例解读",
          site: "example.com",
          characterCount: 1234,
          truncated: false,
        },
      }}
    />,
  );
  expect(screen.getByText("已读取 条例解读")).toBeDefined();
  expect(screen.getByText(/example\.com/)).toBeDefined();
  expect(screen.getByText(/1,234 个字符/)).toBeDefined();
  expect(screen.queryByText(/private-token/)).toBeNull();
  expect(screen.queryByText(/不应铺满时间线/)).toBeNull();
  expect(screen.queryByRole("button", { name: "查看结果" })).toBeNull();
});
