import { useEffect, useState } from "react";

/**
 * Tracks an object URL for the given blob so preview surfaces can render
 * attachment bytes fetched with the launch token in a header (the token never
 * enters the URL). The URL is revoked whenever the blob is replaced or the
 * consumer unmounts, so preview bytes never outlive their viewer.
 */
export function useObjectUrl(blob: Blob | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (blob === null) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [blob]);

  return url;
}
