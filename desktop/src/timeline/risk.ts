import type { RiskLevel } from "../runtime/contracts";

const LEGACY_HIGH_RISK_TOOLS = new Set([
  "write_file",
  "apply_file_patch",
  "run_command",
]);

export function riskLevelForTool(
  value: unknown,
  toolName: string,
): RiskLevel | undefined {
  if (value === "low" || value === "medium" || value === "high") return value;
  return LEGACY_HIGH_RISK_TOOLS.has(toolName) ? "high" : undefined;
}
