import { open } from "@tauri-apps/plugin-dialog";

type DirectoryDialog = (options: {
  directory: true;
  multiple: false;
}) => Promise<string | string[] | null>;

const DIRECTORY_ERROR = "无法选择工作区目录，请重试。";

export async function pickWorkspaceDirectory(
  openDialog: DirectoryDialog = open as DirectoryDialog,
): Promise<string | null> {
  try {
    const selected = await openDialog({ directory: true, multiple: false });
    if (selected === null) return null;
    if (typeof selected !== "string" || selected.length === 0) {
      throw new Error(DIRECTORY_ERROR);
    }
    return selected;
  } catch {
    throw new Error(DIRECTORY_ERROR);
  }
}
