import { restoreText, obfuscatePrompt } from "../core/transform";
import { DEFAULT_SETTINGS, type ExtensionSettings } from "../core/types";
import {
  errorResponse,
  isRuntimeRequest,
  type ConversationRef,
  type RenderPayload,
  type RuntimeRequest,
  type RuntimeResponse,
  type SupportedSite
} from "./messages";
import { chromeStorage, restrictStorageAccess } from "./storage";

const storage = chromeStorage();
const queues = new Map<string, Promise<unknown>>();
const MAPPING_QUEUE = "mapping-operations";
const supportedOrigins = new Set(["https://chatgpt.com", "https://claude.ai"]);

function serial<T>(key: string, operation: () => Promise<T>): Promise<T> {
  const previous = queues.get(key) ?? Promise.resolve();
  const next = previous.catch(() => undefined).then(operation);
  queues.set(key, next);
  const cleanup = (): void => {
    if (queues.get(key) === next) queues.delete(key);
  };
  void next.then(cleanup, cleanup);
  return next;
}

function isInternalSender(sender: chrome.runtime.MessageSender): boolean {
  if (!sender.url) return false;
  try {
    return new URL(sender.url).protocol === "chrome-extension:";
  } catch {
    return false;
  }
}

function isSupportedPageSender(sender: chrome.runtime.MessageSender): boolean {
  if (!sender.url) return false;
  try {
    return supportedOrigins.has(new URL(sender.url).origin);
  } catch {
    return false;
  }
}

function assertConversation(request: RuntimeRequest, sender: chrome.runtime.MessageSender): ConversationRef {
  if (!("site" in request) || !("conversationId" in request)) {
    throw new Error("Conversation reference is required");
  }
  if (!(["chatgpt", "claude"] as SupportedSite[]).includes(request.site)) {
    throw new Error("Unsupported site");
  }
  let conversationId = request.conversationId.trim();
  if (!conversationId) {
    if (sender.tab?.id === undefined) throw new Error("Conversation ID is unavailable");
    conversationId = `tab:${sender.tab.id}`;
  }
  if (conversationId.length > 256 || /[\r\n\0]/.test(conversationId)) {
    throw new Error("Invalid conversation ID");
  }
  return { site: request.site, conversationId };
}

function newRenderId(): string {
  return globalThis.crypto.randomUUID();
}

function resolvedConversationId(
  conversationId: string,
  sender: chrome.runtime.MessageSender
): string {
  const clean = conversationId.trim();
  if (clean) return clean;
  if (sender.tab?.id === undefined) throw new Error("Conversation ID is unavailable");
  return `tab:${sender.tab.id}`;
}

