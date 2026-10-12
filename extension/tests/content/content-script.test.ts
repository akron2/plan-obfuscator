/**
 * @vitest-environment jsdom
 * @vitest-environment-options {"url":"https://chatgpt.com/c/conversation-1"}
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS } from "../../src/core/types";
import type { ExtensionIntegration } from "../../src/content/content-script";

type ContentListener = (
  message: unknown,
  sender: chrome.runtime.MessageSender,
  sendResponse: (response: unknown) => void
) => boolean | undefined;

let contentListener: ContentListener | undefined;
let integration: ExtensionIntegration | null = null;

beforeAll(async () => {
  document.body.innerHTML = `
    <form>
      <div id="prompt-textarea" contenteditable="true"></div>
      <button type="button" data-testid="send-button"><span>Send</span></button>
    </form>`;
  const composer = document.querySelector<HTMLElement>("#prompt-textarea")!;
  composer.getBoundingClientRect = () => ({
    x: 100,
    y: 500,
    left: 100,
    top: 500,
    right: 800,
    bottom: 590,
    width: 700,
    height: 90,
    toJSON: () => ({})
  });
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("chrome", {
    runtime: {
      getURL: (path: string) => `chrome-extension://test/${path}`,
      sendMessage: vi.fn(async (message: { type: string }) => {
        if (message.type === "GET_SETTINGS") return { ok: true, settings: DEFAULT_SETTINGS };
        return { ok: false, code: "unexpected", error: message.type };
      }),
      onMessage: {
        addListener: (listener: ContentListener) => {
          contentListener = listener;
        }
      }
    }
  });
  const module = await import("../../src/content/content-script");
  integration = module.activeIntegration;
  await new Promise((resolve) => setTimeout(resolve, 0));
});

afterAll(() => {
  integration?.stop();
});

describe("content integration shell", () => {
  it("mounts an extension-origin composer over the native editor", () => {
    const composer = document.querySelector<HTMLElement>("#prompt-textarea")!;
    const frame = document.querySelector<HTMLIFrameElement>("#plan-obfuscator-secure-composer")!;
    expect(frame).toBeTruthy();
    expect(frame.src).toBe("chrome-extension://test/composer-frame.html");
    expect(frame.style.width).toBe("700px");
    expect(composer.classList.contains("plan-obfuscator-native-concealed")).toBe(true);
    expect(composer.getAttribute("tabindex")).toBe("-1");
  });

  it("writes only the safe frame payload and clears it when the draft becomes dirty", () => {
    const composer = document.querySelector<HTMLElement>("#prompt-textarea")!;
    const frame = document.querySelector<HTMLIFrameElement>("#plan-obfuscator-secure-composer")!;
    window.dispatchEvent(new MessageEvent("message", {
      source: frame.contentWindow,
      data: {
        source: "plan-obfuscator-frame",
        type: "SAFE_TEXT",
        text: "SELECT OBF_TEST_COL_0001_00 FROM OBF_TEST_TBL_0002_00"
      }
    }));
    expect(composer.textContent).toContain("OBF_TEST");
    expect(composer.textContent).not.toContain("secret");
    const allowed = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button span")!.dispatchEvent(allowed);
    expect(allowed.defaultPrevented).toBe(false);

    window.dispatchEvent(new MessageEvent("message", {
      source: frame.contentWindow,
      data: {
        source: "plan-obfuscator-frame",
        type: "STATE",
        protected: false,
        dirty: true,
        hasText: true
      }
    }));
    expect(composer.textContent).toBe("");
    const blocked = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button span")!.dispatchEvent(blocked);
    expect(blocked.defaultPrevented).toBe(true);
  });

  it("reports adapter diagnostics to the popup", () => {
    let response: unknown;
    contentListener?.({ type: "GET_CONTEXT" }, {}, (value) => {
      response = value;
    });
    expect(response).toEqual({
      site: "chatgpt",
      conversationId: "conversation-1",
      composerFound: true
    });
  });
});
