import { useCallback, useEffect, useState } from "react";

import { listToolCapabilities } from "../runtime/api";
import type { ToolCapabilityRecord } from "../runtime/contracts";

type ToolCapabilitiesLoader = (
  signal?: AbortSignal,
) => Promise<ToolCapabilityRecord[]>;

interface UseToolCapabilitiesOptions {
  loader?: ToolCapabilitiesLoader;
}

export interface ToolCapabilitiesState {
  status: "loading" | "ready" | "error";
  tools: ToolCapabilityRecord[];
  error: string;
  retry: () => void;
}

export function useToolCapabilities(
  { loader = listToolCapabilities }: UseToolCapabilitiesOptions = {},
): ToolCapabilitiesState {
  const [status, setStatus] = useState<ToolCapabilitiesState["status"]>("loading");
  const [tools, setTools] = useState<ToolCapabilityRecord[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  const retry = useCallback(() => {
    setRevision((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setStatus("loading");
    setError("");

    loader(controller.signal)
      .then((records) => {
        if (!active || controller.signal.aborted) return;
        setTools(records);
        setStatus("ready");
      })
      .catch(() => {
        if (!active || controller.signal.aborted) return;
        setTools([]);
        setError("无法读取工具目录。");
        setStatus("error");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [loader, revision]);

  return { status, tools, error, retry };
}
