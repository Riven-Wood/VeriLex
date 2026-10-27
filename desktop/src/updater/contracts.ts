export type UpdatePhase =
  | "idle"
  | "checking"
  | "up-to-date"
  | "available"
  | "downloading"
  | "installing"
  | "failed"
  | "ready-to-relaunch";

export interface UpdateMetadata {
  version: string;
  currentVersion: string;
  date: string | null;
  body: string;
}

export interface UpdateProgress {
  downloadedBytes: number;
  totalBytes: number | null;
  phase: "downloading" | "installing";
}

export interface AvailableUpdate extends UpdateMetadata {
  close?(): Promise<void>;
  install(onProgress: (progress: UpdateProgress) => void): Promise<void>;
}

export interface UpdaterClient {
  readonly supported: boolean;
  getCurrentVersion(): Promise<string>;
  check(): Promise<AvailableUpdate | null>;
  relaunch(): Promise<void>;
}

export interface UpdateState {
  supported: boolean;
  phase: UpdatePhase;
  currentVersion: string;
  update: UpdateMetadata | null;
  progress: UpdateProgress | null;
  error: string;
  relaunchRequired: boolean;
}

export interface UpdateController {
  state: UpdateState;
  checkForUpdates(): Promise<void>;
  installUpdate(): Promise<void>;
}

/** User-facing failure identified by the native updater adapter. */
export class UpdateServiceError extends Error {}
