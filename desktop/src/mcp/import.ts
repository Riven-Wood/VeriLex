import type {
  McpServerDraft,
  McpTransport,
} from "../runtime/contracts";

const MAX_IMPORT_BYTES = 256 * 1024;
const MAX_IMPORT_SERVERS = 32;
const MAX_JSON_DEPTH = 64;
const MAX_COLLECTION_ITEMS = 512;
const MAX_FIELD_BYTES = 64 * 1024;
const MAX_TIMEOUT_MS = 10 * 60 * 1000;
const DEFAULT_CONNECT_TIMEOUT_MS = 10_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

const SERVER_FIELDS = new Set([
  "name",
  "displayName",
  "display_name",
  "type",
  "transport",
  "command",
  "args",
  "cwd",
  "url",
  "env",
  "headers",
  "connectTimeoutMs",
  "connect_timeout_ms",
  "requestTimeoutMs",
  "request_timeout_ms",
  "enabled",
  "requires_approval",
]);
const SECRET_MARKERS = [
  "token", "accesstoken", "refreshtoken", "apitoken", "authtoken", "secret", "clientsecret",
  "password", "passwd", "key", "apikey", "auth", "credential", "signature", "sig",
  "xamzcredential", "xamzsignature",
];
const SECRET_HEADERS = new Set([
  "authorization",
  "cookie",
  "proxy-authorization",
  "x-api-key",
  "api-key",
]);
const UNSAFE_OBJECT_KEYS = new Set(["__proto__", "constructor", "prototype"]);

type JsonObject = Record<string, unknown>;

export interface McpImportPreview extends McpServerDraft {
  /** Kept only in the current editor state and sent through the secretPatch API field. */
  secretPatch: {
    env: Record<string, string | null>;
    headers: Record<string, string | null>;
  };
}

function utf8Size(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUnsafeObjectKey(value: string): boolean {
  return UNSAFE_OBJECT_KEYS.has(value.toLocaleLowerCase("en-US"));
}

function validText(value: unknown, allowEmpty = true): value is string {
  return typeof value === "string"
    && (allowEmpty || value.length > 0)
    && utf8Size(value) <= MAX_FIELD_BYTES
    && !Array.from(value).some((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127;
    });
}

function validJsonTree(root: unknown): boolean {
  const stack: Array<[unknown, number]> = [[root, 1]];
  let nodes = 0;
  while (stack.length > 0) {
    const [value, depth] = stack.pop()!;
    nodes += 1;
    if (depth > MAX_JSON_DEPTH || nodes > 100_000) return false;
    if (Array.isArray(value)) {
      if (value.length > MAX_COLLECTION_ITEMS) return false;
      for (const item of value) stack.push([item, depth + 1]);
      continue;
    }
    if (isObject(value)) {
      const entries = Object.entries(value);
      if (entries.length > MAX_COLLECTION_ITEMS) return false;
      for (const [key, item] of entries) {
        if (!validText(key, false) || isUnsafeObjectKey(key)) return false;
        stack.push([item, depth + 1]);
      }
      continue;
    }
    if (typeof value === "string") {
      if (!validText(value)) return false;
      continue;
    }
    if (value !== null && !["boolean", "number"].includes(typeof value)) return false;
    if (typeof value === "number" && !Number.isFinite(value)) return false;
  }
  return true;
}

function stringList(value: unknown): string[] | null {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > MAX_COLLECTION_ITEMS) return null;
  return value.every((item) => validText(item)) ? [...value] as string[] : null;
}

function stringMap(
  value: unknown,
  caseInsensitiveKeys = false,
): Record<string, string> | null {
  if (value === undefined || value === null) return {};
  if (!isObject(value) || Object.keys(value).length > MAX_COLLECTION_ITEMS) return null;
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const [key, item] of Object.entries(value)) {
    if (!validText(key, false) || isUnsafeObjectKey(key) || !validText(item)) return null;
    const comparable = caseInsensitiveKeys ? key.toLocaleLowerCase("en-US") : key;
    if (seen.has(comparable)) return null;
    seen.add(comparable);
    result[key] = item;
  }
  return result;
}

