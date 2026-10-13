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
  "#prompt-textarea",
  "textarea#mobile-composer-prompt",
  "textarea[data-mobile-composer-prompt]",
  "textarea[name='prompt'][aria-label]",
  "textarea[data-testid='prompt-textarea']",
  "div[contenteditable='true'][data-lexical-editor='true']",
  "form div[contenteditable='true']"
] as const;
const SEND_SELECTORS = [
  "button[data-testid='send-button']",
  "button[data-composer-submit]",
  "button[type='submit'][aria-label*='Send' i]",
  "button[type='submit'][aria-label*='Отправ' i]",
  "button[type='submit']"
] as const;

function firstRenderedComposer(document: Document): HTMLElement | null {
  const candidates = uniqueElements(document, COMPOSER_SELECTORS);
  return (
    candidates.find((element) => {
      if (element.hidden || element.getAttribute("aria-hidden") === "true") return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }) ??
    candidates.find((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true") ??
    candidates[0] ??
    null
  );
}

export class ChatGptAdapter implements SiteAdapter {
  readonly id = "chatgpt" as const;

  matches(url: URL): boolean {
    return url.origin === "https://chatgpt.com";
  }

  getConversationId(url: URL): string {
    const match = url.pathname.match(/(?:^|\/)c\/([^/?#]+)/i);
    return match?.[1] ? decodeURIComponent(match[1]) : "";
  }

  findComposer(document: Document): HTMLElement | null {
    return firstRenderedComposer(document);
  }

  findSendControl(document: Document): HTMLElement | null {
    const composer = this.findComposer(document);
    const form = composer?.closest("form");
    return firstElement(form ?? document, SEND_SELECTORS);
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
    const direct = uniqueElements(document, [
      "[data-message-author-role='assistant']",
      "article[data-turn='assistant']",
      "[data-testid^='conversation-turn-'] [data-message-author-role='assistant']"
    ]);
    return direct.map((element) =>
      element.closest<HTMLElement>("article, [data-testid^='conversation-turn-']") ?? element
    ).filter((element, index, all) => all.indexOf(element) === index);
  }

  getMessageBody(message: HTMLElement): HTMLElement {
    return firstElement(message, ["[data-message-author-role='assistant'] .markdown", ".markdown", ".prose"]) ?? message;
  }

  getMessageContent(message: HTMLElement): string {
    const body = this.getMessageBody(message);
    return body.innerText || body.textContent || "";
  }

  getMessageKey(message: HTMLElement, index: number): string {
    return (
      message.dataset.messageId ??
      message.dataset.turnId ??
      message.getAttribute("data-testid") ??
      `assistant-${index}`
    );
  }

  isStreaming(document: Document, message?: HTMLElement): boolean {
    if (message?.querySelector("[data-is-streaming='true']")) return true;
    return Boolean(
      firstElement(document, [
        "button[data-testid='stop-button']",
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
