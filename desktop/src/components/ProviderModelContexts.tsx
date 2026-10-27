import { useEffect, useState } from "react";

import {
  formatContextWindowTokens,
  parseContextWindowInput,
} from "../providers/contextWindowInput";
import type {
  ProviderModelContextWindowRecord,
  ProviderRecord,
} from "../runtime/contracts";
import type { ProviderApi } from "../runtime/providerClient";

interface ProviderModelContextsProps {
  api: ProviderApi;
  provider: ProviderRecord;
  onChanged: () => void;
}

function statusCopy(record: ProviderModelContextWindowRecord): string {
  const value = record.context_window_tokens == null
    ? "未知"
    : formatContextWindowTokens(record.context_window_tokens);
  const source = record.context_window_source === "user_declared"
    ? `手动 ${value}`
    : record.context_window_source === "provider"
      ? `${value} · Provider 自动`
      : record.context_window_source === "curated"
        ? `${value} · VeriLex 资料`
        : "未知";
  return record.is_available ? source : `${source} · 当前不可用`;
}

export function ProviderModelContexts({
  api,
  provider,
  onChanged,
}: ProviderModelContextsProps) {
  const [rows, setRows] = useState<ProviderModelContextWindowRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [refreshError, setRefreshError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError("");
    void api.listModelContextWindows(provider.id, controller.signal)
      .then((records) => {
        if (!controller.signal.aborted) setRows(records);
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadError("无法读取已保存的模型上限。请重试。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, provider.id]);

  function replaceRow(updated: ProviderModelContextWindowRecord) {
    setRows((current) => current.map((row) => (
      row.model === updated.model ? updated : row
    )));
  }

  async function refresh() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError("");
    try {
      const records = await api.refreshModelContextWindows(provider.id);
      setRows(records);
      onChanged();
    } catch {
      setRefreshError("自动资料刷新失败，请重试。");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <section
      className="provider-context-manager"
      role="region"
      aria-label={`${provider.name} 的模型上下文上限`}
    >
      <div className="provider-context-toolbar">
        <span>{loading ? "正在读取模型上限…" : `共 ${rows.length} 个模型`}</span>
        <button
          type="button"
          disabled={refreshing}
          aria-label={refreshError
            ? `重试刷新 ${provider.name} 的自动资料`
            : `刷新 ${provider.name} 的自动资料`}
          onClick={() => void refresh()}
        >
          {refreshing ? "刷新中…" : "刷新自动资料"}
        </button>
      </div>
      {loadError ? <p className="provider-row-error" role="alert">{loadError}</p> : null}
      {refreshError ? <p className="provider-row-error" role="alert">{refreshError}</p> : null}
      {!loading && !loadError && rows.length === 0 ? (
        <p className="provider-context-empty">尚无模型资料，请刷新自动资料。</p>
      ) : null}
      {rows.length > 0 ? (
        <div className="provider-context-list" role="list">
          {rows.map((row) => (
            <ModelContextRow
              key={row.model}
              api={api}
              providerId={provider.id}
              record={row}
              onChanged={(updated) => {
                replaceRow(updated);
                onChanged();
              }}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

interface ModelContextRowProps {
  api: ProviderApi;
  providerId: string;
  record: ProviderModelContextWindowRecord;
  onChanged: (record: ProviderModelContextWindowRecord) => void;
}

function ModelContextRow({
  api,
  providerId,
  record,
  onChanged,
}: ModelContextRowProps) {
  const [draft, setDraft] = useState(
    record.context_window_tokens == null
      ? ""
      : formatContextWindowTokens(record.context_window_tokens),
  );
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft(record.context_window_tokens == null
      ? ""
      : formatContextWindowTokens(record.context_window_tokens));
    setError("");
  }, [record]);

  async function save() {
    const parsed = parseContextWindowInput(draft);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setBusy("save");
    setError("");
    try {
      const updated = await api.setModelContextWindowOverride(
        providerId,
        record.model,
        parsed.tokens,
      );
      onChanged(updated);
    } catch {
      setError("保存失败，请重试。");
    } finally {
      setBusy(null);
    }
  }

  async function reset() {
    setBusy("reset");
    setError("");
    try {
      const updated = await api.resetModelContextWindowOverride(
        providerId,
        record.model,
      );
      onChanged(updated);
    } catch {
      setError("恢复自动值失败，请重试。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="provider-context-row" role="listitem">
      <div className="provider-context-copy">
        <strong>{record.model}</strong>
        <span>{statusCopy(record)}</span>
      </div>
      <input
        aria-label={`设置 ${record.model} 上下文上限`}
        value={draft}
        disabled={busy !== null}
        onChange={(event) => {
          setDraft(event.target.value);
          setError("");
        }}
        placeholder="例如 64K"
      />
      <button
        type="button"
        disabled={busy !== null}
        aria-label={`保存 ${record.model} 上下文上限`}
        onClick={() => void save()}
      >
        {busy === "save" ? "保存中" : "保存"}
      </button>
      <button
        type="button"
        disabled={busy !== null || record.manual_tokens == null}
        aria-label={`恢复 ${record.model} 自动上限`}
        onClick={() => void reset()}
      >
        {busy === "reset" ? "恢复中" : "自动"}
      </button>
      {error ? <p className="provider-context-error" role="alert">{error}</p> : null}
    </div>
  );
}
