import {
  createProvider,
  deleteProvider,
  discoverProviderModels,
  listModelContextWindows,
  refreshModelContextWindows,
  resetModelContextWindowOverride,
  setModelContextWindowOverride,
  listProviders,
  testProviderConnection,
  testSavedProvider,
} from "./api";
import type {
  ProviderDraft,
  ProviderProbeResult,
  ProviderModelContextWindowRecord,
  ProviderRecord,
} from "./contracts";

export interface ProviderApi {
  listProviders: (signal?: AbortSignal) => Promise<ProviderRecord[]>;
  testProviderConnection: (
    draft: ProviderDraft,
    signal?: AbortSignal,
  ) => Promise<ProviderProbeResult>;
  createProvider: (
    draft: ProviderDraft,
    signal?: AbortSignal,
  ) => Promise<ProviderRecord>;
  testSavedProvider: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderProbeResult>;
  discoverProviderModels: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderProbeResult>;
  listModelContextWindows: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderModelContextWindowRecord[]>;
  refreshModelContextWindows: (
    providerId: string,
    signal?: AbortSignal,
  ) => Promise<ProviderModelContextWindowRecord[]>;
  setModelContextWindowOverride: (
    providerId: string,
    model: string,
    tokens: number,
    signal?: AbortSignal,
  ) => Promise<ProviderModelContextWindowRecord>;
  resetModelContextWindowOverride: (
    providerId: string,
    model: string,
    signal?: AbortSignal,
  ) => Promise<ProviderModelContextWindowRecord>;
  deleteProvider: (providerId: string, signal?: AbortSignal) => Promise<void>;
}

export const runtimeProviderApi: ProviderApi = {
  listProviders,
  testProviderConnection,
  createProvider,
  testSavedProvider,
  discoverProviderModels,
  listModelContextWindows,
  refreshModelContextWindows,
  setModelContextWindowOverride,
  resetModelContextWindowOverride,
  deleteProvider,
};
