import type { SupportedSite } from "../background/messages";

export interface SiteAdapter {
  readonly id: SupportedSite;
  matches(url: URL): boolean;
  getConversationId(url: URL): string;
  findComposer(document: Document): HTMLElement | null;
  findSendControl(document: Document): HTMLElement | null;
  writeComposerText(composer: HTMLElement, text: string): void;
  readComposerText(composer: HTMLElement): string;
  isSendingGesture(event: Event, document: Document): boolean;
  listAssistantMessages(document: Document): HTMLElement[];
  getMessageBody(message: HTMLElement): HTMLElement;
  getMessageContent(message: HTMLElement): string;
  getMessageKey(message: HTMLElement, index: number): string;
  isStreaming(document: Document, message?: HTMLElement): boolean;
  watch(document: Document, onChange: () => void): MutationObserver;
}

export function firstElement(
  root: ParentNode,
  selectors: readonly string[]
): HTMLElement | null {
  for (const selector of selectors) {
    const element = root.querySelector<HTMLElement>(selector);
    if (element) return element;
  }
  return null;
}

export function uniqueElements(
  root: ParentNode,
  selectors: readonly string[]
): HTMLElement[] {
  const found = new Set<HTMLElement>();
  for (const selector of selectors) {
    for (const element of root.querySelectorAll<HTMLElement>(selector)) found.add(element);
  }
  return [...found];
}

export function setEditableText(element: HTMLElement, text: string): void {
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
    const prototype = element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    setter?.call(element, text);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }
  element.focus({ preventScroll: true });
  let inserted = false;
  if (typeof document.execCommand === "function") {
    const selection = document.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection?.removeAllRanges();
    selection?.addRange(range);
    inserted = document.execCommand("insertText", false, text);
    selection?.removeAllRanges();
  }
  if (!inserted || readEditableText(element) !== text) {
    element.replaceChildren(document.createTextNode(text));
  }
  element.dispatchEvent(
    new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: text
    })
  );
}

export function readEditableText(element: HTMLElement): string {
  return element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement
    ? element.value
    : element.innerText || element.textContent || "";
}

export function defaultWatch(document: Document, onChange: () => void): MutationObserver {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["disabled", "aria-disabled", "data-is-streaming"]
  });
  return observer;
}

export function clickTargets(event: Event, element: HTMLElement | null): boolean {
  if (!element || !(event.target instanceof Node)) return false;
  return element === event.target || element.contains(event.target);
}

export function submitTargets(event: Event, composer: HTMLElement | null): boolean {
  if (event.type !== "submit" || !composer || !(event.target instanceof HTMLElement)) return false;
  return event.target.contains(composer);
}
