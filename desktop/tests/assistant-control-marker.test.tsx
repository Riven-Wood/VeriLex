import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MarkdownBody } from "../src/components/timeline/MarkdownBody";
import { TurnGroup } from "../src/components/timeline/TurnGroup";
import { visibleAssistantText } from "../src/timeline/assistantText";
import { exportMarkdown } from "../src/timeline/exportMarkdown";

afterEach(cleanup);

test("the internal web offer marker stays hidden in final text and older exported history", () => {
  const text = "待核验。[[offer_web_access]]可继续讨论。";
  const {container} = render(<MarkdownBody text={text} />);
  expect(container.textContent).toBe("待核验。可继续讨论。");
  expect(exportMarkdown("测试", "2026-09-22", [{kind: "assistant", id: "old", isStreaming: false, segments: [{type: "text", text, isStreaming: false}]}])).not.toContain("offer_web_access");
});

test("every streamed prefix is withheld without hiding ordinary bracketed text", () => {
  const marker = "[[offer_web_access]]";
  for (let i = 2; i <= marker.length; i += 1) {
    expect(visibleAssistantText("答复" + marker.slice(0, i), true)).toBe("答复");
  }
  expect(visibleAssistantText("答复[[普通文字]]", true)).toBe("答复[[普通文字]]");
  expect(visibleAssistantText("答复[[officer]]", true)).toBe("答复[[officer]]");
  expect(visibleAssistantText("答复[[off", false)).toBe("答复[[off");
});

test("copying an older completed answer omits its internal control marker", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {value: {writeText}, configurable: true});
  render(<TurnGroup active={false} items={[{kind: "assistant", id: "old", isStreaming: false,
    segments: [{type: "text", text: "待核验。[[offer_web_access]]", isStreaming: false}]}]} renderItem={() => null} />);
  fireEvent.click(screen.getByRole("button", {name: "复制"}));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("待核验。"));
});
