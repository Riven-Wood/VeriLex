import { ServerCrash } from "lucide-react";

interface RuntimeBannerProps {
  state: "starting" | "offline" | "healthy";
  message?: string;
  onRetry?: () => void;
  retrying?: boolean;
}

/**
 * Renders only the offline failure banner. The healthy and starting states
 * are silent by design (product decision 2026-08-31): connection status and
 * counts live on their own settings pages, never as ambient chrome. When the
 * Runtime does fail, the banner stays honest: error detail plus a retry.
 */
export function RuntimeBanner({ state, message, onRetry, retrying = false }: RuntimeBannerProps) {
  if (state !== "offline") {
    return null;
  }

  return (
    <div className="runtime-banner" data-state="offline" role="status">
      <ServerCrash aria-hidden="true" size={15} />
      <span>本机后台暂时无法连接</span>
      {message ? <span className="runtime-detail">{message}</span> : null}
      {onRetry ? (
        <button
          className="runtime-retry-button"
          type="button"
          aria-label="重新检查本机后台状态"
          disabled={retrying}
          onClick={onRetry}
        >
          {retrying ? "正在重试" : "重试"}
        </button>
      ) : null}
    </div>
  );
}