const credentialName = (name: string) => name.replace(/[-_]/g, "").toLocaleLowerCase("en-US");
const CREDENTIAL_NAMES = new Set([...SECRET_MARKERS, ...SECRET_HEADERS].map(credentialName));

function timeout(
  raw: JsonObject,
  camel: string,
  snake: string,
  fallback: number,
): number | null {
  if (camel in raw && snake in raw) return null;
  const value = camel in raw ? raw[camel] : snake in raw ? raw[snake] : fallback;
  return Number.isInteger(value) && Number(value) > 0 && Number(value) <= MAX_TIMEOUT_MS
    ? Number(value)
    : null;
}

function transport(raw: JsonObject, hasUrl: boolean): McpTransport | null {
  if ("type" in raw && "transport" in raw) return null;
  const declared = raw.transport ?? raw.type;
  if (declared === undefined) return hasUrl ? "http" : "stdio";
  if (typeof declared !== "string") return null;
  switch (declared.toLocaleLowerCase("en-US")) {
    case "stdio":
      return "stdio";
    case "sse":
      return "sse";
    case "http":
    case "https":
    case "streamable-http":
    case "streamable_http":
      return "http";
    default:
      return null;
  }
}

export function safeMcpUrl(value: string): boolean {
  if (/\s|\\/.test(value)) return false;
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol)) return false;
    if (parsed.username || parsed.password || parsed.hash) return false;
    for (const key of parsed.searchParams.keys()) {
      if (CREDENTIAL_NAMES.has(credentialName(key))) {
        return false;
      }
    }
    return Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

function displayName(raw: JsonObject, suppliedName?: string): string | null {
  const aliases = ["name", "displayName", "display_name"].filter((key) => key in raw);
  if (aliases.length > 1) return null;
  const normalizedName = (value: unknown): string | null => {
    if (!validText(value, false)) return null;
    const trimmed = value.trim();
    return trimmed && utf8Size(trimmed) <= 256 ? trimmed : null;
  };
  const direct = suppliedName ?? (aliases.length === 1 ? raw[aliases[0]] : undefined);
  if (direct !== undefined) return normalizedName(direct);
  const args = Array.isArray(raw.args) ? raw.args : [];
  const candidate = args.find((item) => typeof item === "string" && item && !item.startsWith("-"));
  const fallback = candidate ?? raw.command;
  if (typeof fallback === "string" && fallback) {
    return normalizedName(fallback.replaceAll("\\", "/").split("/").pop()!);
  }
  if (typeof raw.url === "string") {
    try {
      return normalizedName(new URL(raw.url).hostname || "MCP 服务器");
    } catch {
      return null;
    }
  }
  return "MCP 服务器";
}

