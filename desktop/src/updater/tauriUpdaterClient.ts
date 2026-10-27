import { getVersion } from "@tauri-apps/api/app";
import { relaunch as relaunchApp } from "@tauri-apps/plugin-process";
import {
  check as checkForUpdate,
  type DownloadEvent,
} from "@tauri-apps/plugin-updater";

import { UpdateServiceError } from "./contracts";

import type {
  AvailableUpdate,
  UpdateProgress,
  UpdaterClient,
} from "./contracts";

const UPDATE_REQUEST_TIMEOUT_MS = 15_000;
const UPDATE_DOWNLOAD_TIMEOUT_MS = 30 * 60_000;



function isTauriRuntime(): boolean {
  return Reflect.has(globalThis, "__TAURI_INTERNALS__");
}

function progressAdapter(
  onProgress: (progress: UpdateProgress) => void,
): (event: DownloadEvent) => void {
  let downloadedBytes = 0;
  let totalBytes: number | null = null;

  return (event) => {
    switch (event.event) {
      case "Started":
        downloadedBytes = 0;
        totalBytes = event.data.contentLength ?? null;
        onProgress({ phase: "downloading", downloadedBytes, totalBytes });
        break;
      case "Progress":
        downloadedBytes += event.data.chunkLength;
        onProgress({ phase: "downloading", downloadedBytes, totalBytes });
        break;
      case "Finished":
        onProgress({ phase: "installing", downloadedBytes, totalBytes });
        break;
    }
  };
}

export const tauriUpdaterClient: UpdaterClient = {
  get supported() {
    return isTauriRuntime();
  },

  async getCurrentVersion() {
    return isTauriRuntime() ? getVersion() : "开发模式";
  },

  async check() {
    if (!isTauriRuntime()) {
      return null;
    }

    const update = await checkForUpdate({ timeout: UPDATE_REQUEST_TIMEOUT_MS }).catch((error: unknown) => {
      // The native plugin groups non-success HTTP responses (including 404) here.
      // Do not turn a missing release manifest into "already up to date".
      if (String(error).includes("Could not fetch a valid release JSON from the remote")) {
        throw new UpdateServiceError("更新服务暂未提供可用的更新信息，请稍后重试");
      }
      throw error;
    });
    if (!update) {
      return null;
    }

    const availableUpdate: AvailableUpdate = {
      version: update.version,
      currentVersion: update.currentVersion,
      date: update.date ?? null,
      body: update.body ?? "",
      install: (onProgress) =>
        update.downloadAndInstall(progressAdapter(onProgress), {
          timeout: UPDATE_DOWNLOAD_TIMEOUT_MS,
        }),
      close: () => update.close(),
    };

    return availableUpdate;
  },

  async relaunch() {
    if (isTauriRuntime()) {
      await relaunchApp();
    }
  },
};