async function handle(
  request: RuntimeRequest,
  sender: chrome.runtime.MessageSender
): Promise<RuntimeResponse> {
  if (request.type === "GET_SETTINGS") {
    return { ok: true, settings: await storage.getSettings() };
  }
  if (request.type === "SET_SETTINGS") {
    if (!isInternalSender(sender)) return errorResponse("Untrusted settings sender", "forbidden");
    const allowed: Partial<ExtensionSettings> = {};
    for (const key of Object.keys(DEFAULT_SETTINGS) as Array<keyof ExtensionSettings>) {
      if (request.settings[key] !== undefined) {
        (allowed as Record<string, unknown>)[key] = request.settings[key];
      }
    }
    return { ok: true, settings: await storage.setSettings(allowed) };
  }
  if (request.type === "GET_RENDER") {
    if (!isInternalSender(sender)) return errorResponse("Untrusted render sender", "forbidden");
    return { ok: true, render: await storage.getRender(request.renderId, true) };
  }
  if (request.type === "CLEAR_ALL_MAPPINGS") {
    if (!isInternalSender(sender)) return errorResponse("Untrusted cleanup sender", "forbidden");
    return serial(MAPPING_QUEUE, async () => ({
      ok: true,
      cleared: await storage.clearAllMappings()
    }));
  }
  if (request.type === "RESET_PROVISIONAL") {
    if (!isSupportedPageSender(sender) || sender.tab?.id === undefined) {
      return errorResponse("Untrusted provisional reset sender", "forbidden");
    }
    if (!(request.site === "chatgpt" || request.site === "claude")) {
      return errorResponse("Unsupported site", "invalid_input");
    }
    return serial(MAPPING_QUEUE, async () => ({
      ok: true,
      cleared: await storage.clearMapping({
        site: request.site,
        conversationId: `tab:${sender.tab!.id}`
      })
    }));
  }

  const ref = assertConversation(request, sender);
  if (request.type === "STORE_UNSAFE_OVERRIDE") {
    if (!isInternalSender(sender)) return errorResponse("Untrusted plaintext sender", "forbidden");
    if (typeof request.text !== "string" || request.text.length > 2_000_000) {
      return errorResponse("Prompt is too large", "invalid_input");
    }
    const unsafeId = globalThis.crypto.randomUUID();
    await storage.putUnsafe(unsafeId, { ref, text: request.text, createdAt: Date.now() });
    return { ok: true, unsafeId };
  }
  if (request.type === "CONSUME_UNSAFE_OVERRIDE") {
    if (!isSupportedPageSender(sender)) return errorResponse("Untrusted page sender", "forbidden");
    return { ok: true, unsafeText: await storage.consumeUnsafe(request.unsafeId, ref) };
  }
  if (request.type === "OBFUSCATE") {
    if (!isInternalSender(sender)) return errorResponse("Plaintext sender is not trusted", "forbidden");
    if (typeof request.text !== "string" || request.text.length > 2_000_000) {
      return errorResponse("Prompt is too large", "invalid_input");
    }
    return serial(MAPPING_QUEUE, async () => {
      const mapping = await storage.getOrCreateMapping(ref);
      const result = obfuscatePrompt(request.text, mapping);
      await storage.saveMapping(result.mapping);
      return {
        ok: true,
        obfuscatedText: result.text,
        replacementCount: result.replacementCount,
        validationStatus: result.validationStatus,
        findings: result.artifact.findings
      };
    });
  }
  if (request.type === "PREPARE_RESTORE") {
    if (!isSupportedPageSender(sender)) return errorResponse("Untrusted page sender", "forbidden");
    if (typeof request.rawText !== "string" || request.rawText.length > 2_000_000) {
      return errorResponse("Response is too large", "invalid_input");
    }
    const mapping = await storage.getMapping(ref);
    if (!mapping) {
      return { ok: true, renderId: "", replacementCount: 0, unknownMarkers: [] };
    }
    const restored = restoreText(request.rawText, mapping);
    if (restored.replacementCount === 0 && restored.unknownMarkers.length === 0) {
      return { ok: true, renderId: "", replacementCount: 0, unknownMarkers: [] };
    }
    const renderId = newRenderId();
    const payload: RenderPayload = {
      renderId,
      site: ref.site,
      conversationId: ref.conversationId,
      rawText: request.rawText,
      restoredText: restored.text,
      replacementCount: restored.replacementCount,
      unknownMarkers: restored.unknownMarkers,
      createdAt: new Date().toISOString()
    };
    await storage.putRender(payload);
    return {
      ok: true,
      renderId,
      replacementCount: restored.replacementCount,
      unknownMarkers: restored.unknownMarkers
    };
  }
  if (request.type === "MIGRATE_CONVERSATION") {
    if (!isSupportedPageSender(sender)) return errorResponse("Untrusted page sender", "forbidden");
    const from = {
      site: ref.site,
      conversationId: resolvedConversationId(request.fromConversationId, sender)
    };
    return serial(MAPPING_QUEUE, async () => ({
      ok: true,
      migrated: await storage.migrateMapping(from, ref)
    }));
  }
  if (request.type === "GET_MAPPING_STATS") {
    const mapping = await storage.getMapping(ref);
    return {
      ok: true,
      symbolCount: mapping?.symbols.length ?? 0,
      updatedAt: mapping?.updatedAt ?? null
    };
  }
  if (request.type === "CLEAR_MAPPING") {
    if (!isInternalSender(sender)) return errorResponse("Untrusted cleanup sender", "forbidden");
    return serial(MAPPING_QUEUE, async () => ({
      ok: true,
      cleared: await storage.clearMapping(ref)
    }));
  }
  return errorResponse("Unsupported message", "unsupported_message");
}

chrome.runtime.onInstalled.addListener(() => {
  void restrictStorageAccess().then(async () => {
    await storage.setSettings(await storage.getSettings());
  });
});

chrome.runtime.onStartup.addListener(() => {
  void restrictStorageAccess();
});

chrome.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
  if (!isRuntimeRequest(message)) return false;
  void handle(message, sender)
    .then(sendResponse)
    .catch((error: unknown) => sendResponse(errorResponse(error)));
  return true;
});
