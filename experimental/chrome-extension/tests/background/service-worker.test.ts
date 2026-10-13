import { beforeAll, describe, expect, it, vi } from "vitest";
import type { RuntimeResponse } from "../../src/background/messages";

class ChromeArea {
  values: Record<string, unknown> = {};
  async get(keys?: string | string[] | null): Promise<Record<string, unknown>> {
    if (keys == null) return { ...this.values };
    const selected = typeof keys === "string" ? [keys] : keys;
    return Object.fromEntries(selected.filter((key) => key in this.values).map((key) => [key, this.values[key]]));
  }
  async set(items: Record<string, unknown>): Promise<void> { Object.assign(this.values, items); }
  async remove(keys: string | string[]): Promise<void> {
    for (const key of typeof keys === "string" ? [keys] : keys) delete this.values[key];
  }
  setAccessLevel = vi.fn(async () => undefined);
}

type MessageListener = (
  message: unknown,
  sender: chrome.runtime.MessageSender,
  sendResponse: (response: RuntimeResponse) => void
) => boolean | undefined;

let messageListener: MessageListener;
let installedListener: (() => void) | undefined;
let startupListener: (() => void) | undefined;
const local = new ChromeArea();
const session = new ChromeArea();

beforeAll(async () => {
  vi.stubGlobal("chrome", {
    storage: { local, session },
    runtime: {
      getURL: (path: string) => `chrome-extension://test/${path}`,
      onInstalled: { addListener: (listener: () => void) => { installedListener = listener; } },
      onStartup: { addListener: (listener: () => void) => { startupListener = listener; } },
      onMessage: { addListener: (listener: MessageListener) => { messageListener = listener; } }
    }
  });
  await import("../../src/background/service-worker");
});

function request(
  message: object,
  sender: chrome.runtime.MessageSender = { url: "chrome-extension://test/composer-frame.html", tab: { id: 7 } as chrome.tabs.Tab }
): Promise<RuntimeResponse> {
  return new Promise((resolve, reject) => {
    const handled = messageListener(message, sender, resolve);
    if (!handled) reject(new Error("Message was not handled"));
  });
}

