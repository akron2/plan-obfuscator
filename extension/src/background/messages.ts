import type {
  ConversationMapping,
  ExtensionSettings,
  ParseFinding,
  SendGuardMode
} from "../core/types";

export type SupportedSite = "chatgpt" | "claude";

export interface ConversationRef {
  site: SupportedSite;
  conversationId: string;
}

export interface RenderPayload {
  renderId: string;
  site: SupportedSite;
  conversationId: string;
  rawText: string;
  restoredText: string;
  replacementCount: number;
  unknownMarkers: string[];
  createdAt: string;
}

export type RuntimeRequest =
  | { type: "GET_SETTINGS" }
  | { type: "SET_SETTINGS"; settings: Partial<ExtensionSettings> }
  | ({ type: "OBFUSCATE"; text: string } & ConversationRef)
  | ({ type: "PREPARE_RESTORE"; rawText: string } & ConversationRef)
  | ({ type: "STORE_UNSAFE_OVERRIDE"; text: string } & ConversationRef)
  | ({ type: "CONSUME_UNSAFE_OVERRIDE"; unsafeId: string } & ConversationRef)
  | { type: "GET_RENDER"; renderId: string }
  | { type: "RESET_PROVISIONAL"; site: SupportedSite }
  | ({ type: "MIGRATE_CONVERSATION"; fromConversationId: string } & ConversationRef)
  | ({ type: "GET_MAPPING_STATS" } & ConversationRef)
  | ({ type: "CLEAR_MAPPING" } & ConversationRef)
  | { type: "CLEAR_ALL_MAPPINGS" };

export type RuntimeResponse =
  | { ok: true; settings: ExtensionSettings }
  | {
      ok: true;
      obfuscatedText: string;
      replacementCount: number;
      validationStatus: "ok" | "warning" | "error";
      findings: ParseFinding[];
    }
  | {
      ok: true;
      renderId: string;
      replacementCount: number;
      unknownMarkers: string[];
    }
  | { ok: true; render: RenderPayload | null }
  | { ok: true; unsafeId: string }
  | { ok: true; unsafeText: string | null }
  | { ok: true; migrated: boolean }
  | { ok: true; symbolCount: number; updatedAt: string | null }
  | { ok: true; cleared: number }
  | { ok: false; error: string; code: string };

export interface ContentSettings {
  enabled: boolean;
  sendGuard: SendGuardMode;
  autoRestore: boolean;
  hideOriginal: boolean;
  restoreVisibleMessages: boolean;
}

export function isRuntimeRequest(value: unknown): value is RuntimeRequest {
  return Boolean(
    value &&
      typeof value === "object" &&
      "type" in value &&
      typeof (value as { type?: unknown }).type === "string"
  );
}

export function errorResponse(error: unknown, code = "internal_error"): RuntimeResponse {
  return {
    ok: false,
    code,
    error: error instanceof Error ? error.message : String(error)
  };
}

export type StoredMapping = ConversationMapping;
