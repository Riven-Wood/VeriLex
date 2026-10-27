import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import './DetailDialog.css';

export function DetailDialog({ title, children, onClose, busy = false, className = '', returnFocus }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean; className?: string; returnFocus?: HTMLElement | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    // macOS mouse clicks may leave activeElement on the page instead of the trigger.
    const trigger = returnFocus ?? document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => { if (trigger?.isConnected) trigger.focus(); };
  }, [returnFocus]);
  return <dialog ref={ref} className={`detail-dialog ${className}`} aria-label={title}
    onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
    <header className="detail-dialog-heading"><h2>{title}</h2><button type="button" aria-label="关闭" disabled={busy} onClick={onClose}><X size={18}/></button></header>
    {children}
  </dialog>;
}
