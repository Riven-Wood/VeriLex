import { useEffect, useRef, useState } from "react";

interface WebAccessOfferProps {
  disabled: boolean;
  onEnable: () => Promise<boolean>;
  onSend: (text: string) => void;
}

const FOLLOW_UP = "请联网补充以上问题";

export function WebAccessOffer({
  disabled,
  onEnable,
  onSend,
}: WebAccessOfferProps) {
  const [pending, setPending] = useState(false);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleClick() {
    if (disabled || pending) return;
    setPending(true);
    try {
      const enabled = await onEnable();
      if (mountedRef.current && enabled) onSend(FOLLOW_UP);
    } finally {
      if (mountedRef.current) setPending(false);
    }
  }

  return (
    <div className="web-access-offer">
      <span>需要最新公开资料时，可以由你决定是否联网。</span>
      <button
        type="button"
        disabled={disabled || pending}
        onClick={() => void handleClick()}
      >
        {pending ? "正在开启…" : "联网补充"}
      </button>
    </div>
  );
}
