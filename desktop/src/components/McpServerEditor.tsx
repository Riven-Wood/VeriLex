import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { mcpErrorCopy } from "../mcp/errorCopy";
import { previewMcpImport, safeMcpUrl, type McpImportPreview } from "../mcp/import";
import {
  createMcpServer,
  parseMcpImport,
  RuntimeApiError,
  testMcpServerDraft,
  testSavedMcpServer,
  updateMcpServer,
} from "../runtime/api";
import type {
  CreateMcpServerInput,
  McpParsedServerDraft,
  McpRiskFinding,
  McpSecretPatch,
  McpServerDraft,
  McpServerPatch,
  McpServerRecord,
  McpTestResult,
  McpToolDecision,
  McpTransport,
} from "../runtime/contracts";

import type { CatalogCredential } from "../mcp/catalog";

type EditorMode = "settings" | "thread";

export interface McpServerEditorProps {
  mode: EditorMode;
  server?: McpServerRecord;
  /** Public template only; always scanned by Runtime before testing. */
  initialDraft?: McpServerDraft;
  credential?: CatalogCredential;
  onSaved?: (server: McpServerRecord) => void;
  /** Task 11 owns the atomic Thread selection; this callback receives the reviewed input. */
  onAtomicSelected?: (input: CreateMcpServerInput) => Promise<void> | void;
  onCancel?: () => void;
  onComplete?: () => void;
  onBusyChange?: (busy: boolean) => void;
}

interface EditorEntry {
  key: string;
  sourceName: string | null;
  sourceValidated: boolean;
  draft: McpServerDraft;
  secretPatch: McpSecretPatch;
  risks: McpRiskFinding[];
  acceptedRiskCodes: string[];
  selected: boolean;
  result: McpTestResult | null;
  decisions: McpToolDecision[];
  error: string;
  lineInputs: {
    env: string;
    secretEnv: string;
    headers: string;
    secretHeaders: string;
  };
}

const EMPTY_DRAFT: McpServerDraft = {
  displayName: "MCP 服务器",
  transport: "stdio",
  command: "",
  args: [],
  cwd: null,
  url: null,
  env: {},
  headers: {},
  secretEnvKeys: [],
  secretHeaderKeys: [],
  connectTimeoutMs: 10_000,
  requestTimeoutMs: 30_000,
};

const BLOCK_RISK_CODES = new Set([
  "mcp_config_invalid",
  "mcp_secret_in_arguments",
  "mcp_url_credentials",
  "mcp_secret_in_url",
]);
const MAX_IMPORT_BYTES = 256 * 1024;
const MAX_FIELD_BYTES = 64 * 1024;

function validFieldText(value: string, maxBytes = MAX_FIELD_BYTES): boolean {
  return new TextEncoder().encode(value).byteLength <= maxBytes
    && !Array.from(value).some((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127;
    });
}

function publicDraft(value: McpServerDraft): McpServerDraft {
  return {
    displayName: value.displayName,
    transport: value.transport,
    command: value.command,
    args: [...value.args],
    cwd: value.cwd,
    url: value.url,
    env: { ...value.env },
    headers: { ...value.headers },
    secretEnvKeys: [...value.secretEnvKeys],
    secretHeaderKeys: [...value.secretHeaderKeys],
    connectTimeoutMs: value.connectTimeoutMs,
    requestTimeoutMs: value.requestTimeoutMs,
  };
}

function entryFromPreview(value: McpImportPreview, index: number): EditorEntry {
  return {
    key: `${index}:${value.displayName}`,
    sourceName: value.displayName,
    sourceValidated: false,
    draft: publicDraft(value),
    secretPatch: {
      env: { ...value.secretPatch.env },
      headers: { ...value.secretPatch.headers },
    },
    risks: [],
    acceptedRiskCodes: [],
    selected: true,
    result: null,
    decisions: [],
    error: "",
    lineInputs: {
      env: mapLines(value.env),
      secretEnv: mapLines(value.secretPatch.env),
      headers: mapLines(value.headers),
      secretHeaders: mapLines(value.secretPatch.headers),
    },
  };
}

function entryFromServer(server: McpServerRecord): EditorEntry {
  return {
    key: server.id,
    sourceName: null,
    sourceValidated: true,
    draft: publicDraft(server),
    secretPatch: { env: {}, headers: {} },
    risks: server.riskFindings.map((code) => ({
      code,
      severity: server.configReviewState === "blocked" || BLOCK_RISK_CODES.has(code)
        ? "block" as const
        : "warn" as const,
    })),
    acceptedRiskCodes: server.configReviewState === "reviewed" ? [...server.riskFindings] : [],
    selected: true,
    result: null,
    decisions: [],
    error: "",
    lineInputs: {
      env: mapLines(server.env),
      secretEnv: "",
      headers: mapLines(server.headers),
      secretHeaders: "",
    },
  };
}

function manualEntry(): EditorEntry {
  return {
    key: "manual",
    sourceName: null,
    sourceValidated: false,
    draft: { ...EMPTY_DRAFT, args: [], env: {}, headers: {} },
    secretPatch: { env: {}, headers: {} },
    risks: [],
    acceptedRiskCodes: [],
    selected: true,
    result: null,
    decisions: [],
    error: "",
    lineInputs: { env: "", secretEnv: "", headers: "", secretHeaders: "" },
  };
}

function codeFromError(error: unknown): unknown {
  return error instanceof RuntimeApiError ? error.code : undefined;
}

function decisionsFromResult(result: McpTestResult): McpToolDecision[] {
  return result.tools.map((tool) => ({
    toolName: tool.toolName,
    enabled: true,
    expectedDescriptionHash: tool.descriptionHash,
    expectedSchemaHash: tool.schemaHash,
  }));
}

