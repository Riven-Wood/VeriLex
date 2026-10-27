export type ContextWindowInputResult =
  | { tokens: number }
  | { error: string };

const INTEGER_EXAMPLE_ERROR = "请输入整数，例如 128K、1M 或 32768";
const BOUNDS_ERROR = "上下文上限必须在 1 到 1000 万 tokens 之间";
const CONTEXT_WINDOW_PATTERN = /^(\d+)([kKmM])?$/;

export function parseContextWindowInput(value: string): ContextWindowInputResult {
  const match = CONTEXT_WINDOW_PATTERN.exec(value.trim());
  if (!match) return { error: INTEGER_EXAMPLE_ERROR };

  const base = Number(match[1]);
  const suffix = match[2]?.toLowerCase();
  const multiplier = suffix === "m" ? 1_000_000 : suffix === "k" ? 1_000 : 1;
  const tokens = base * multiplier;
  if (!Number.isSafeInteger(tokens) || tokens < 1 || tokens > 10_000_000) {
    return { error: BOUNDS_ERROR };
  }
  return { tokens };
}

export function formatContextWindowTokens(tokens: number): string {
  if (Number.isSafeInteger(tokens) && tokens >= 1_000_000 && tokens % 1_000_000 === 0) {
    return `${tokens / 1_000_000}M`;
  }
  if (Number.isSafeInteger(tokens) && tokens >= 1_000 && tokens % 1_000 === 0) {
    return `${tokens / 1_000}K`;
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(tokens);
}
