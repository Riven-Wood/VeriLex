import { memo, useEffect, useState } from "react";
import { FileText, Image } from "lucide-react";

import { useObjectUrl } from "../../hooks/useObjectUrl";
import type { TimelineAttachment } from "../../timeline/items";

export type AttachmentContentLoader = (
  attachment: TimelineAttachment,
) => Promise<Blob>;

interface TimelineAttachmentsProps {
  attachments: readonly TimelineAttachment[];
  loadContent: AttachmentContentLoader;
  onOpen: (attachment: TimelineAttachment) => void;
}

function formatSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  return `${sizeBytes} B`;
}

function AttachmentEntry({
  attachment,
  loadContent,
  onOpen,
}: {
  attachment: TimelineAttachment;
  loadContent: AttachmentContentLoader;
  onOpen: (attachment: TimelineAttachment) => void;
}) {
  const isImage = attachment.mediaType.startsWith("image/");
  const [blob, setBlob] = useState<Blob | null>(null);

  useEffect(() => {
    if (!isImage) return;
    let cancelled = false;
    loadContent(attachment)
      .then((content) => {
        if (!cancelled) setBlob(content);
      })
      .catch(() => {
        // A failed thumbnail must never block the file card or the click.
        if (!cancelled) setBlob(null);
      });
    return () => {
      cancelled = true;
    };
  }, [attachment.id, isImage, loadContent]);

  const objectUrl = useObjectUrl(blob);
  const label = `预览附件 ${attachment.name}`;

  return (
    <button
      type="button"
      className={
        isImage
          ? "timeline-attachment timeline-attachment-image"
          : "timeline-attachment timeline-attachment-file"
      }
      aria-label={label}
      title={`预览 ${attachment.name} · ${formatSize(attachment.sizeBytes)}`}
      onClick={() => onOpen(attachment)}
    >
      {isImage && objectUrl ? (
        <img
          alt=""
          aria-hidden="true"
          className="timeline-attachment-thumb"
          src={objectUrl}
        />
      ) : isImage ? (
        <Image aria-hidden="true" size={14} />
      ) : (
        <FileText aria-hidden="true" size={14} />
      )}
      <span className="timeline-attachment-name">{attachment.name}</span>
      <span className="timeline-attachment-size">
        {formatSize(attachment.sizeBytes)}
      </span>
    </button>
  );
}

/**
 * Renders a message's attachments as click-to-preview entries: real
 * thumbnails for images and format-labelled file cards for everything else.
 * Thumbnail bytes are fetched lazily per image and revoked when the entry
 * leaves the timeline.
 */
export const TimelineAttachments = memo(function TimelineAttachments({
  attachments,
  loadContent,
  onOpen,
}: TimelineAttachmentsProps) {
  if (attachments.length === 0) return null;
  return (
    <div className="timeline-attachments" aria-label="附件">
      {attachments.map((attachment) => (
        <AttachmentEntry
          attachment={attachment}
          key={attachment.id}
          loadContent={loadContent}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
});