function parseMapLines(text: string, secret: boolean, caseInsensitive = false): {
  values: Record<string, string | null>;
  error: string;
} {
  const result: Record<string, string | null> = {};
  if (new TextEncoder().encode(text).byteLength > MAX_FIELD_BYTES
    || Array.from(text).some((character) => {
      const code = character.codePointAt(0) ?? 0;
      return (code < 32 && character !== "\n") || code === 127;
    })) {
    return { values: result, error: "连接信息过长或包含不可用字符。" };
  }
  const seenKeys = new Set<string>();
  for (const rawLine of text.split("\n")) {
    if (!rawLine.trim()) continue;
    const separator = rawLine.indexOf("=");
    const key = separator >= 0 ? rawLine.slice(0, separator).trim() : "";
    const comparableKey = key.toLocaleLowerCase("en-US");
    const duplicateKey = caseInsensitive ? comparableKey : key;
    if (separator <= 0
      || !key
      || ["__proto__", "constructor", "prototype"].includes(comparableKey)
      || seenKeys.has(duplicateKey)) {
      return { values: result, error: "每行请使用 KEY=值，字段名不能留空或重复。" };
    }
    seenKeys.add(duplicateKey);
    const value = rawLine.slice(separator + 1);
    result[key] = secret && value === "" ? null : value;
  }
  return { values: result, error: "" };
}

function mapLines(values: Record<string, string | null> | null | undefined): string {
  return Object.entries(values ?? {})
    .map(([key, value]) => `${key}=${value ?? ""}`)
    .join("\n");
}

function riskCopy(code: string): string {
  switch (code) {
    case "mcp_insecure_http":
      return "数据将通过未加密的网络连接发送。";
    default:
      return "这项配置需要你确认后才能继续。";
  }
}

function blockRiskCopy(code: string): string {
  switch (code) {
    case "mcp_config_invalid":
      return "配置格式或字段不符合要求，请检查并修正连接信息。";
    case "mcp_secret_in_arguments":
      return "命令参数中可能包含秘密，请移除后改用“秘密环境变量”保存。";
    case "mcp_url_credentials":
      return "网络地址中包含账号或密码，请从地址中移除。";
    case "mcp_secret_in_url":
      return "网络地址中可能包含令牌等秘密，请移除后改用“秘密请求头”保存。";
    default:
      return "配置中有不能安全使用的内容，请检查连接信息。";
  }
}

function recipient(url: string | null): string {
  if (!url) return "网络服务器";
  try {
    return new URL(url).origin;
  } catch {
    return "网络服务器";
  }
}

function sparseConnectionPatch(
  current: McpServerDraft,
  next: McpServerDraft,
  includeDisplayName: boolean,
): McpServerPatch {
  const patch: McpServerPatch = {};
  const keys = [
    "transport",
    "command",
    "args",
    "cwd",
    "url",
    "env",
    "headers",
    "secretEnvKeys",
    "secretHeaderKeys",
    "connectTimeoutMs",
    "requestTimeoutMs",
  ] as const;
  if (includeDisplayName && current.displayName !== next.displayName) {
    patch.displayName = next.displayName;
  }
  for (const key of keys) {
    if (JSON.stringify(current[key]) !== JSON.stringify(next[key])) {
      Object.assign(patch, { [key]: next[key] });
    }
  }
  return patch;
}

function connectionSnapshot(draft: McpServerDraft): string {
  return JSON.stringify({
    transport: draft.transport,
    command: draft.command,
    args: draft.args,
    cwd: draft.cwd,
    url: draft.url,
    env: draft.env,
    headers: draft.headers,
    secretEnvKeys: draft.secretEnvKeys,
    secretHeaderKeys: draft.secretHeaderKeys,
    connectTimeoutMs: draft.connectTimeoutMs,
    requestTimeoutMs: draft.requestTimeoutMs,
  });
}

function hasSecretChanges(patch: McpSecretPatch): boolean {
  return Object.keys(patch.env ?? {}).length > 0 || Object.keys(patch.headers ?? {}).length > 0;
}

function scanSource(entries: EditorEntry[]): string {
  const mcpServers = Object.fromEntries(entries.map((entry) => {
    const secretEnv = Object.fromEntries(entry.draft.secretEnvKeys.map((key) => [
      key,
      entry.secretPatch.env?.[key] ?? `\${${key}}`,
    ]).filter(([, value]) => value !== null));
    const secretHeaders = Object.fromEntries(entry.draft.secretHeaderKeys.map((key) => [
      key,
      entry.secretPatch.headers?.[key] ?? `\${${key}}`,
    ]).filter(([, value]) => value !== null));
    return [entry.draft.displayName, {
      type: entry.draft.transport,
      command: entry.draft.command,
      args: entry.draft.args,
      cwd: entry.draft.cwd,
      url: entry.draft.url,
      env: { ...entry.draft.env, ...secretEnv },
      headers: { ...entry.draft.headers, ...secretHeaders },
      connectTimeoutMs: entry.draft.connectTimeoutMs,
      requestTimeoutMs: entry.draft.requestTimeoutMs,
    }];
  }));
  return JSON.stringify({ mcpServers });
}

