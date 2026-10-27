import type { CitationSource } from "../runtime/contracts";

const MAX_SOURCE_ID_CHARACTERS = 128;
const CITATION_TOKEN_PREFIX = "VERILEX_CITATION_TOKEN_";
const SOURCE_KEYS = new Set([
  "number",
  "sourceId",
  "title",
  "url",
  "site",
  "fetchedAt",
  "truncated",
]);
const SPECIAL_HOST_SUFFIXES = [
  "localhost",
  "local",
  "localdomain",
  "internal",
  "lan",
  "test",
  "invalid",
  "example",
  "home.arpa",
  "onion",
];

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  });
}

function hasControlOrSpace(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.codePointAt(0)!;
    return code <= 32 || code === 127;
  });
}

function unicodeLength(value: string): number {
  return Array.from(value).length;
}

function isNonPublicIpv4(parts: number[]): boolean {
  const [first, second, third, fourth] = parts;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    (first === 100 && second >= 64 && second <= 127) ||
    (first === 169 && second === 254) ||
    (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 &&
      second === 0 &&
      third === 0 &&
      fourth !== 9 &&
      fourth !== 10) ||
    (first === 192 && second === 0 && third === 2) ||
    (first === 192 && second === 168) ||
    (first === 198 && (second === 18 || second === 19)) ||
    (first === 198 && second === 51 && third === 100) ||
    (first === 203 && second === 0 && third === 113) ||
    first >= 224
  );
}

function parseIpv6(value: string): number[] | null {
  const halves = value.split("::");
  if (halves.length > 2) return null;
  const parseHalf = (half: string): number[] | null => {
    if (half === "") return [];
    const groups = half.split(":");
    if (groups.some((group) => !/^[0-9a-f]{1,4}$/.test(group))) return null;
    return groups.map((group) => Number.parseInt(group, 16));
  };
  const head = parseHalf(halves[0]);
  const tail = parseHalf(halves[1] ?? "");
  if (head === null || tail === null) return null;
  if (halves.length === 1) return head.length === 8 ? head : null;
  const omitted = 8 - head.length - tail.length;
  if (omitted < 1) return null;
  return [...head, ...Array<number>(omitted).fill(0), ...tail];
}

function matchesIpv6Prefix(
  address: number[],
  network: number[],
  prefixLength: number,
): boolean {
  const fullGroups = Math.floor(prefixLength / 16);
  for (let index = 0; index < fullGroups; index += 1) {
    if (address[index] !== network[index]) return false;
  }
  const remainingBits = prefixLength % 16;
  if (remainingBits === 0) return true;
  const mask = (0xffff << (16 - remainingBits)) & 0xffff;
  return (address[fullGroups] & mask) === (network[fullGroups] & mask);
}

function isRuntimePublicIpv6(address: number[]): boolean {
  const mappedIpv4 = [0, 0, 0, 0, 0, 0xffff];
  if (matchesIpv6Prefix(address, mappedIpv4, 96)) {
    return !isNonPublicIpv4([
      address[6] >> 8,
      address[6] & 255,
      address[7] >> 8,
      address[7] & 255,
    ]);
  }

  // Mirror the stable, conservative table in Runtime web_hosts.py. Runtime
  // defines it explicitly so Python 3.10–3.13 ipaddress changes cannot alter
  // the persisted-source contract underneath the Desktop.
  if (!matchesIpv6Prefix(address, [0x2000], 3)) return false;

  if (matchesIpv6Prefix(address, [0x2001, 0], 23)) {
    const exceptions = [
      { network: [0x2001, 1, 0, 0, 0, 0, 0, 1], bits: 128 },
      { network: [0x2001, 1, 0, 0, 0, 0, 0, 2], bits: 128 },
      { network: [0x2001, 3], bits: 32 },
      { network: [0x2001, 4, 0x112], bits: 48 },
      { network: [0x2001, 0x20], bits: 28 },
      { network: [0x2001, 0x30], bits: 28 },
    ];
    if (!exceptions.some(({ network, bits }) =>
      matchesIpv6Prefix(address, network, bits))) return false;
  }

  return !(
    matchesIpv6Prefix(address, [0x2001, 0xdb8], 32) ||
    matchesIpv6Prefix(address, [0x2002], 16) ||
    matchesIpv6Prefix(address, [0x3fff, 0], 20)
  );
}

