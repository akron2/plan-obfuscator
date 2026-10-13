import {
  clickTargets,
  defaultWatch,
  firstElement,
  readEditableText,
  setEditableText,
  submitTargets,
  uniqueElements
} from "./site-adapter";
import type { SiteAdapter } from "./site-adapter";

const COMPOSER_SELECTORS = [
  "div[contenteditable='true'].ProseMirror",
  "div[contenteditable='true'][data-testid*='input']",
  "fieldset div[contenteditable='true']",
  "div[contenteditable='true'][role='textbox']"
] as const;
const SEND_SELECTORS = [
  "button[data-testid='send-button']",
  "button[aria-label*='Send' i]",
  "button[aria-label*='Отправ' i]"
] as const;

export class ClaudeAdapter implements SiteAdapter {
  readonly id = "claude" as const;

  matches(url: URL): boolean {
    return url.origin === "https://claude.ai";
  }

  getConversationId(url: URL): string {
    const match = url.pathname.match(/(?:^|\/)chat\/([^/?#]+)/i);
    return match?.[1] ? decodeURIComponent(match[1]) : "";
  }

  findComposer(document: Document): HTMLElement | null {
    return firstElement(document, COMPOSER_SELECTORS);
  }

  findSendControl(document: Document): HTMLElement | null {
    return firstElement(document, SEND_SELECTORS);
  }

  writeComposerText(composer: HTMLElement, text: string): void {
    setEditableText(composer, text);
  }

  readComposerText(composer: HTMLElement): string {
    return readEditableText(composer);
  }

  isSendingGesture(event: Event, document: Document): boolean {
    return (
      (event.type === "click" && clickTargets(event, this.findSendControl(document))) ||
      submitTargets(event, this.findComposer(document))
    );
  }

  listAssistantMessages(document: Document): HTMLElement[] {
    return uniqueElements(document, [
      "[data-testid='assistant-message']",
      "[data-is-streaming] .font-claude-response",
      ".font-claude-response"
    ]).map((element) =>
      element.closest<HTMLElement>("[data-testid='assistant-message'], [data-is-streaming], article") ?? element
    ).filter((element, index, all) => all.indexOf(element) === index);
  }

  getMessageBody(message: HTMLElement): HTMLElement {
    return firstElement(message, [".font-claude-response", ".prose", "[data-testid='assistant-message-content']"]) ?? message;
  }

  getMessageContent(message: HTMLElement): string {
    const body = this.getMessageBody(message);
    return body.innerText || body.textContent || "";
  }

  getMessageKey(message: HTMLElement, index: number): string {
    return (
      message.dataset.messageId ??
      message.getAttribute("data-testid") ??
      (message.id || `assistant-${index}`)
    );
  }

  isStreaming(document: Document, message?: HTMLElement): boolean {
    if (message?.matches("[data-is-streaming='true']") || message?.querySelector("[data-is-streaming='true']")) {
      return true;
    }
    return Boolean(
      firstElement(document, [
        "button[aria-label*='Stop' i]",
        "button[aria-label*='Останов' i]",
        "[data-is-streaming='true']"
      ])
    );
  }

  watch(document: Document, onChange: () => void): MutationObserver {
    return defaultWatch(document, onChange);
  }
}
