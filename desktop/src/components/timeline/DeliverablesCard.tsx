import { memo, useState } from "react";

import type { ThreadArtifactRecord } from "../../runtime/contracts";

const MAX_FILES = 5;

interface DeliverablesCardProps {
  artifacts: ThreadArtifactRecord[];
  onOpen: (workspaceId: string, path: string) => void;
  onViewAll?: () => void;
}

/**
 * The conversation's tail card: the files this task actually delivered.
 *
 * Only real published artifacts count — legacy workspace footprints never
 * present themselves as deliverables. The head row collapses the list; the
 * card spans the message column and scales with the window.
 */
export const DeliverablesCard = memo(function DeliverablesCard({
  artifacts,
  onOpen,
  onViewAll,
}: DeliverablesCardProps) {
  const [open, setOpen] = useState(true);
  const published = artifacts.filter(
    (artifact): artifact is Extract<ThreadArtifactRecord, { kind: "artifact" }> =>
      artifact.kind === "artifact"
      && Boolean(artifact.published_workspace_id)
      && Boolean(artifact.published_relative_path),
  );
  const files = published.slice(0, MAX_FILES);
  if (files.length === 0) return null;
  return (
    <div className="deliverables-card">
      <button
        type="button"
        className="deliverables-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="reasoning-chevron" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        产出的文件 · {published.length}
      </button>
      {open && (
        <>
          <ul className="deliverables-list">
            {files.map((artifact) => (
              <li key={artifact.id}>
                <button
                  type="button"
                  className="deliverables-file-button"
                  onClick={() =>
                    onOpen(
                      artifact.published_workspace_id,
                      artifact.published_relative_path,
                    )
                  }
                >
                  {artifact.name}
                </button>
              </li>
            ))}
          </ul>
          {published.length > MAX_FILES && onViewAll ? (
            <button type="button" className="deliverables-view-all" onClick={onViewAll}>
              全部 {published.length} 个文件
            </button>
          ) : null}
        </>
      )}
    </div>
  );
});
