// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from "vitest";

const sendMessage = vi.fn(async (message: { type?: string }) => {
  if (message.type === "OBFUSCATE") {
    return {
      ok: true,
      obfuscatedText: "SELECT OBF_COL_0001_00 FROM OBF_TBL_0002_00",
      replacementCount: 2,
      validationStatus: "ok",
      findings: []
    };
  }
  return { ok: false, code: "unexpected", error: String(message.type) };
});
const writeText = vi.fn(async () => undefined);

beforeAll(async () => {
  document.body.innerHTML = `
    <main id="composer">
      <textarea id="prompt"></textarea>
      <span id="status-text"></span>
      <button id="copy-safe" class="hidden" type="button">Копировать OBF</button>
      <button id="unsafe" class="hidden" type="button">Отправить без защиты</button>
      <button id="protect" type="button">Обфусцировать</button>
    </main>`;
  vi.stubGlobal("chrome", { runtime: { sendMessage } });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText }
  });
  await import("../../src/frames/composer-frame");
  window.dispatchEvent(new MessageEvent("message", {
    source: window,
    data: {
      source: "plan-obfuscator-content",
      type: "CONFIGURE",
      site: "chatgpt",
      conversationId: "conversation-1",
      settings: {
        enabled: true,
        sendGuard: "block",
        autoRestore: true,
        hideOriginal: true,
        restoreVisibleMessages: true
      }
    }
  }));
});

describe("secure composer frame", () => {
  it("copies only the obfuscated result for fallback use", async () => {
    const prompt = document.querySelector<HTMLTextAreaElement>("#prompt")!;
    const protect = document.querySelector<HTMLButtonElement>("#protect")!;
    const copy = document.querySelector<HTMLButtonElement>("#copy-safe")!;
    prompt.value = "SELECT private_column FROM private_table";
    prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));

    protect.click();
    await vi.waitFor(() => expect(copy.classList.contains("hidden")).toBe(false));
    copy.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledOnce());

    expect(writeText).toHaveBeenCalledWith("SELECT OBF_COL_0001_00 FROM OBF_TBL_0002_00");
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining("private_"));
  });
});
