import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SettingsView } from "../src/views/SettingsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

interface PreferencesCall {
  method: string;
  url: string;
  body: string | null;
}

function setupPreferencesFetch(initialMinutes = 30) {
  const calls: PreferencesCall[] = [];
  let current = initialMinutes;
  const failNextPut = { value: false };
  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/v1/preferences")) {
      calls.push({ method: "GET", url, body: null });
      return new Response(JSON.stringify({ background_task_max_minutes: current }), {
        status: 200,
      });
    }
    if (url.includes("/v1/preferences/") && init?.method === "PUT") {
      calls.push({ method: "PUT", url, body: init.body ? String(init.body) : null });
      if (failNextPut.value) {
        failNextPut.value = false;
        return new Response(
          JSON.stringify({ error: { code: "internal_error", message: "boom", retryable: false } }),
          { status: 500 },
        );
      }
      current = JSON.parse(String(init.body)).value;
      return new Response(JSON.stringify({ background_task_max_minutes: current }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify([]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:8766";
  globalThis.__VERILEX_API_TOKEN__ = "token";
  return { calls, failNextPut };
}

function viewProps() {
  return {
    configured: {
      models: 0,
      mcp_servers: 0,
      skills: 0,
      folders: 0,
    },
    reduceMotion: false,
    onReduceMotionChange: () => {},
    embeddedNavigation: true,
  };
}

async function openAgentCategory() {
  const button = screen.getByRole("button", { name: /助手与权限/ });
  await act(async () => {
    button.click();
  });
}

test("agent category shows the background command limit with the runtime value", async () => {
  setupPreferencesFetch(45);

  render(<SettingsView {...viewProps()} />);
  await openAgentCategory();

  const input = screen.getByLabelText(/后台命令最长运行时间/) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("45"));
});

test("saving a valid limit calls PUT and reports success", async () => {
  const { calls } = setupPreferencesFetch(30);

  render(<SettingsView {...viewProps()} />);
  await openAgentCategory();
  const input = screen.getByLabelText(/后台命令最长运行时间/) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("30"));

  await act(async () => {
    fireEvent.change(input, { target: { value: "90" } });
  });
  expect(screen.queryByText(/已保存/)).toBeNull();

  await act(async () => {
    screen.getByRole("button", { name: "保存", exact: true }).click();
  });

  await waitFor(() => expect(screen.getByText(/已保存/)).toBeDefined());
  const put = calls.find((call) => call.method === "PUT");
  expect(put?.url).toBe(
    "http://127.0.0.1:8766/v1/preferences/background_task_max_minutes",
  );
  expect(put?.body).toBe(JSON.stringify({ value: 90 }));
});

test("out-of-range values are blocked before saving", async () => {
  const { calls } = setupPreferencesFetch(30);

  render(<SettingsView {...viewProps()} />);
  await openAgentCategory();
  const input = screen.getByLabelText(/后台命令最长运行时间/) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("30"));

  for (const bad of ["0", "121", "-5"]) {
    await act(async () => {
      fireEvent.change(input, { target: { value: bad } });
    });
    expect(screen.getByText(/请输入 1 到 120 之间的整数/)).toBeDefined();
    expect(screen.getByRole("button", { name: "保存", exact: true }).hasAttribute("disabled")).toBe(
      true,
    );
  }

  await act(async () => {
    fireEvent.change(input, { target: { value: "60" } });
  });
  expect(screen.queryByText(/请输入 1 到 120 之间的整数/)).toBeNull();
  expect(screen.getByRole("button", { name: "保存", exact: true }).hasAttribute("disabled")).toBe(
    false,
  );
  expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
});

test("a failed save shows an error without claiming success", async () => {
  const { failNextPut } = setupPreferencesFetch(30);

  render(<SettingsView {...viewProps()} />);
  await openAgentCategory();
  const input = screen.getByLabelText(/后台命令最长运行时间/) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("30"));
  failNextPut.value = true;

  await act(async () => {
    fireEvent.change(input, { target: { value: "45" } });
  });
  await act(async () => {
    screen.getByRole("button", { name: "保存", exact: true }).click();
  });

  await waitFor(() => expect(screen.getByText(/保存失败/)).toBeDefined());
  expect(screen.queryByText(/已保存/)).toBeNull();
});
