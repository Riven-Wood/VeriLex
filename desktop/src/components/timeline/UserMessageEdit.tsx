import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { CopyButton } from "./MessageActions";

export function UserMessageEdit({ text, running, disabled, hasAttachments, createdAt, onBegin, onCancel, onSend }: {
  text: string; running: boolean; disabled?: boolean; hasAttachments?: boolean; createdAt?: string;
  onBegin: () => boolean;
  onCancel: () => void;
  onSend: (text: string) => boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(text);
  const [submitting, setSubmitting] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const editor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!editing) return;
    const frame = requestAnimationFrame(() => editor.current?.scrollIntoView?.({ block: "nearest" }));
    return () => cancelAnimationFrame(frame);
  }, [editing]);
  useEffect(() => { if (!running) setSubmitting(false); }, [running]);
  function cancel() {
    if (submitting) return;
    setEditing(false);
    onCancel();
    requestAnimationFrame(() => editButton.current?.focus());
  }
  return <>
    {!editing && <div className="message-actions">
      {createdAt && <time dateTime={createdAt}>{new Date(createdAt).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</time>}
      <CopyButton text={text} />
      <button ref={editButton} type="button" className="message-action" aria-label="编辑" title="编辑指令：停止当前工作，从这里重新开始" disabled={disabled} onClick={() => {
        if (!onBegin()) return;
        setValue(text); setEditing(true);
      }}><Pencil size={14} /></button>
    </div>}
    {editing && <div ref={editor} className="message-edit">
      <textarea aria-label="编辑已发消息" autoFocus value={value} disabled={submitting} maxLength={100000} onChange={e => setValue(e.target.value)} onKeyDown={e => {
        if (e.key === "Escape" && !e.nativeEvent.isComposing) { e.preventDefault(); cancel(); }
      }} />
      <p className="message-edit-hint">重新发送后，从这条指令重新开始，后续对话将移除。已修改的文件不会恢复。{hasAttachments ? "原附件会保留。" : ""}</p>
      <div className="message-edit-controls">
        <span role="status">{submitting ? "正在发送…" : running ? "正在停止当前工作…" : ""}</span>
        <button type="button" disabled={submitting} onClick={cancel}>取消</button>
        <button type="button" className="message-edit-send" disabled={disabled || running || submitting || (!value.trim() && !hasAttachments)} onClick={() => {
          if (!submitting && !running && onSend(value)) setSubmitting(true);
        }}>重新发送</button>
      </div>
    </div>}
  </>;
}
