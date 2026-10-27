import type { RecoveryKind, TimelineItem } from "./items";

const SETTINGS_ERRORS = new Set([
  "invalid_provider_url",
  "insecure_remote_url",
  "provider_auth_failed",
  "provider_has_no_models",
  "provider_model_invalid",
  "provider_model_not_found",
  "provider_not_found",
  "provider_disabled",
]);

const RESTORE_ERRORS = new Set([
  "provider_request_failed",
  "provider_overloaded",
  "provider_busy",
  "provider_protocol_error",
  "provider_response_too_large",
  "provider_timeout",
  "provider_rate_limited",
  "provider_unreachable",
  "turn_limit_exceeded",
  "approval_wait_expired",
]);

const RESTART_ERRORS = new Set([
  "internal_error",
  "secret_store_unavailable",
  "secret_store_write_failed",
]);

export function recoveryKindFor(
  errorCode: string | undefined,
  sourceText: string,
): RecoveryKind {
  if (errorCode && SETTINGS_ERRORS.has(errorCode)) return "open_settings";
  if (errorCode?.startsWith("attachment_") || errorCode === "model_vision_unsupported") {
    return "reselect_attachment";
  }
  if (errorCode && RESTART_ERRORS.has(errorCode)) return "restart_app";
  if (sourceText && (!errorCode || RESTORE_ERRORS.has(errorCode))) {
    return "restore_text";
  }
  return sourceText ? "restore_text" : "none";
}

export function sourceTextForTurn(
  items: TimelineItem[],
  turnId: string,
): string {
  const item = items.find(
    (candidate) => candidate.kind === "user" && candidate.turnId === turnId,
  );
  return item?.kind === "user" ? item.text : "";
}

export function hasPriorSideEffectForTurn(
  items: TimelineItem[],
  turnId: string,
): boolean {
  return items.some(
    (item) =>
      item.kind === "tool" &&
      item.turnId === turnId &&
      item.riskLevel === "high" &&
      item.status === "succeeded",
  );
}
