import { useCallback, useEffect, useState } from "react";
import { Scale, Trash2 } from "lucide-react";

import { clearLegalCache, getLegalCacheStats } from "../runtime/api";

function formatFetchedAt(iso: string | null): string {
  if (!iso) return "";
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return iso;
  const date = new Date(parsed);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * 设置 → 工作区与数据：法律检索的本机法条缓存（规格硬约束 5——
 * 缓存仅存本机、带读取时间、清除不可逆且有一句必要说明）。
 */
export function LegalCacheSettings() {
  const [stats, setStats] = useState<{ count: number; oldest: string | null } | null>(
    null,
  );
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setFailed(false);
    getLegalCacheStats()
      .then((value) => {
        setStats({ count: value.count, oldest: value.oldestFetchedAt });
      })
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const clear = () => {
    setClearing(true);
    setError("");
    clearLegalCache()
      .then(() => {
        setConfirming(false);
        load();
      })
      .catch(() => setError("清除失败，请重试。"))
      .finally(() => setClearing(false));
  };

  return (
    <div className="legal-cache-settings">
      <div className="legal-cache-summary">
        <Scale aria-hidden="true" size={18} />
        <span>
          <strong>法律检索缓存</strong>
          {failed ? (
            <small>暂时无法读取缓存数量。</small>
          ) : stats === null ? (
            <small>正在读取…</small>
          ) : (
            <small>
              {`本机已缓存 ${stats.count} 条法规原文`}
              {stats.count > 0 && stats.oldest
                ? `（最早读取于 ${formatFetchedAt(stats.oldest)}）`
                : ""}
              ；仅用于提速，不会上传。
            </small>
          )}
        </span>
      </div>
      {failed ? (
        <button type="button" onClick={load}>重试</button>
      ) : stats && stats.count > 0 ? (
        confirming ? (
          <div className="legal-cache-confirm" role="alertdialog" aria-label="确认清除法条缓存">
            <span>清除后不可恢复，下次检索会重新读取官方数据库。</span>
            <button type="button" onClick={clear} disabled={clearing}>
              {clearing ? "清除中…" : "确认清除"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={clearing}>
              取消
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => setConfirming(true)}>
            <Trash2 aria-hidden="true" size={14} />
            清除法条缓存
          </button>
        )
      ) : null}
      {error ? <p className="legal-cache-error" role="alert">{error}</p> : null}
    </div>
  );
}
