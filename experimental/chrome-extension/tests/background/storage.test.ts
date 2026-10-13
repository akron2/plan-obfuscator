import { describe, expect, it } from "vitest";
import { createConversationMapping } from "../../src/core/symbol-registry";
import { DEFAULT_SETTINGS, EntityType } from "../../src/core/types";
import { ExtensionStorage, type StorageAreaLike } from "../../src/background/storage";

class MemoryArea implements StorageAreaLike {
  values: Record<string, unknown> = {};

  async get(keys?: string | string[] | null): Promise<Record<string, unknown>> {
    if (keys == null) return { ...this.values };
    const requested = typeof keys === "string" ? [keys] : keys;
    return Object.fromEntries(requested.filter((key) => key in this.values).map((key) => [key, this.values[key]]));
  }

  async set(items: Record<string, unknown>): Promise<void> {
    Object.assign(this.values, items);
  }

  async remove(keys: string | string[]): Promise<void> {
    for (const key of typeof keys === "string" ? [keys] : keys) delete this.values[key];
  }
}

describe("extension storage", () => {
  it("merges defaults and persists settings", async () => {
    const store = new ExtensionStorage(new MemoryArea(), new MemoryArea());
    expect(await store.getSettings()).toEqual(DEFAULT_SETTINGS);
    const changed = await store.setSettings({ sendGuard: "warn", autoRestore: false });
    expect(changed.sendGuard).toBe("warn");
    expect((await store.getSettings()).enabledChatGpt).toBe(true);
  });

  it("creates, saves, migrates, merges and clears mappings", async () => {
    const local = new MemoryArea();
    const store = new ExtensionStorage(local, new MemoryArea());
    const provisional = await store.getOrCreateMapping({ site: "chatgpt", conversationId: "tab:7" });
    provisional.symbols.push({
      entityType: EntityType.Table,
      canonical: "U:ORDERS",
      preferredOriginal: "orders",
      marker: "OBF_AAAA_TBL_0001_00",
      sequence: 1
    });
    provisional.nextSequence = 2;
    await store.saveMapping(provisional);
    expect(await store.migrateMapping(
      { site: "chatgpt", conversationId: "tab:7" },
      { site: "chatgpt", conversationId: "real" }
    )).toBe(true);
    expect((await store.getMapping({ site: "chatgpt", conversationId: "real" }))?.symbols).toHaveLength(1);
    expect(await store.getMapping({ site: "chatgpt", conversationId: "tab:7" })).toBeNull();

    const another = createConversationMapping("chatgpt", "other", "BBBB");
    another.symbols.push({
      entityType: EntityType.Column,
      canonical: "U:ID",
      preferredOriginal: "id",
      marker: "OBF_BBBB_COL_0001_00",
      sequence: 1
    });
    await store.saveMapping(another);
    expect(await store.migrateMapping(
      { site: "chatgpt", conversationId: "other" },
      { site: "chatgpt", conversationId: "real" }
    )).toBe(true);
    expect((await store.getMapping({ site: "chatgpt", conversationId: "real" }))?.symbols).toHaveLength(2);
    expect(await store.migrateMapping(
      { site: "claude", conversationId: "x" },
      { site: "chatgpt", conversationId: "x" }
    )).toBe(false);
    expect(await store.clearMapping({ site: "chatgpt", conversationId: "missing" })).toBe(0);
    expect(await store.clearAllMappings()).toBe(1);
  });

  it("stores render payloads and consumes one-time unsafe text", async () => {
    const store = new ExtensionStorage(new MemoryArea(), new MemoryArea());
    const ref = { site: "claude" as const, conversationId: "c1" };
    const render = {
      renderId: "r1",
      ...ref,
      rawText: "OBF",
      restoredText: "secret",
      replacementCount: 1,
      unknownMarkers: [],
      createdAt: new Date().toISOString()
    };
    await store.putRender(render);
    expect(await store.getRender("r1")).toEqual(render);
    expect(await store.getRender("missing")).toBeNull();

    await store.putUnsafe("u1", { ref, text: "secret", createdAt: Date.now() });
    expect(await store.consumeUnsafe("u1", ref)).toBe("secret");
    expect(await store.consumeUnsafe("u1", ref)).toBeNull();
    await store.putUnsafe("u2", { ref, text: "secret", createdAt: Date.now() - 31_000 });
    expect(await store.consumeUnsafe("u2", ref)).toBeNull();
  });
});