function mergeAuthoritativePreview(
  authoritative: McpParsedServerDraft,
  prior: EditorEntry,
  preview: McpImportPreview,
): EditorEntry | null {
  if (authoritative.displayName !== preview.displayName
    || prior.sourceName !== preview.displayName
    || JSON.stringify([...authoritative.secretEnvKeys].sort())
      !== JSON.stringify([...preview.secretEnvKeys].sort())
    || JSON.stringify([...authoritative.secretHeaderKeys].map((name) =>
      name.toLocaleLowerCase("en-US")).sort())
      !== JSON.stringify([...preview.secretHeaderKeys].map((name) =>
        name.toLocaleLowerCase("en-US")).sort())) {
    return null;
  }
  const fields = [
    "displayName",
    "transport",
    "command",
    "args",
    "cwd",
    "url",
    "env",
    "headers",
    "secretEnvKeys",
    "secretHeaderKeys",
    "connectTimeoutMs",
    "requestTimeoutMs",
  ] as const;
  const draft = publicDraft(authoritative);
  for (const field of fields) {
    if (JSON.stringify(prior.draft[field]) !== JSON.stringify(preview[field])) {
      Object.assign(draft, { [field]: prior.draft[field] });
    }
  }
  return {
    ...prior,
    draft,
    risks: [...authoritative.risks],
    sourceValidated: true,
    result: null,
    decisions: [],
    error: "",
  };
}

function entriesFromAuthoritativeSource(
  parsed: McpParsedServerDraft[],
  current: EditorEntry[],
  previews: McpImportPreview[],
): EditorEntry[] | null {
  if (previews.length === 0) {
    if (parsed.some((draft) => draft.secretEnvKeys.length > 0 || draft.secretHeaderKeys.length > 0)) {
      return null;
    }
    return parsed.map((draft, index) => ({
      key: `runtime-${index}:${draft.displayName}`,
      sourceName: null,
      sourceValidated: true,
      draft: publicDraft(draft),
      secretPatch: { env: {}, headers: {} },
      risks: [...draft.risks],
      acceptedRiskCodes: [],
      selected: true,
      result: null,
      decisions: [],
      error: "",
      lineInputs: {
        env: mapLines(draft.env),
        secretEnv: "",
        headers: mapLines(draft.headers),
        secretHeaders: "",
      },
    }));
  }
  if (parsed.length !== previews.length || current.length !== previews.length) return null;
  const parsedByName = new Map<string, McpParsedServerDraft>();
  for (const draft of parsed) {
    if (parsedByName.has(draft.displayName)) return null;
    parsedByName.set(draft.displayName, draft);
  }
  const merged: EditorEntry[] = [];
  for (let index = 0; index < previews.length; index += 1) {
    const preview = previews[index]!;
    const prior = current[index]!;
    if (prior.key !== `${index}:${preview.displayName}`) return null;
    const authoritative = parsedByName.get(preview.displayName);
    if (!authoritative) return null;
    const entry = mergeAuthoritativePreview(authoritative, prior, preview);
    if (!entry) return null;
    merged.push(entry);
  }
  return merged;
}

