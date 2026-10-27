import { FileText, Image, LoaderCircle, RotateCcw, X } from "lucide-react";

import type { ComposerAttachment } from "../hooks/useComposerAttachments";

interface ComposerAttachmentChipsProps {
  attachments: readonly ComposerAttachment[];
  onRetry: (key: string) => void;
  onRemove: (key: string) => void;
}

export function ComposerAttachmentChips({
  attachments,
  onRetry,
  onRemove,
}: ComposerAttachmentChipsProps) {
  if (attachments.length === 0) return null;
  return (
    <div className="composer-attachments" aria-label="已选附件">
      {attachments.map((attachment) => (
        <div
          className={`composer-attachment composer-attachment-${attachment.status}`}
          key={attachment.key}
          title={attachment.error || attachment.name}
        >
          {attachment.mediaType.startsWith("image/")
            ? <Image aria-hidden="true" size={15} />
            : <FileText aria-hidden="true" size={15} />}
          <span>{attachment.name}</span>
          {attachment.status === "pending" && (
            <LoaderCircle className="attachment-spinner" aria-label="上传中" size={14} />
          )}
          {attachment.status === "failed" && (
            <button type="button" aria-label={`重试 ${attachment.name}`} onClick={() => onRetry(attachment.key)}>
              <RotateCcw aria-hidden="true" size={13} />
            </button>
          )}
          <button type="button" aria-label={`移除 ${attachment.name}`} onClick={() => onRemove(attachment.key)}>
            <X aria-hidden="true" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
