import { invoke } from "@tauri-apps/api/core";

import { YUANDIAN_CONSOLE_COMMAND } from "../mcp/yuandian";

const OPEN_CONSOLE_ERROR = "元典窗口没有打开，请稍后重试。";

export async function openYuandianConsole(): Promise<void> {
  try {
    await invoke(YUANDIAN_CONSOLE_COMMAND);
  } catch {
    throw new Error(OPEN_CONSOLE_ERROR);
  }
}
