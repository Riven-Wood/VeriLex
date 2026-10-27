import { memo, useEffect, useState } from "react";

import { getBackgroundTask } from "../../runtime/api";
import type { BackgroundTaskRecord } from "../../runtime/contracts";

const TERMINAL_LABELS: Record<string, string> = {
  completed: "成功",
  failed: "失败",
  stopped: "已停止",
  expired: "已超时停止",
  interrupted: "随应用退出中断",
};

export interface BackgroundTaskLiveProps {
  outputText: string;
  status: string | null;
  error: string | null;
}

interface BackgroundTaskCardProps {
  threadId: string;
  task: BackgroundTaskRecord;
  live?: BackgroundTaskLiveProps | null;
  onStop?: (taskId: string) => void;
  stopPending?: boolean;
  nowMs?: () => number;
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds} 秒`;
  return `${minutes} 分 ${seconds} 秒`;
}

/**
 * Live card for a background task started by ``run_background_command``.
 *
 * While the task runs it shows the accumulated output tail (fed by the
 * polling hook) and a stop button that hits the REST endpoint directly —
 * stopping a background task never needs an approval. Terminal states keep
 * the retained output behind an expandable toggle, lazily fetching the full
 * detail (including omission markers) from the Runtime.
 */
export const BackgroundTaskCard = memo(function BackgroundTaskCard({
  threadId,
  task,
  live = null,
  onStop,
  stopPending = false,
  nowMs = () => Date.now(),
}: BackgroundTaskCardProps) {
  const [showFinalOutput, setShowFinalOutput] = useState(false);
  const [finalOutput, setFinalOutput] = useState<string | null>(null);
  const status = live?.status ?? task.status;
  const isRunning = status === "running";
  const terminalLabel = TERMINAL_LABELS[status] ?? status;

  useEffect(() => {
    setFinalOutput(null);
  }, [task.id]);

  useEffect(() => {
    if (!showFinalOutput || isRunning || finalOutput !== null) return;
    let cancelled = false;
    getBackgroundTask(threadId, task.id)
      .then((detail) => {
        if (!cancelled) setFinalOutput(detail.outputText);
      })
      .catch(() => {
        if (!cancelled) {
          setFinalOutput("最终输出暂时无法获取，请稍后重试。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showFinalOutput, isRunning, finalOutput, task.id, threadId]);

  const startedMs = Date.parse(task.startedAt);
  const endMs = task.finishedAt ? Date.parse(task.finishedAt) : null;
  const elapsed = formatElapsed(
    Number.isFinite(startedMs) ? (endMs ?? nowMs()) - startedMs : 0,
  );

  return (
    <div className="tool-card background-task-card" data-background-status={status}>
      <div className="tool-card-head">
        <span className="tool-status-dot" aria-hidden="true" />
        <span className="tool-name">后台运行命令</span>
        <span className="tool-status-label">
          {isRunning ? "执行中" : terminalLabel}
        </span>
        <span className="tool-duration">
          {isRunning ? `已运行 ${elapsed}` : `共运行 ${elapsed}`}
        </span>
      </div>
      <div className="tool-args">
        {task.command} · 用途：{task.description}
      </div>
      {isRunning && (
        <div className="tool-approval-impact">命令在后台运行，可随时停止</div>
      )}
      {isRunning && live?.error && (
        <div className="tool-approval-error" role="alert">
          {live.error}
        </div>
      )}
      {isRunning && (
        <>
          <pre className="background-task-output" aria-label="后台命令实时输出">
            {live?.outputText || "（等待输出…）"}
          </pre>
          <div className="tool-approval-actions">
            <button
              type="button"
              className="tool-approval-deny"
              disabled={stopPending}
              onClick={() => onStop?.(task.id)}
            >
              停止
            </button>
          </div>
        </>
      )}
      {!isRunning && status === "interrupted" && (
        <div className="tool-approval-impact">
          这条后台命令随应用退出而中断，未继续运行。
        </div>
      )}
      {!isRunning && (
        <div className="tool-result">
          <button
            type="button"
            className="tool-result-toggle"
            aria-expanded={showFinalOutput}
            onClick={() => setShowFinalOutput((value) => !value)}
          >
            {showFinalOutput ? "收起最终输出" : "查看最终输出"}
          </button>
          {showFinalOutput && (
            <pre className="tool-result-preview">
              {finalOutput ?? "正在读取最终输出…"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
});
