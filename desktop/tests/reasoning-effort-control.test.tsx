import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ReasoningEffortControl } from "../src/components/ReasoningEffortControl";

afterEach(cleanup);

test("selects one supported reasoning effort with radio semantics", () => {
  const onChange = vi.fn();
  render(
    <ReasoningEffortControl
      value="auto"
      providerKind="openai_compatible"
      supportedEfforts={["low", "medium", "high"]}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "推理强度，当前自动" }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: "高" }));
  expect(onChange).toHaveBeenCalledWith("high");
});

test("confirmed unsupported model offers only auto and explains why", () => {
  render(
    <ReasoningEffortControl
      value="auto"
      providerKind="openai_compatible"
      supportedEfforts={[]}
      onChange={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "推理强度，当前自动" }));
  expect(screen.getAllByRole("menuitemradio")).toHaveLength(1);
  expect(screen.getByText("由当前模型决定")).toBeTruthy();
});

test("unknown OpenAI-compatible capability keeps choices with a compatibility note", () => {
  render(
    <ReasoningEffortControl
      value="auto"
      providerKind="openai_compatible"
      supportedEfforts={null}
      onChange={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "推理强度，当前自动" }));
  expect(screen.getAllByRole("menuitemradio")).toHaveLength(4);
  expect(screen.getByText("兼容性由模型服务确认；不支持时会安全回到自动。")).toBeTruthy();
});
