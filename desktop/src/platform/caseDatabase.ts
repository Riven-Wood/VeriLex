import { invoke } from "@tauri-apps/api/core";

/** Fixed destinations only; the application never retrieves their contents. */
export async function openCaseDatabase(database: "library" | "judgments" | "templates"): Promise<void> {
  try { await invoke("open_case_database", { database }); }
  catch { throw new Error("系统浏览器没有打开，请稍后重试。"); }
}
