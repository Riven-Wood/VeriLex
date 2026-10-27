import type { UpdateState } from "./contracts";

export function updateAction(state: UpdateState) {
  const { phase, update, relaunchRequired, supported } = state;
  const busy = phase === "checking" || phase === "downloading" || phase === "installing";
  const installed = phase === "ready-to-relaunch";
  return {
    disabled: !supported || busy || (installed && !relaunchRequired),
    installs: update !== null || installed,
    label: phase === "downloading" ? "正在下载"
      : phase === "installing" ? "正在安装"
        : installed ? (relaunchRequired ? "重新启动" : "正在重启")
          : update ? (phase === "failed" ? "重试" : "立即更新")
            : phase === "failed" ? "重新检查" : "检查更新",
  };
}

export function formatUpdateBytes(value: number): string {
  if (value < 1024) return `${value.toLocaleString("en-US")} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

export function updateProgressText(state: UpdateState): string | null {
  if (state.phase === "installing") return "正在校验并安装更新";
  if (state.phase !== "downloading" || !state.progress) return null;
  const { downloadedBytes, totalBytes } = state.progress;
  return `已下载 ${formatUpdateBytes(downloadedBytes)}${totalBytes === null ? "" : ` / ${formatUpdateBytes(totalBytes)}`}`;
}