function draftFromRaw(raw: unknown, suppliedName?: string): McpImportPreview | null {
  if (!isObject(raw) || Object.keys(raw).some((key) => !SERVER_FIELDS.has(key))) return null;
  if (("enabled" in raw && typeof raw.enabled !== "boolean")
    || ("requires_approval" in raw && typeof raw.requires_approval !== "boolean")) return null;
  const name = displayName(raw, suppliedName);
  const command = raw.command === undefined || raw.command === null ? null : raw.command;
  const url = raw.url === undefined || raw.url === null ? null : raw.url;
  if (!name || (command !== null && !validText(command, false)) || (url !== null && !validText(url, false))) return null;
  if ((command === null) === (url === null)) return null;
  if (url !== null && !safeMcpUrl(url)) return null;
  const resolvedTransport = transport(raw, url !== null);
  if (!resolvedTransport
    || (resolvedTransport === "stdio" && command === null)
    || (resolvedTransport !== "stdio" && url === null)) return null;
  const args = stringList(raw.args);
  const env = stringMap(raw.env);
  const headers = stringMap(raw.headers, true);
  const cwd = raw.cwd === undefined || raw.cwd === null ? null : raw.cwd;
  const connectTimeoutMs = timeout(raw, "connectTimeoutMs", "connect_timeout_ms", DEFAULT_CONNECT_TIMEOUT_MS);
  const requestTimeoutMs = timeout(raw, "requestTimeoutMs", "request_timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS);
  if (!args || !env || !headers || (cwd !== null && !validText(cwd, false))
    || connectTimeoutMs === null || requestTimeoutMs === null) return null;
  return {
    displayName: name,
    transport: resolvedTransport,
    command,
    args,
    cwd,
    url,
    env: {},
    headers: {},
    secretEnvKeys: Object.keys(env).sort(),
    secretHeaderKeys: Object.keys(headers).sort(),
    connectTimeoutMs,
    requestTimeoutMs,
    secretPatch: { env, headers },
  };
}

function jsonEntries(value: unknown): Array<[string, JsonObject]> | null {
  if (!isObject(value)) return null;
  let entries: Array<[string, unknown]>;
  if ("mcpServers" in value) {
    if (Object.keys(value).length !== 1 || !isObject(value.mcpServers)) return null;
    entries = Object.entries(value.mcpServers);
  } else if (typeof value.command === "string" || typeof value.url === "string") {
    const name = displayName(value);
    return name ? [[name, value]] : null;
  } else {
    entries = Object.entries(value);
  }
  if (entries.length === 0 || entries.length > MAX_IMPORT_SERVERS) return null;
  if (!entries.every(([name, raw]) => validText(name, false) && isObject(raw))) return null;
  return entries as Array<[string, JsonObject]>;
}

function parseJsonPreview(text: string): McpImportPreview[] | null {
  try {
    const value: unknown = JSON.parse(text);
    if (!validJsonTree(value)) return null;
    const entries = jsonEntries(value);
    if (!entries) return null;
    const drafts = entries.map(([name, raw]) => draftFromRaw(raw, name));
    return drafts.every((item): item is McpImportPreview => item !== null) ? drafts : null;
  } catch {
    return null;
  }
}

function shellTokens(text: string): string[] | null {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | "\"" | null = null;
  let started = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quote !== null) {
      if (character === quote) quote = null;
      else if (character === "\\" && quote === "\"" && index + 1 < text.length) token += text[++index];
      else token += character;
      started = true;
      continue;
    }
    if (character === "'" || character === "\"") {
      quote = character;
      started = true;
    } else if (/\s/.test(character)) {
      if (started) {
        tokens.push(token);
        token = "";
        started = false;
      }
    } else if (character === "\\" && index + 1 < text.length) {
      token += text[++index];
      started = true;
    } else {
      token += character;
      started = true;
    }
  }
  if (quote !== null) return null;
  if (started) tokens.push(token);
  return tokens;
}

function splitAssignment(value: string, allowColon: boolean): [string, string] | null {
  const equals = value.indexOf("=");
  const colon = allowColon ? value.indexOf(":") : -1;
  const index = equals >= 0 ? equals : colon;
  if (index <= 0) return null;
  const key = value.slice(0, index).trim();
  const item = value.slice(index + 1).trimStart();
  return validText(key, false) && validText(item) ? [key, item] : null;
}