describe("service worker protocol", () => {
  it("initializes restricted storage and settings", async () => {
    installedListener?.();
    startupListener?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(local.setAccessLevel).toHaveBeenCalled();
    expect(session.setAccessLevel).toHaveBeenCalled();
    const settings = await request({ type: "GET_SETTINGS" });
    expect(settings.ok && "settings" in settings && settings.settings.sendGuard).toBe("block");
  });

  it("obfuscates, persists, restores and reports mapping stats", async () => {
    const obfuscated = await request({
      type: "OBFUSCATE",
      site: "chatgpt",
      conversationId: "",
      text: "select secret_col from secret_table where status = 'PRIVATE'"
    });
    expect(obfuscated.ok && "obfuscatedText" in obfuscated && obfuscated.obfuscatedText).toContain("OBF_");
    const stats = await request({ type: "GET_MAPPING_STATS", site: "chatgpt", conversationId: "tab:7" });
    expect(stats.ok && "symbolCount" in stats && stats.symbolCount).toBeGreaterThan(0);

    const raw = obfuscated.ok && "obfuscatedText" in obfuscated ? `Try ${obfuscated.obfuscatedText}` : "";
    const restored = await request(
      { type: "PREPARE_RESTORE", site: "chatgpt", conversationId: "tab:7", rawText: raw },
      { url: "https://chatgpt.com/c/test", tab: { id: 7 } as chrome.tabs.Tab }
    );
    expect(restored.ok && "renderId" in restored && restored.renderId).toBeTruthy();
    const render = await request({
      type: "GET_RENDER",
      renderId: restored.ok && "renderId" in restored ? restored.renderId : ""
    });
    expect(render.ok && "render" in render && render.render?.restoredText).toContain("secret_table");
  });

  it("uses one-time unsafe override storage without page postMessage plaintext", async () => {
    const stored = await request({
      type: "STORE_UNSAFE_OVERRIDE",
      site: "claude",
      conversationId: "chat-1",
      text: "plaintext"
    });
    expect(stored.ok && "unsafeId" in stored && stored.unsafeId).toBeTruthy();
    const unsafeId = stored.ok && "unsafeId" in stored ? stored.unsafeId : "";
    const consumed = await request(
      { type: "CONSUME_UNSAFE_OVERRIDE", site: "claude", conversationId: "chat-1", unsafeId },
      { url: "https://claude.ai/chat/chat-1", tab: { id: 9 } as chrome.tabs.Tab }
    );
    expect(consumed.ok && "unsafeText" in consumed && consumed.unsafeText).toBe("plaintext");
    const second = await request(
      { type: "CONSUME_UNSAFE_OVERRIDE", site: "claude", conversationId: "chat-1", unsafeId },
      { url: "https://claude.ai/chat/chat-1", tab: { id: 9 } as chrome.tabs.Tab }
    );
    expect(second.ok && "unsafeText" in second && second.unsafeText).toBeNull();
  });

  it("migrates provisional mapping and clears it", async () => {
    const migrated = await request(
      {
        type: "MIGRATE_CONVERSATION",
        site: "chatgpt",
        fromConversationId: "",
        conversationId: "real-id"
      },
      { url: "https://chatgpt.com/c/real-id", tab: { id: 7 } as chrome.tabs.Tab }
    );
    expect(migrated.ok && "migrated" in migrated && migrated.migrated).toBe(true);
    const cleared = await request({ type: "CLEAR_MAPPING", site: "chatgpt", conversationId: "real-id" });
    expect(cleared.ok && "cleared" in cleared && cleared.cleared).toBe(1);
  });

  it("serializes concurrent mapping updates", async () => {
    const [first, second] = await Promise.all([
      request({
        type: "OBFUSCATE",
        site: "chatgpt",
        conversationId: "concurrent",
        text: "select alpha from table_one"
      }),
      request({
        type: "OBFUSCATE",
        site: "chatgpt",
        conversationId: "concurrent",
        text: "select beta from table_two"
      })
    ]);
    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    const stats = await request({
      type: "GET_MAPPING_STATS",
      site: "chatgpt",
      conversationId: "concurrent"
    });
    expect(stats.ok && "symbolCount" in stats && stats.symbolCount).toBeGreaterThanOrEqual(4);
  });

  it("discards only the current tab's abandoned provisional mapping", async () => {
    const internal = { url: "chrome-extension://test/composer-frame.html", tab: { id: 11 } as chrome.tabs.Tab };
    await request({
      type: "OBFUSCATE",
      site: "claude",
      conversationId: "",
      text: "select abandoned_secret from abandoned_table"
    }, internal);
    const reset = await request(
      { type: "RESET_PROVISIONAL", site: "claude" },
      { url: "https://claude.ai/new", tab: { id: 11 } as chrome.tabs.Tab }
    );
    expect(reset.ok && "cleared" in reset && reset.cleared).toBe(1);
    const stats = await request({
      type: "GET_MAPPING_STATS",
      site: "claude",
      conversationId: "tab:11"
    });
    expect(stats.ok && "symbolCount" in stats && stats.symbolCount).toBe(0);
  });

  it("enforces sender boundaries and validates messages", async () => {
    const forbidden = await request(
      { type: "OBFUSCATE", site: "chatgpt", conversationId: "x", text: "select a from b" },
      { url: "https://chatgpt.com/c/x", tab: { id: 1 } as chrome.tabs.Tab }
    );
    expect(forbidden).toMatchObject({ ok: false, code: "forbidden" });
    const untrustedRestore = await request(
      { type: "PREPARE_RESTORE", site: "chatgpt", conversationId: "x", rawText: "OBF_X" },
      { url: "https://evil.example/", tab: { id: 1 } as chrome.tabs.Tab }
    );
    expect(untrustedRestore).toMatchObject({ ok: false, code: "forbidden" });
    const invalid = await request({ type: "OBFUSCATE", site: "invalid", conversationId: "x", text: "x" });
    expect(invalid.ok).toBe(false);
    await expect(request({ hello: "world" })).rejects.toThrow("not handled");
  });

  it("updates settings and clears all mappings", async () => {
    const updated = await request({ type: "SET_SETTINGS", settings: { sendGuard: "warn", rogue: true } });
    expect(updated.ok && "settings" in updated && updated.settings.sendGuard).toBe("warn");
    const cleared = await request({ type: "CLEAR_ALL_MAPPINGS" });
    expect(cleared.ok && "cleared" in cleared).toBe(true);
  });
});
