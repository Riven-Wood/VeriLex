import { useEffect, useId, useRef, useState } from "react";

import { updateAction, updateProgressText } from "../updater/presentation";

import type { UpdateController } from "../updater/contracts";

export interface UpdatePanelProps {
  controller: UpdateController;
}

function formatReleaseDate(value: string | null): string {
  if (!value || Number.isNaN(Date.parse(value))) {
    return "发布日期未提供";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function UpdatePanel({ controller }: UpdatePanelProps) {
  const { error, phase, progress, update } = controller.state;
  const [open, setOpen] = useState(false);
  const [showNotes, setShowNotes] = useState(false);
  const panelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setOpen(false);
    setShowNotes(false);
  }, [update?.body, update?.currentVersion, update?.date, update?.version]);

  useEffect(() => {
    if (!open) {
      return;
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setShowNotes(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  if (!update) {
    return null;
  }

  const busy = phase === "downloading" || phase === "installing";
  const action = updateAction(controller.state);
  const progressPercent = progress?.totalBytes
    ? Math.min(100, Math.round((progress.downloadedBytes / progress.totalBytes) * 100))
    : null;

  function closePanel() {
    setOpen(false);
    setShowNotes(false);
    triggerRef.current?.focus();
  }

  return (
    <div className="update-panel-anchor">
      {open ? (
        <section
          className="update-panel"
          id={panelId}
          role="region"
          aria-label="软件更新"
        >
          <header className="update-panel-header">
            <div>
              <span className="update-panel-kicker">软件更新</span>
              <code className="update-version-route">
                {update.currentVersion} → {update.version}
              </code>
            </div>
            <button
              className="update-close"
              type="button"
              aria-label="关闭软件更新"
              onClick={closePanel}
            >
              ×
            </button>
          </header>

          {showNotes ? (
            <div className="update-notes-view">
              <button
                className="update-text-action"
                type="button"
                onClick={() => setShowNotes(false)}
              >
                返回
              </button>
              <p className="update-notes">{update.body || "未提供更新说明"}</p>
            </div>
          ) : (
            <div className="update-summary">
              <time dateTime={update.date ?? undefined}>
                {formatReleaseDate(update.date)}
              </time>

              {busy && progress ? (
                <div className="update-progress-block" aria-live="polite">
                  <div
                    className="update-progress-track"
                    role="progressbar"
                    aria-label={phase === "installing" ? "正在安装更新" : "正在下载更新"}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progressPercent ?? undefined}
                  >
                    <span style={{ width: `${progressPercent ?? 8}%` }} />
                  </div>
                  <span>
                    {updateProgressText(controller.state)}
                  </span>
                </div>
              ) : null}

              {phase === "available" ? <p>更新完成后将自动重启</p> : null}
              {error ? <p className="update-error" role="alert">{error}</p> : null}

              <div className="update-actions">
                <button
                  className="update-text-action"
                  type="button"
                  onClick={() => setShowNotes(true)}
                >
                  查看更新说明
                </button>
                <button
                  className="update-install"
                  type="button"
                  disabled={action.disabled}
                  onClick={() => void controller.installUpdate()}
                >
                  {action.label}
                </button>
              </div>
            </div>
          )}
        </section>
      ) : null}

      <button
        ref={triggerRef}
        className="update-entry"
        type="button"
        aria-label={`${phase === "ready-to-relaunch" ? "更新已安装" : "发现新版本"} ${update.version}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((current) => !current);
          if (open) setShowNotes(false);
        }}
      >
        <span className="update-entry-dot" aria-hidden="true" />
        <span>{phase === "ready-to-relaunch" ? "更新已安装" : busy ? "正在更新" : "发现新版本"}</span>
        <code>{update.version}</code>
      </button>
    </div>
  );
}