export function McpServerEditor({
  mode,
  server,
  initialDraft,
  credential,
  onSaved,
  onAtomicSelected,
  onCancel,
  onComplete,
  onBusyChange,
}: McpServerEditorProps) {
  const [source, setSource] = useState("");
  const [manual, setManual] = useState(false);
  const [entries, setEntries] = useState<EditorEntry[]>(() => server ? [entryFromServer(server)] : initialDraft ? [{
    ...manualEntry(), sourceValidated: true, draft: publicDraft(initialDraft),
    lineInputs: { env: mapLines(initialDraft.env), headers: mapLines(initialDraft.headers), secretEnv: "", secretHeaders: "" },
  }] : []);
  const [busy, setBusy] = useState<"testing" | "saving" | "">("");
  useEffect(() => { onBusyChange?.(busy !== ""); }, [busy, onBusyChange]);
  const [status, setStatus] = useState("");
  const [globalError, setGlobalError] = useState("");
  const [sourceParsed, setSourceParsed] = useState(Boolean(server || initialDraft));
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  useEffect(() => () => {
    generationRef.current += 1;
    controllerRef.current?.abort();
  }, []);

  const selected = entries.filter((entry) => entry.selected);
  const renameOnly = Boolean(server && selected.length === 1
    && selected[0]!.draft.displayName !== server.displayName
    && connectionSnapshot(selected[0]!.draft) === connectionSnapshot(publicDraft(server))
    && !hasSecretChanges(selected[0]!.secretPatch));
  const baseCanSave = selected.length > 0
    && (server
      ? renameOnly || selected.every((entry) => entry.result !== null
        && (connectionSnapshot(entry.draft) !== connectionSnapshot(publicDraft(server))
          || hasSecretChanges(entry.secretPatch)))
      : selected.every((entry) => entry.result !== null))
    && busy === "";

  const validationFor = (entry: EditorEntry): string => {
    if (credential && !(entry.secretPatch[credential.scope]?.[credential.key] ?? "").slice(credential.prefix.length).trim()) return `请填写${credential.label}。`;
    if (!entry.draft.displayName.trim()) return "请输入名称。";
    if (entry.selected && entries.some((other) => other.selected && other.key !== entry.key
      && other.draft.displayName.trim().toLocaleLowerCase("zh-CN")
        === entry.draft.displayName.trim().toLocaleLowerCase("zh-CN"))) {
      return "服务器名称不能重复。";
    }
    if (!validFieldText(entry.draft.displayName, 256)) {
      return "连接信息过长或包含不可用字符。";
    }
    const fieldValues = [
      entry.draft.command ?? "",
      entry.draft.cwd ?? "",
      entry.draft.url ?? "",
      ...entry.draft.args,
      ...Object.entries(entry.draft.env).flat(),
      ...Object.entries(entry.draft.headers).flat(),
      ...entry.draft.secretEnvKeys,
      ...entry.draft.secretHeaderKeys,
      ...Object.entries(entry.secretPatch.env ?? {}).flatMap(([key, value]) => [key, value ?? ""]),
      ...Object.entries(entry.secretPatch.headers ?? {}).flatMap(([key, value]) => [key, value ?? ""]),
    ];
    if (fieldValues.some((value) => !validFieldText(value))) {
      return "连接信息过长或包含不可用字符。";
    }
    const lineInputError = [
      parseMapLines(entry.lineInputs.env, false).error,
      parseMapLines(entry.lineInputs.secretEnv, true).error,
      parseMapLines(entry.lineInputs.headers, false, true).error,
      parseMapLines(entry.lineInputs.secretHeaders, true, true).error,
    ].find(Boolean);
    if (lineInputError) return lineInputError;
    const ordinaryHeaderNames = new Set(Object.keys(entry.draft.headers)
      .map((name) => name.toLocaleLowerCase("en-US")));
    if (entry.draft.secretHeaderKeys.some((name) =>
      ordinaryHeaderNames.has(name.toLocaleLowerCase("en-US")))) {
      return "普通请求头和秘密请求头的字段名不能重复。";
    }
    if (entry.draft.transport === "stdio" && !entry.draft.command?.trim()) {
      return "请输入要启动的命令。";
    }
    if (entry.draft.transport !== "stdio" && !safeMcpUrl(entry.draft.url ?? "")) {
      return "请输入有效且不含凭据的 http 或 https 地址。";
    }
    if (!Number.isInteger(entry.draft.connectTimeoutMs)
      || entry.draft.connectTimeoutMs < 1_000
      || entry.draft.connectTimeoutMs > 120_000) {
      return "连接超时应为 1000 到 120000 毫秒的整数。";
    }
    if (!Number.isInteger(entry.draft.requestTimeoutMs)
      || entry.draft.requestTimeoutMs < 1_000
      || entry.draft.requestTimeoutMs > 600_000) {
      return "请求超时应为 1000 到 600000 毫秒的整数。";
    }
    return "";
  };
  const canSave = baseCanSave && selected.every((entry) => validationFor(entry) === "");
  const canAttemptInitialParse = !server
    && !sourceParsed
    && source.trim() !== ""
    && previewMcpImport(source).length === 0;
  const selectedCanTest = selected.length > 0
    && selected.every((entry) => validationFor(entry) === "")
    && selected.some((entry) =>
      !entry.sourceValidated || !entry.risks.some((risk) => risk.severity === "block"));

  const abortWork = () => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy("");
  };

  const changeSource = (value: string) => {
    if (busy) return;
    abortWork();
    setSource(value);
    setSourceParsed(false);
    setStatus("");
    setGlobalError("");
    const nextPreviews = value ? previewMcpImport(value) : [];
    if (nextPreviews.length > 0) {
      setManual(false);
      setEntries(nextPreviews.map(entryFromPreview));
    } else if (value.trim()) {
      setManual(true);
      setEntries([manualEntry()]);
    } else {
      setManual(false);
      setEntries([]);
    }
  };

  const dropFile = async (files: FileList | File[]) => {
    if (busy) return;
    abortWork();
    const generation = generationRef.current;
    setGlobalError("");
    const file = files.length === 1 ? files[0] : undefined;
    if (!file || file.size <= 0 || file.size > MAX_IMPORT_BYTES
      || (!file.name.toLocaleLowerCase("en-US").endsWith(".json")
        && file.type !== "application/json")) {
      setGlobalError("请选择一个不超过 256 KiB 的 JSON 文件。");
      return;
    }
    try {
      const bytes = await file.arrayBuffer();
      if (generation !== generationRef.current) return;
      if (bytes.byteLength <= 0 || bytes.byteLength > MAX_IMPORT_BYTES) {
        setGlobalError("请选择一个不超过 256 KiB 的 JSON 文件。");
        return;
      }
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      changeSource(text);
    } catch {
      if (generation === generationRef.current) {
        setGlobalError("无法读取这个 JSON 文件，请检查文件后重试。");
      }
    }
  };

  const invalidateEntry = (
    key: string,
    update: (entry: EditorEntry) => EditorEntry,
    preserveAcceptedRisks = false,
  ) => {
    if (busy) return;
    abortWork();
    setStatus("");
    setEntries((current) => current.map((entry) => {
      if (entry.key !== key) return entry;
      const updated = update(entry);
      return {
        ...updated,
        acceptedRiskCodes: preserveAcceptedRisks ? updated.acceptedRiskCodes : [],
        risks: preserveAcceptedRisks ? updated.risks : [],
        result: null,
        decisions: [],
        error: "",
      };
    }));
  };

  const updateDraft = (key: string, patch: Partial<McpServerDraft>) => {
    invalidateEntry(key, (entry) => ({
      ...entry,
      draft: { ...entry.draft, ...patch },
    }));
  };

  const switchTransport = (key: string, transport: McpTransport) => {
    invalidateEntry(key, (entry) => {
      if (transport === entry.draft.transport) return entry;
      if (transport === "stdio") {
        return {
          ...entry,
          draft: {
            ...entry.draft,
            transport,
            command: "",
            args: [],
            cwd: null,
            url: null,
            headers: {},
            secretHeaderKeys: [],
          },
          secretPatch: { ...entry.secretPatch, headers: {} },
          lineInputs: { ...entry.lineInputs, headers: "", secretHeaders: "" },
        };
      }
      if (entry.draft.transport === "stdio") {
        return {
          ...entry,
          draft: {
            ...entry.draft,
            transport,
            command: null,
            args: [],
            cwd: null,
            url: "",
            env: {},
            secretEnvKeys: [],
          },
          secretPatch: { ...entry.secretPatch, env: {} },
          lineInputs: { ...entry.lineInputs, env: "", secretEnv: "" },
        };
      }
      return { ...entry, draft: { ...entry.draft, transport } };
    });
  };

  const updateLineInput = (
    key: string,
    field: "env" | "secretEnv" | "headers" | "secretHeaders",
    text: string,
  ) => {
    invalidateEntry(key, (entry) => {
      const secret = field === "secretEnv" || field === "secretHeaders";
      const header = field === "headers" || field === "secretHeaders";
      const parsed = parseMapLines(text, secret, header);
      const withText = {
        ...entry,
        lineInputs: { ...entry.lineInputs, [field]: text },
      };
      if (parsed.error) return withText;
      if (!secret) {
        const values = Object.fromEntries(Object.entries(parsed.values)
          .map(([name, value]) => [name, value ?? ""]));
        return {
          ...withText,
          draft: {
            ...entry.draft,
            ...(field === "env" ? { env: values } : { headers: values }),
          },
        };
      }
      const scope = field === "secretEnv" ? "env" : "headers";
      const oldKeys = server
        ? (scope === "env" ? server.secretEnvKeys : server.secretHeaderKeys)
        : [];
      const canonicalKeys = scope === "headers"
        ? new Map(oldKeys.map((name) => [name.toLocaleLowerCase("en-US"), name]))
        : new Map<string, string>();
      const normalizedValues = Object.fromEntries(Object.entries(parsed.values).map(([name, value]) => [
        scope === "headers"
          ? (canonicalKeys.get(name.toLocaleLowerCase("en-US")) ?? name)
          : name,
        value,
      ]));
      const keys = server ? new Set(oldKeys) : new Set<string>();
      for (const [name, value] of Object.entries(normalizedValues)) {
        if (value === null) keys.delete(name);
        else keys.add(name);
      }
      return {
        ...withText,
        draft: {
          ...entry.draft,
          ...(scope === "env"
            ? { secretEnvKeys: Array.from(keys).sort() }
            : { secretHeaderKeys: Array.from(keys).sort() }),
        },
        secretPatch: { ...entry.secretPatch, [scope]: normalizedValues },
      };
    });
  };

  const toggleRisk = (key: string, code: string, accepted: boolean) => {
    invalidateEntry(key, (entry) => ({
      ...entry,
      acceptedRiskCodes: accepted
        ? Array.from(new Set([...entry.acceptedRiskCodes, code])).sort()
        : entry.acceptedRiskCodes.filter((value) => value !== code),
    }), true);
  };

  const testEntries = async () => {
    abortWork();
    const generation = generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy("testing");
    setGlobalError("");
    setStatus("");

    let candidates = entries;
    try {
      if (!server && !sourceParsed) {
        const previews = previewMcpImport(source);
        if (previews.length > 0) {
          const selectedEntries = candidates.filter((entry) => entry.selected);
          const pendingEntries = selectedEntries.filter((entry) => entry.result === null);
          const rawTargets = pendingEntries.length > 0 ? pendingEntries : selectedEntries;
          const next = [...candidates];
          for (const target of rawTargets.filter((entry) => !entry.sourceValidated)) {
            const index = next.findIndex((entry) => entry.key === target.key);
            if (index < 0 || !target.sourceName) continue;
            try {
              const parsedSource = await parseMcpImport(
                source,
                controller.signal,
                [target.sourceName],
              );
              if (controller.signal.aborted || generation !== generationRef.current) return;
              const preview = previews.find((item) => item.displayName === target.sourceName);
              const authoritative = parsedSource.drafts.length === 1 && preview
                ? mergeAuthoritativePreview(parsedSource.drafts[0]!, target, preview)
                : null;
              next[index] = authoritative ?? {
                ...target,
                error: mcpErrorCopy("mcp_config_invalid"),
              };
            } catch (error) {
              if (controller.signal.aborted || generation !== generationRef.current) return;
              next[index] = { ...target, error: mcpErrorCopy(codeFromError(error)) };
            }
          }
          candidates = next;
          setEntries(candidates);
        } else {
          let parsedSource: { drafts: McpParsedServerDraft[] } | undefined;
          try {
            parsedSource = await parseMcpImport(source, controller.signal);
          } catch (error) {
            if (controller.signal.aborted || generation !== generationRef.current) return;
            if (!(error instanceof RuntimeApiError) || error.code !== "mcp_config_invalid") {
              setGlobalError(mcpErrorCopy(codeFromError(error)));
              return;
            }
            setSourceParsed(true);
            setManual(true);
            candidates = (entries.length > 0 ? entries : [manualEntry()]).map((entry) => ({
              ...entry,
              sourceValidated: true,
            }));
            setEntries(candidates);
            if (candidates.filter((entry) => entry.selected).some((entry) => validationFor(entry))) {
              return;
            }
          }
          if (parsedSource) {
            if (controller.signal.aborted || generation !== generationRef.current) return;
            const authoritative = entriesFromAuthoritativeSource(
              parsedSource.drafts,
              entries,
              previews,
            );
            if (!authoritative) {
              setGlobalError(mcpErrorCopy("mcp_config_invalid"));
              return;
            }
            candidates = authoritative;
            setSourceParsed(true);
            setManual(false);
            setEntries(candidates);
          }
        }
      }

      const selectedCandidatesBeforeScan = candidates.filter((entry) => entry.selected);
      const pendingCandidates = selectedCandidatesBeforeScan.filter((entry) => entry.result === null);
      const entriesToTest = (pendingCandidates.length > 0
        ? pendingCandidates
        : selectedCandidatesBeforeScan).filter((entry) => entry.sourceValidated);
      if (entriesToTest.length === 0) return;
      const scannedDrafts = new Map<string, McpParsedServerDraft>();
      const placementErrors = new Map<string, string>();
      for (const entry of entriesToTest) {
        try {
          const parsed = await parseMcpImport(scanSource([entry]), controller.signal);
          if (controller.signal.aborted || generation !== generationRef.current) return;
          const parsedDraft = parsed.drafts.length === 1
            && parsed.drafts[0]?.displayName === entry.draft.displayName
            ? parsed.drafts[0]
            : null;
          if (!parsedDraft) {
            placementErrors.set(entry.key, mcpErrorCopy("mcp_config_invalid"));
            continue;
          }
          scannedDrafts.set(entry.key, parsedDraft);
        } catch (error) {
          if (controller.signal.aborted || generation !== generationRef.current) return;
          placementErrors.set(entry.key, mcpErrorCopy(codeFromError(error)));
          continue;
        }
        const parsedDraft = scannedDrafts.get(entry.key)!;
        const currentEnv = new Set(entry.draft.secretEnvKeys);
        const currentHeaders = new Set(entry.draft.secretHeaderKeys
          .map((name) => name.toLocaleLowerCase("en-US")));
        const misplacedEnv = parsedDraft.secretEnvKeys.filter((name) => !currentEnv.has(name));
        const misplacedHeaders = parsedDraft.secretHeaderKeys.filter((name) =>
          !currentHeaders.has(name.toLocaleLowerCase("en-US")));
        const instructions = [
          misplacedEnv.length > 0
            ? `请把 ${misplacedEnv.join("、")} 移到秘密环境变量。`
            : "",
          misplacedHeaders.length > 0
            ? `请把 ${misplacedHeaders.join("、")} 移到秘密请求头。`
            : "",
        ].filter(Boolean);
        if (instructions.length > 0) placementErrors.set(entry.key, instructions.join(" "));
      }
      candidates = candidates.map((entry) => {
        if (placementErrors.has(entry.key)) return {
          ...entry,
          risks: [],
          result: null,
          decisions: [],
          error: placementErrors.get(entry.key)!,
        };
        return scannedDrafts.has(entry.key) ? {
          ...entry,
          risks: [...scannedDrafts.get(entry.key)!.risks],
          result: null,
          decisions: [],
          error: "",
        } : entry;
      });
      setEntries(candidates);

      const testedKeys = new Set(entriesToTest.map((entry) => entry.key));
      const selectedCandidates = candidates.filter((entry) =>
        testedKeys.has(entry.key)
        && !placementErrors.has(entry.key)
        && !entry.risks.some((risk) => risk.severity === "block")
        && !entry.risks.some((risk) => risk.severity === "warn"
          && !entry.acceptedRiskCodes.includes(risk.code)));
      if (selectedCandidates.length === 0) return;

      const next = [...candidates];
      for (const candidate of selectedCandidates) {
        const index = next.findIndex((entry) => entry.key === candidate.key);
        if (index < 0) continue;
        try {
          const changedConnection = server
            ? connectionSnapshot(candidate.draft) !== connectionSnapshot(publicDraft(server))
              || hasSecretChanges(candidate.secretPatch)
            : true;
          const result = server
            ? await testSavedMcpServer(server.id, {
              expectedRevision: server.configRevision,
              patch: changedConnection
                ? sparseConnectionPatch(publicDraft(server), candidate.draft, false)
                : {},
              secretPatch: candidate.secretPatch,
              acceptedRiskCodes: candidate.acceptedRiskCodes,
            }, controller.signal)
            : await testMcpServerDraft({
              draft: candidate.draft,
              secretPatch: candidate.secretPatch,
              acceptedRiskCodes: candidate.acceptedRiskCodes,
            }, controller.signal);
          if (controller.signal.aborted || generation !== generationRef.current) return;
          next[index] = {
            ...candidate,
            result,
            decisions: decisionsFromResult(result),
            error: "",
          };
        } catch (error) {
          if (controller.signal.aborted || generation !== generationRef.current) return;
          next[index] = { ...candidate, error: mcpErrorCopy(codeFromError(error)) };
        }
        setEntries([...next]);
      }
    } catch (error) {
      if (!controller.signal.aborted && generation === generationRef.current) {
        setGlobalError(mcpErrorCopy(codeFromError(error)));
      }
    } finally {
      if (!controller.signal.aborted && generation === generationRef.current) {
        setBusy("");
      }
    }
  };

  const saveEntries = async () => {
    if (!canSave) return;
    abortWork();
    const generation = generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy("saving");
    setGlobalError("");
    setStatus("");
    const succeeded = new Set<string>();

    for (const entry of selected) {
      const isRenameOnly = Boolean(server
        && entry.draft.displayName !== server.displayName
        && connectionSnapshot(entry.draft) === connectionSnapshot(publicDraft(server))
        && !hasSecretChanges(entry.secretPatch));
      if (!entry.result && !isRenameOnly) continue;
      const input: CreateMcpServerInput = {
        draft: entry.draft,
        secretPatch: entry.secretPatch,
        acceptedRiskCodes: entry.acceptedRiskCodes,
        testReceipt: entry.result?.testReceipt ?? "",
        toolDecisions: entry.decisions,
      };
      try {
        if (mode === "thread" && onAtomicSelected) {
          await onAtomicSelected(input);
        } else if (server) {
          const saved = await updateMcpServer(server.id, isRenameOnly ? {
            expectedRevision: server.configRevision,
            patch: { displayName: entry.draft.displayName },
            secretPatch: {},
            acceptedRiskCodes: [],
            toolDecisions: [],
          } : {
            expectedRevision: server.configRevision,
            patch: sparseConnectionPatch(publicDraft(server), entry.draft, true),
            secretPatch: entry.secretPatch,
            acceptedRiskCodes: entry.acceptedRiskCodes,
            testReceipt: entry.result?.testReceipt,
            toolDecisions: entry.decisions,
          }, controller.signal);
          if (controller.signal.aborted || generation !== generationRef.current) return;
          onSaved?.(saved);
        } else {
          const saved = await createMcpServer(input, controller.signal);
          if (controller.signal.aborted || generation !== generationRef.current) return;
          onSaved?.(saved);
        }
        if (controller.signal.aborted || generation !== generationRef.current) return;
        succeeded.add(entry.key);
      } catch (error) {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const copy = mcpErrorCopy(codeFromError(error));
        setEntries((current) => current.map((item) => item.key === entry.key
          ? { ...item, error: copy }
          : item));
      }
    }

    if (!controller.signal.aborted && generation === generationRef.current) {
      if (entries.length > 1 && succeeded.size > 0) {
        setEntries((current) => current.filter((entry) => !succeeded.has(entry.key)));
      }
      if (succeeded.size > 0) setStatus("已保存");
      setBusy("");
      if (succeeded.size === selected.length) onComplete?.();
    }
  };

  return (
    <div className="mcp-editor" aria-label={server ? `编辑 ${server.displayName}` : "添加 MCP 服务器"}>
      <fieldset className="mcp-editor-fields" disabled={busy !== ""}>
      {!server && !initialDraft && (
        <label className="mcp-editor-import">
          <span>粘贴 MCP 配置</span>
          <textarea
            value={source}
            disabled={busy !== ""}
            onChange={(event) => changeSource(event.target.value)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              void dropFile(event.dataTransfer.files);
            }}
            placeholder="粘贴配置、命令或网址，也可拖入 JSON 文件"
            rows={5}
          />
        </label>
      )}

      {manual && <p className="mcp-inline-copy">没有识别成功，请填写连接信息。</p>}
      {globalError && <p className="mcp-error" role="alert">{globalError}</p>}
      {status && <p className="mcp-save-status" role="status">{status}</p>}

      <div className="mcp-editor-entries">
        {entries.map((entry) => {
          const blocked = entry.risks.some((risk) => risk.severity === "block");
          return (
            <section className="mcp-editor-entry" key={entry.key}>
              {entries.length > 1 && (
                <label className="mcp-entry-choice">
                  <input
                    type="checkbox"
                    checked={entry.selected}
                    onChange={(event) => setEntries((current) => current.map((item) =>
                      item.key === entry.key ? { ...item, selected: event.target.checked } : item))}
                  />
                  {`保存 ${entry.draft.displayName}`}
                </label>
              )}

              {credential && <label className="mcp-catalog-credential">
                <span>{credential.label}</span>
                <input type="password" autoComplete="off" spellCheck={false} maxLength={4096}
                  value={(entry.secretPatch[credential.scope]?.[credential.key] ?? "").slice(credential.prefix.length)}
                  onChange={event => updateLineInput(entry.key, credential.scope === "env" ? "secretEnv" : "secretHeaders",
                    `${credential.key}=${event.target.value ? credential.prefix + event.target.value : ""}`)} />
              </label>}

              {!initialDraft && <div className="mcp-editor-grid">
                <label>
                  <span>名称</span>
                  <input
                    value={entry.draft.displayName}
                    onChange={(event) => setEntries((current) => current.map((item) =>
                      item.key === entry.key
                        ? { ...item, draft: { ...item.draft, displayName: event.target.value } }
                        : item))}
                  />
                </label>
                {manual && (
                  <label>
                    <span>传输方式</span>
                    <select
                      aria-label="传输方式"
                      value={entry.draft.transport}
                      onChange={(event) => switchTransport(
                        entry.key,
                        event.target.value as McpTransport,
                      )}
                    >
                      <option value="stdio">本机命令</option>
                      <option value="http">网络地址</option>
                      <option value="sse">网络地址（SSE）</option>
                    </select>
                  </label>
                )}
                {manual && entry.draft.transport === "stdio" && (
                  <label>
                    <span>命令</span>
                    <input
                      value={entry.draft.command ?? ""}
                      onChange={(event) => updateDraft(entry.key, { command: event.target.value })}
                    />
                  </label>
                )}
                {manual && entry.draft.transport !== "stdio" && (
                  <label>
                    <span>网络地址</span>
                    <input
                      value={entry.draft.url ?? ""}
                      onChange={(event) => updateDraft(entry.key, { url: event.target.value })}
                    />
                  </label>
                )}
              </div>}

              {validationFor(entry) && <p className="mcp-field-error">{validationFor(entry)}</p>}

              {entry.draft.transport === "stdio" ? (
                <p className="mcp-consequence">会在本机启动命令。请只添加你信任的服务器。</p>
              ) : (
                <p className="mcp-consequence">{`使用时，数据会发送到 ${recipient(entry.draft.url)}。`}</p>
              )}
              {(entry.draft.secretEnvKeys.length + entry.draft.secretHeaderKeys.length) > 0 && (
                <p className="mcp-inline-copy">
                  {`秘密字段 ${[...entry.draft.secretEnvKeys, ...entry.draft.secretHeaderKeys].join("、")} 只会保存在 VeriLex 的本地私有配置中。`}
                </p>
              )}

              {blocked && (
                <div className="mcp-block-reasons" role="alert">
                  <p className="mcp-error">这份配置存在不安全内容，不能添加。</p>
                  <ul>
                    {entry.risks.filter((risk) => risk.severity === "block").map((risk) => (
                      <li key={risk.code}>{blockRiskCopy(risk.code)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {entry.risks.filter((risk) => risk.severity === "warn").map((risk) => (
                <div className="mcp-risk" key={risk.code}>
                  <p>{riskCopy(risk.code)}</p>
                  <label>
                    <input
                      type="checkbox"
                      checked={entry.acceptedRiskCodes.includes(risk.code)}
                      onChange={(event) => toggleRisk(entry.key, risk.code, event.target.checked)}
                    />
                    我了解并继续
                  </label>
                </div>
              ))}

              <details className="mcp-advanced">
                <summary>高级选项</summary>
                <div className="mcp-editor-grid">
                  {entry.draft.transport === "stdio" ? (
                    <>
                      {!manual && (
                        <label>
                          <span>命令</span>
                          <input
                            value={entry.draft.command ?? ""}
                            onChange={(event) => updateDraft(entry.key, { command: event.target.value })}
                          />
                        </label>
                      )}
                      <label>
                        <span>参数（每行一个）</span>
                        <textarea
                          value={entry.draft.args.join("\n")}
                          onChange={(event) => updateDraft(entry.key, {
                            args: event.target.value.split("\n").filter(Boolean),
                          })}
                        />
                      </label>
                      <label>
                        <span>工作目录</span>
                        <input
                          value={entry.draft.cwd ?? ""}
                          onChange={(event) => updateDraft(entry.key, { cwd: event.target.value || null })}
                        />
                      </label>
                    </>
                  ) : !manual ? (
                    <label>
                      <span>网络地址</span>
                      <input
                        value={entry.draft.url ?? ""}
                        onChange={(event) => updateDraft(entry.key, { url: event.target.value })}
                      />
                    </label>
                  ) : null}
                  <label>
                    <span>连接超时</span>
                    <input
                      type="number"
                      value={entry.draft.connectTimeoutMs}
                      onChange={(event) => updateDraft(entry.key, {
                        connectTimeoutMs: Number(event.target.value),
                      })}
                    />
                  </label>
                  <label>
                    <span>请求超时</span>
                    <input
                      type="number"
                      value={entry.draft.requestTimeoutMs}
                      onChange={(event) => updateDraft(entry.key, {
                        requestTimeoutMs: Number(event.target.value),
                      })}
                    />
                  </label>
                  {entry.draft.transport === "stdio" ? (
                    <>
                      <label>
                        <span>普通环境变量</span>
                        <textarea
                          value={entry.lineInputs.env}
                          onChange={(event) => updateLineInput(entry.key, "env", event.target.value)}
                        />
                      </label>
                      <label>
                        <span>秘密环境变量</span>
                        <textarea
                          disabled={!!credential} value={credential ? "" : entry.lineInputs.secretEnv}
                          placeholder={server ? "留空表示保持不变" : "KEY=值"}
                          onChange={(event) => updateLineInput(entry.key, "secretEnv", event.target.value)}
                        />
                      </label>
                    </>
                  ) : (
                    <>
                      <label>
                        <span>普通请求头</span>
                        <textarea
                          value={entry.lineInputs.headers}
                          onChange={(event) => updateLineInput(entry.key, "headers", event.target.value)}
                        />
                      </label>
                      <label>
                        <span>秘密请求头</span>
                        <textarea
                          disabled={!!credential} value={credential ? "" : entry.lineInputs.secretHeaders}
                          placeholder={server ? "留空表示保持不变" : "Authorization=值"}
                          onChange={(event) => updateLineInput(entry.key, "secretHeaders", event.target.value)}
                        />
                      </label>
                    </>
                  )}
                </div>
              </details>

              {entry.result && (
                <div className="mcp-tool-preview">
                  <p>{`找到 ${entry.result.tools.length} 个工具`}</p>
                  <ul aria-label={`${entry.draft.displayName} 的待保存工具`}>
                    {entry.result.tools.map((tool, index) => (
                      <li key={tool.toolName}>
                        <label>
                          <input
                            type="checkbox"
                            checked={entry.decisions[index]?.enabled ?? false}
                            onChange={(event) => setEntries((current) => current.map((item) =>
                              item.key === entry.key
                                ? {
                                  ...item,
                                  decisions: item.decisions.map((decision, decisionIndex) =>
                                    decisionIndex === index
                                      ? { ...decision, enabled: event.target.checked }
                                      : decision),
                                }
                                : item))}
                          />
                          <span><strong>{tool.toolName}</strong><small>{tool.description || "无说明"}</small></span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {entry.error && <p className="mcp-error" role="alert">{entry.error}</p>}
            </section>
          );
        })}
      </div>
      </fieldset>

      <div className="mcp-editor-actions">
        <button
          className="mcp-secondary-button"
          type="button"
          disabled={busy !== ""}
          onClick={onCancel}
        >
          取消
        </button>
        <button
          className="mcp-secondary-button"
          type="button"
          disabled={busy !== "" || (!canAttemptInitialParse && !selectedCanTest)}
          onClick={() => void testEntries()}
        >
          {busy === "testing" && <LoaderCircle className="attachment-spinner" aria-hidden="true" size={15} />}
          测试并查看工具
        </button>
        <button
          className="mcp-add"
          type="button"
          disabled={!canSave}
          onClick={() => void saveEntries()}
        >
          {busy === "saving" && <LoaderCircle className="attachment-spinner" aria-hidden="true" size={15} />}
          {mode === "thread" ? "添加到当前对话" : "保存"}
        </button>
      </div>
    </div>
  );
}
