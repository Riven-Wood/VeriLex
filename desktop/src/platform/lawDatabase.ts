import { invoke } from "@tauri-apps/api/core";

export const LAW_DATABASE_COMMAND = "open_law_database";

const OPEN_ERROR = "官方数据库窗口没有打开，请稍后重试。";

/** 在应用内窗口打开国家法律法规数据库官网（导航限定官方域名）。 */
export async function openLawDatabase(): Promise<void> {
  try {
    await invoke(LAW_DATABASE_COMMAND);
  } catch {
    throw new Error(OPEN_ERROR);
  }
}
