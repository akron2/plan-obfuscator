import { createConversationMapping, validateMapping } from "../core/symbol-registry";
import { DEFAULT_SETTINGS, type ConversationMapping, type ExtensionSettings } from "../core/types";
import type { ConversationRef, RenderPayload } from "./messages";

const SETTINGS_KEY = "settings";
const MAPPING_PREFIX = "mapping:";
const RENDER_PREFIX = "render:";
const UNSAFE_PREFIX = "unsafe:";

export interface StorageAreaLike {
  get(keys?: string | string[] | null): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

function mappingKey(ref: ConversationRef): string {
  return `${MAPPING_PREFIX}${ref.site}:${encodeURIComponent(ref.conversationId)}`;
}

function renderKey(renderId: string): string {
  return `${RENDER_PREFIX}${renderId}`;
}

export class ExtensionStorage {
  constructor(
    private readonly local: StorageAreaLike,
    private readonly session: StorageAreaLike
  ) {}

  async getSettings(): Promise<ExtensionSettings> {
    const stored = (await this.local.get(SETTINGS_KEY))[SETTINGS_KEY] as
      | Partial<ExtensionSettings>
      | undefined;
    return { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
  }

  async setSettings(patch: Partial<ExtensionSettings>): Promise<ExtensionSettings> {
    const settings = { ...(await this.getSettings()), ...patch };
    await this.local.set({ [SETTINGS_KEY]: settings });
    return settings;
  }

  async getMapping(ref: ConversationRef): Promise<ConversationMapping | null> {
    const mapping = ((await this.local.get(mappingKey(ref)))[mappingKey(ref)] as
      | ConversationMapping
      | undefined) ?? null;
    if (mapping) validateMapping(mapping);
    return mapping;
  }

  async getOrCreateMapping(ref: ConversationRef): Promise<ConversationMapping> {
    const existing = await this.getMapping(ref);
    if (existing) return existing;
    const mapping = createConversationMapping(ref.site, ref.conversationId);
    await this.saveMapping(mapping);
    return mapping;
  }

  async saveMapping(mapping: ConversationMapping): Promise<void> {
    await this.local.set({
      [mappingKey({ site: mapping.site as ConversationRef["site"], conversationId: mapping.conversationId })]: mapping
    });
  }

  async migrateMapping(
    from: ConversationRef,
    to: ConversationRef
  ): Promise<boolean> {
    if (from.site !== to.site || from.conversationId === to.conversationId) return false;
    const source = await this.getMapping(from);
    if (!source) return false;
    const destination = await this.getMapping(to);
    if (!destination) {
      const moved = { ...source, conversationId: to.conversationId, updatedAt: new Date().toISOString() };
      await this.local.set({ [mappingKey(to)]: moved });
    } else {
      const markerSet = new Set(destination.symbols.map((item) => item.marker.toUpperCase()));
      const mergedSymbols = [...destination.symbols];
      for (const symbol of source.symbols) {
        if (!markerSet.has(symbol.marker.toUpperCase())) mergedSymbols.push(symbol);
      }
      const merged: ConversationMapping = {
        ...destination,
        symbols: mergedSymbols,
        nextSequence: Math.max(destination.nextSequence, source.nextSequence),
        updatedAt: new Date().toISOString()
      };
      await this.local.set({ [mappingKey(to)]: merged });
    }
    await this.local.remove(mappingKey(from));
    return true;
  }

  async clearMapping(ref: ConversationRef): Promise<number> {
    const exists = Boolean(await this.getMapping(ref));
    await this.local.remove(mappingKey(ref));
    return exists ? 1 : 0;
  }

  async clearAllMappings(): Promise<number> {
    const values = await this.local.get(null);
    const keys = Object.keys(values).filter((key) => key.startsWith(MAPPING_PREFIX));
    if (keys.length > 0) await this.local.remove(keys);
    return keys.length;
  }

  async putRender(payload: RenderPayload): Promise<void> {
    await this.session.set({ [renderKey(payload.renderId)]: payload });
  }

  async getRender(renderId: string, consume = false): Promise<RenderPayload | null> {
    const key = renderKey(renderId);
    const payload = ((await this.session.get(key))[key] as
      | RenderPayload
      | undefined) ?? null;
    if (consume && payload) await this.session.remove(key);
    return payload;
  }

  async putUnsafe(
    unsafeId: string,
    payload: { ref: ConversationRef; text: string; createdAt: number }
  ): Promise<void> {
    await this.session.set({ [`${UNSAFE_PREFIX}${unsafeId}`]: payload });
  }

  async consumeUnsafe(
    unsafeId: string,
    ref: ConversationRef
  ): Promise<string | null> {
    const key = `${UNSAFE_PREFIX}${unsafeId}`;
    const payload = (await this.session.get(key))[key] as
      | { ref: ConversationRef; text: string; createdAt: number }
      | undefined;
    await this.session.remove(key);
    if (!payload) return null;
    if (
      payload.ref.site !== ref.site ||
      payload.ref.conversationId !== ref.conversationId ||
      Date.now() - payload.createdAt > 30_000
    ) {
      return null;
    }
    return payload.text;
  }
}

export function chromeStorage(): ExtensionStorage {
  return new ExtensionStorage(chrome.storage.local, chrome.storage.session);
}

export async function restrictStorageAccess(): Promise<void> {
  await Promise.all([
    chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" }),
    chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" })
  ]);
}
