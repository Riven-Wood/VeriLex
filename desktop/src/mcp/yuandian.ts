import type {
  CreateMcpServerInput,
  McpProbeDraftRequest,
  McpServerRecord,
  McpTestResult,
} from "../runtime/contracts";

export type YuandianServiceId = "law" | "case" | "company" | "securities";

export const YUANDIAN_SERVICES = [
  {
    id: "law",
    displayName: "元典智库·法律法规",
    url: "https://open.chineselaw.com/mcp/law/stream",
  },
  {
    id: "case",
    displayName: "元典智库·案例",
    url: "https://open.chineselaw.com/mcp/case/stream",
  },
  {
    id: "company",
    displayName: "元典智库·企业",
    url: "https://open.chineselaw.com/mcp/company/stream",
  },
  {
    id: "securities",
    displayName: "元典智库·证券",
    url: "https://open.chineselaw.com/mcp/securities/stream",
  },
] as const satisfies ReadonlyArray<{
  id: YuandianServiceId;
  displayName: string;
  url: string;
}>;

export const YUANDIAN_CONSOLE_COMMAND = "open_yuandian_console";

export function yuandianServiceFromUrl(url: string | null): YuandianServiceId | null {
  return YUANDIAN_SERVICES.find((service) => service.url === url)?.id ?? null;
}

export function yuandianServiceFromServer(
  server: Pick<McpServerRecord, "transport" | "url" | "headers" | "secretHeaderKeys">,
): YuandianServiceId | null {
  const hasSecretAuthorization = server.secretHeaderKeys.some((name) =>
    name.toLocaleLowerCase("en-US") === "authorization");
  const hasPublicAuthorization = Object.keys(server.headers).some((name) =>
    name.toLocaleLowerCase("en-US") === "authorization");
  if (server.transport !== "http" || !hasSecretAuthorization || hasPublicAuthorization) return null;
  return yuandianServiceFromUrl(server.url);
}

export function yuandianProbe(
  service: (typeof YUANDIAN_SERVICES)[number],
  apiKey: string,
): McpProbeDraftRequest {
  return {
    draft: {
      displayName: service.displayName,
      transport: "http",
      command: null,
      args: [],
      cwd: null,
      url: service.url,
      env: {},
      headers: {},
      secretEnvKeys: [],
      secretHeaderKeys: ["Authorization"],
      connectTimeoutMs: 10_000,
      requestTimeoutMs: 30_000,
    },
    secretPatch: {
      env: {},
      headers: { Authorization: `Bearer ${apiKey.trim()}` },
    },
    acceptedRiskCodes: [],
  };
}

export function yuandianCreateInput(
  probe: McpProbeDraftRequest,
  result: McpTestResult,
): CreateMcpServerInput {
  return {
    ...probe,
    testReceipt: result.testReceipt,
    toolDecisions: result.tools.map((tool) => ({
      toolName: tool.toolName,
      enabled: true,
      expectedDescriptionHash: tool.descriptionHash,
      expectedSchemaHash: tool.schemaHash,
    })),
  };
}
