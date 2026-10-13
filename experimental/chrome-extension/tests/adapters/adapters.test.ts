// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { adapterFor, ChatGptAdapter, ClaudeAdapter } from "../../src/adapters";

describe("site adapters", () => {
  it("selects adapters and conversation IDs", () => {
    expect(adapterFor(new URL("https://chatgpt.com/c/abc"))?.id).toBe("chatgpt");
    expect(adapterFor(new URL("https://claude.ai/chat/xyz"))?.id).toBe("claude");
    expect(adapterFor(new URL("https://example.com"))).toBeNull();
    expect(new ChatGptAdapter().getConversationId(new URL("https://chatgpt.com/g/g-id/c/a%20b"))).toBe("a b");
    expect(new ClaudeAdapter().getConversationId(new URL("https://claude.ai/new"))).toBe("");
  });

  it("drives a ChatGPT DOM fixture", () => {
    document.body.innerHTML = `
      <form><div id="prompt-textarea" contenteditable="true"></div><button type="button" data-testid="send-button"><span>Send</span></button></form>
      <article data-turn-id="turn-1"><div data-message-author-role="assistant"><div class="markdown">Answer OBF_A</div></div></article>`;
    const adapter = new ChatGptAdapter();
    const composer = adapter.findComposer(document)!;
    let inputs = 0;
    composer.addEventListener("input", () => inputs++);
    adapter.writeComposerText(composer, "safe text");
    expect(adapter.readComposerText(composer)).toBe("safe text");
    expect(inputs).toBe(1);
    const event = new MouseEvent("click", { bubbles: true });
    document.querySelector("button span")!.dispatchEvent(event);
    expect(adapter.isSendingGesture(event, document)).toBe(true);
    const submit = new Event("submit", { bubbles: true, cancelable: true });
    document.querySelector("form")!.dispatchEvent(submit);
    expect(adapter.isSendingGesture(submit, document)).toBe(true);
    const messages = adapter.listAssistantMessages(document);
    expect(messages).toHaveLength(1);
    expect(adapter.getMessageContent(messages[0]!)).toContain("Answer OBF_A");
    expect(adapter.getMessageKey(messages[0]!, 0)).toBe("turn-1");
    expect(adapter.isStreaming(document)).toBe(false);
    document.body.insertAdjacentHTML("beforeend", `<button data-testid="stop-button">Stop</button>`);
    expect(adapter.isStreaming(document)).toBe(true);
  });

  it("supports the current ChatGPT mobile composer DOM and prefers the rendered editor", () => {
    document.body.innerHTML = `
      <form data-desktop-composer hidden>
        <div id="prompt-textarea" contenteditable="true"></div>
        <button type="submit" data-testid="send-button">Send</button>
      </form>
      <form data-mobile-composer>
        <textarea
          id="mobile-composer-prompt"
          data-mobile-composer-prompt
          name="prompt"
          aria-label="Чат с ChatGPT"
        ></textarea>
        <button type="submit" data-composer-submit aria-label="Отправить сообщение"></button>
      </form>`;
    const mobile = document.querySelector<HTMLTextAreaElement>("#mobile-composer-prompt")!;
    mobile.getBoundingClientRect = () => ({
      x: 20,
      y: 400,
      left: 20,
      top: 400,
      right: 620,
      bottom: 424,
      width: 600,
      height: 24,
      toJSON: () => ({})
    });

    const adapter = new ChatGptAdapter();
    expect(adapter.findComposer(document)).toBe(mobile);
    expect(adapter.findSendControl(document)?.hasAttribute("data-composer-submit")).toBe(true);
    adapter.writeComposerText(mobile, "safe mobile text");
    expect(mobile.value).toBe("safe mobile text");
  });

  it("drives a Claude DOM fixture and textarea fallback", () => {
    document.body.innerHTML = `
      <fieldset><div class="ProseMirror" contenteditable="true" role="textbox"></div></fieldset>
      <button aria-label="Send message">Send</button>
      <article id="answer"><div class="font-claude-response">Claude answer</div></article>`;
    const adapter = new ClaudeAdapter();
    const composer = adapter.findComposer(document)!;
    adapter.writeComposerText(composer, "protected");
    expect(adapter.readComposerText(composer)).toBe("protected");
    expect(adapter.findSendControl(document)?.textContent).toBe("Send");
    const messages = adapter.listAssistantMessages(document);
    expect(messages).toHaveLength(1);
    expect(adapter.getMessageKey(messages[0]!, 0)).toBe("answer");

    document.body.innerHTML = `<textarea data-testid="prompt-textarea"></textarea>`;
    const chatgpt = new ChatGptAdapter();
    const textarea = chatgpt.findComposer(document)!;
    chatgpt.writeComposerText(textarea, "textarea value");
    expect(chatgpt.readComposerText(textarea)).toBe("textarea value");
  });

  it("watches mutations", async () => {
    document.body.innerHTML = `<main></main>`;
    const callback = vi.fn();
    const observer = new ChatGptAdapter().watch(document, callback);
    document.querySelector("main")!.append(document.createElement("div"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(callback).toHaveBeenCalled();
    observer.disconnect();
  });
});