function isCanonicalHostname(hostname: string): boolean {
  if (hostname.startsWith("[") && hostname.endsWith("]")) return true;
  const ipv4 = hostname.split(".");
  if (
    ipv4.length === 4 &&
    ipv4.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
  ) return true;
  return (
    hostname.length <= 253 &&
    hostname.split(".").every(
      (label) =>
        label.length > 0 &&
        label.length <= 63 &&
        /^[a-z0-9-]+$/.test(label) &&
        !label.startsWith("-") &&
        !label.endsWith("-"),
    )
  );
}

/**
 * Reject persisted source hosts that contradict the Runtime's pinned address
 * tables. This is only a corrupt-history guard: it never authorizes a network
 * connection, and the Runtime still owns DNS resolution and SSRF protection.
 */
function isNonPublicLiteralHost(hostname: string): boolean {
  if (SPECIAL_HOST_SUFFIXES.some(
    (suffix) => hostname === suffix || hostname.endsWith(`.${suffix}`),
  )) return true;
  const ipv4 = hostname.split(".");
  if (
    ipv4.length === 4 &&
    ipv4.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
  ) {
    return isNonPublicIpv4(ipv4.map(Number));
  }
  const ipv6 = hostname.startsWith("[") && hostname.endsWith("]")
    ? hostname.slice(1, -1).toLowerCase()
    : "";
  if (ipv6 !== "") {
    const parsedIpv6 = parseIpv6(ipv6);
    return parsedIpv6 === null || !isRuntimePublicIpv6(parsedIpv6);
  }
  return false;
}

export function citationTokenNonceFrom(text: string): number {
  const used = new Set<number>();
  let cursor = 0;
  while (cursor < text.length) {
    const start = text.indexOf(CITATION_TOKEN_PREFIX, cursor);
    if (start < 0) break;
    const digitsStart = start + CITATION_TOKEN_PREFIX.length;
    let end = digitsStart;
    while (end < text.length) {
      const code = text.charCodeAt(end);
      if (code < 48 || code > 57) break;
      end += 1;
    }
    if (end > digitsStart && text[end] === "_") {
      const nonce = Number(text.slice(digitsStart, end));
      if (Number.isSafeInteger(nonce)) used.add(nonce);
    }
    cursor = end > digitsStart && text[end] === "_"
      ? end + 1
      : Math.max(end, digitsStart);
  }
  let nonce = 0;
  while (used.has(nonce)) nonce += 1;
  return nonce;
}

function hasSingleDecodedDotSegment(path: string): boolean {
  const decodedPath = path.replace(
    /%([0-9a-f]{2})/gi,
    (_escape, hex: string) => String.fromCharCode(Number.parseInt(hex, 16)),
  );
  return decodedPath.split("/").some(
    (segment) => segment === "." || segment === "..",
  );
}

function safeSourceUrl(value: unknown): URL | null {
  if (
    typeof value !== "string" ||
    unicodeLength(value) === 0 ||
    unicodeLength(value) > 2048
  ) {
    return null;
  }
  if (
    hasControlOrSpace(value) ||
    /%(?:0a|0d)/i.test(value) ||
    value.includes("\\")
  ) return null;
  try {
    const parsed = new URL(value);
    const canonicalParts = /^(https?):\/\/([^/?#]+)(\/[^?#]*)(?:\?([^#]+))?$/.exec(
      value,
    );
    if (
      canonicalParts === null ||
      `${canonicalParts[1]}:` !== parsed.protocol ||
      canonicalParts[2] !== parsed.host ||
      hasSingleDecodedDotSegment(canonicalParts[3]) ||
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      parsed.hostname.length === 0 ||
      parsed.hostname.endsWith(".") ||
      !isCanonicalHostname(parsed.hostname) ||
      isNonPublicLiteralHost(parsed.hostname) ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.hash !== ""
    ) return null;
    return parsed;
  } catch {
    return null;
  }
}

function isSourceId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    unicodeLength(value) > 0 &&
    unicodeLength(value) <= MAX_SOURCE_ID_CHARACTERS &&
    value === value.trim() &&
    !value.includes("[") &&
    !value.includes("]") &&
    !hasControlCharacter(value)
  );
}

function isCanonicalUtcTimestamp(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 64) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?Z$/.exec(value);
  if (!match) return false;
  const [year, month, day, hour, minute, second] = match
    .slice(1, 7)
    .map(Number);
  const microseconds = match[7];
  if (year < 1 || (microseconds !== undefined && microseconds === "000000")) {
    return false;
  }
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, second, 0);
  return (
    instant.getUTCFullYear() === year &&
    instant.getUTCMonth() === month - 1 &&
    instant.getUTCDate() === day &&
    instant.getUTCHours() === hour &&
    instant.getUTCMinutes() === minute &&
    instant.getUTCSeconds() === second
  );
}