function parseClaude(text: string): McpImportPreview | null {
  const tokens = shellTokens(text);
  if (!tokens || tokens.slice(0, 3).join(" ") !== "claude mcp add") return null;
  const remaining = tokens.slice(3);
  const env: Record<string, string> = {};
  const headers: Record<string, string> = {};
  const positional: string[] = [];
  let declaredTransport: string | undefined;
  let sawDelimiter = false;
  for (let index = 0; index < remaining.length;) {
    const token = remaining[index];
    if (token === "--") {
      sawDelimiter = true;
      positional.push(...remaining.slice(index + 1));
      break;
    }
    const [option, inline] = token.startsWith("--") && token.includes("=")
      ? [token.slice(0, token.indexOf("=")), token.slice(token.indexOf("=") + 1)]
      : [token, undefined];
    if (["--transport", "-t", "--env", "-e", "--header", "--scope", "-s"].includes(option)) {
      const value = inline ?? remaining[index + 1];
      if (value === undefined) return null;
      if (option === "--transport" || option === "-t") declaredTransport = value;
      else if (option === "--scope" || option === "-s") {
        if (!["local", "project", "user"].includes(value)) return null;
      } else {
        const assignment = splitAssignment(value, option === "--header");
        if (!assignment || isUnsafeObjectKey(assignment[0])) return null;
        const target = option === "--header" ? headers : env;
        if (Object.keys(target).some((key) => key.toLocaleLowerCase("en-US") === assignment[0].toLocaleLowerCase("en-US"))) return null;
        target[assignment[0]] = assignment[1];
      }
      index += inline === undefined ? 2 : 1;
      continue;
    }
    if (token.startsWith("-")) return null;
    positional.push(token);
    index += 1;
  }
  if (!sawDelimiter && positional.length > 2) return null;
  if (positional.length < 2) return null;
  const [name, target, ...args] = positional;
  const remote = declaredTransport
    ? ["http", "https", "sse", "streamable-http", "streamable_http"].includes(declaredTransport.toLocaleLowerCase("en-US"))
    : /^https?:\/\//i.test(target);
  if (remote && args.length > 0) return null;
  return draftFromRaw({
    ...(remote ? { url: target } : { command: target, args }),
    ...(declaredTransport ? { type: declaredTransport } : {}),
    env,
    headers,
  }, name);
}

function decodeCursorConfig(value: string): string | null {
  if (value.trimStart().startsWith("{")) return value;
  try {
    const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
    const decoded = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
    return new TextDecoder("utf-8", { fatal: true }).decode(
      Uint8Array.from(decoded, (character) => character.charCodeAt(0)),
    );
  } catch {
    return null;
  }
}

function parseCursor(text: string): McpImportPreview | null {
  try {
    const url = new URL(text);
    if (url.protocol !== "cursor:" || url.host !== "anysphere.cursor-deeplink"
      || url.pathname !== "/mcp/install" || url.hash) return null;
    const pairs = [...url.searchParams.entries()];
    if (pairs.length !== 2 || pairs.filter(([key]) => key === "name").length !== 1
      || pairs.filter(([key]) => key === "config").length !== 1) return null;
    const name = url.searchParams.get("name");
    const encoded = url.searchParams.get("config");
    if (!name || encoded === null) return null;
    const config = decodeCursorConfig(encoded);
    if (!config || utf8Size(config) > MAX_IMPORT_BYTES) return null;
    const value: unknown = JSON.parse(config);
    return validJsonTree(value) ? draftFromRaw(value, name) : null;
  } catch {
    return null;
  }
}

/**
 * Side-effect-free editor hint. The Runtime test receipt remains the only
 * authority for testing or saving the returned draft.
 */
export function previewMcpImport(text: string): McpImportPreview[] {
  if (typeof text !== "string" || utf8Size(text) === 0 || utf8Size(text) > MAX_IMPORT_BYTES) return [];
  const stripped = text.trim();
  if (!stripped || stripped.includes("\0")) return [];
  if (stripped.startsWith("{") || stripped.startsWith("[")) return parseJsonPreview(stripped) ?? [];
  if (Array.from(stripped).some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  })) return [];
  const cursor = parseCursor(stripped);
  if (cursor) return [cursor];
  const claude = parseClaude(stripped);
  if (claude) return [claude];
  const tokens = shellTokens(stripped);
  if (tokens?.[0] && !/^https?:\/\//i.test(tokens[0])) {
    const draft = draftFromRaw({ command: tokens[0], args: tokens.slice(1) });
    return draft ? [draft] : [];
  }
  if (/^https?:\/\//i.test(stripped)) {
    const draft = draftFromRaw({ url: stripped });
    return draft ? [draft] : [];
  }
  return [];
}
