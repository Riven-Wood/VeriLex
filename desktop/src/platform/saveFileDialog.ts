import { invoke } from "@tauri-apps/api/core";
export async function saveConversation(defaultName: string, content: string): Promise<boolean> {
  return invoke<boolean>("save_conversation", {defaultName, content});
}