function isCitationSource(value: unknown): value is CitationSource {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const source = value as Record<string, unknown>;
  if (
    Object.keys(source).length !== SOURCE_KEYS.size ||
    Object.keys(source).some((key) => !SOURCE_KEYS.has(key))
  ) return false;
  const sourceUrl = safeSourceUrl(source.url);
  const sourceHostname = sourceUrl?.hostname.startsWith("[")
    ? sourceUrl.hostname.slice(1, -1)
    : sourceUrl?.hostname;
  return (
    Number.isInteger(source.number) &&
    (source.number as number) > 0 &&
    isSourceId(source.sourceId) &&
    typeof source.title === "string" &&
    unicodeLength(source.title) > 0 &&
    unicodeLength(source.title) <= 512 &&
    !hasControlCharacter(source.title) &&
    sourceUrl !== null &&
    typeof source.site === "string" &&
    source.site === sourceHostname &&
    isCanonicalUtcTimestamp(source.fetchedAt) &&
    typeof source.truncated === "boolean"
  );
}

/**
 * Parse Runtime-owned source metadata. A malformed member rejects the whole
 * block so a corrupt payload can never contribute a clickable citation.
 */
export function citationSourcesFrom(value: unknown): CitationSource[] | null {
  if (!Array.isArray(value)) return null;
  const seenNumbers = new Set<number>();
  const seenIds = new Set<string>();
  const sources: CitationSource[] = [];
  for (const valueSource of value) {
    if (!isCitationSource(valueSource)) return null;
    if (valueSource.number !== sources.length + 1) return null;
    if (seenNumbers.has(valueSource.number) || seenIds.has(valueSource.sourceId)) {
      return null;
    }
    seenNumbers.add(valueSource.number);
    seenIds.add(valueSource.sourceId);
    sources.push(valueSource);
  }
  return sources;
}

export type CitationTextPart =
  | { kind: "text"; value: string }
  | { kind: "citation"; number: number; url: string; title: string };

const CITATION_PREFIX = "[[cite:";

function trailingCitationPrefixLength(value: string): number {
  const maximum = Math.min(value.length, CITATION_PREFIX.length - 1);
  for (let length = maximum; length > 0; length -= 1) {
    if (value.endsWith(CITATION_PREFIX.slice(0, length))) return length;
  }
  return 0;
}

function appendText(parts: CitationTextPart[], value: string): void {
  if (!value) return;
  const last = parts.at(-1);
  if (last?.kind === "text") last.value += value;
  else parts.push({ kind: "text", value });
}

/** Split internal citation markers without ever deriving a URL from marker text. */
export function renderCitationText(
  text: string,
  valueSources: unknown,
  streaming: boolean,
): CitationTextPart[] {
  const sources = citationSourcesFrom(valueSources) ?? [];
  const sourceById = new Map(sources.map((source) => [source.sourceId, source]));
  const parts: CitationTextPart[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const markerStart = text.indexOf(CITATION_PREFIX, cursor);
    if (markerStart < 0) {
      const remainder = text.slice(cursor);
      const partialLength = trailingCitationPrefixLength(remainder);
      appendText(
        parts,
        partialLength > 0 ? remainder.slice(0, -partialLength) : remainder,
      );
      if (partialLength > 0 && !streaming) {
        appendText(parts, "〔引用未验证〕");
      }
      break;
    }
    appendText(parts, text.slice(cursor, markerStart));
    const markerEnd = text.indexOf("]]", markerStart + CITATION_PREFIX.length);
    if (markerEnd < 0) {
      if (!streaming) appendText(parts, "〔引用未验证〕");
      break;
    }
    if (!streaming) {
      const sourceId = text.slice(
        markerStart + CITATION_PREFIX.length,
        markerEnd,
      );
      const source = sourceById.get(sourceId);
      if (source) {
        parts.push({
          kind: "citation",
          number: source.number,
          url: source.url,
          title: source.title,
        });
      } else {
        appendText(parts, "〔引用未验证〕");
      }
    }
    cursor = markerEnd + 2;
  }
  return parts;
}

export function visibleStreamingText(text: string): string {
  return renderCitationText(text, [], true)
    .filter((part): part is Extract<CitationTextPart, { kind: "text" }> =>
      part.kind === "text",
    )
    .map((part) => part.value)
    .join("");
}
