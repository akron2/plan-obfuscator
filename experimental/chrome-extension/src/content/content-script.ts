import { adapterFor, type SiteAdapter } from "../adapters";
import type {
  ContentSettings,
  RuntimeResponse,
  SupportedSite
} from "../background/messages";
import { markerCount } from "../core/transform";
import type { ExtensionSettings, SendGuardMode } from "../core/types";

const FRAME_ID = "plan-obfuscator-secure-composer";
const LAUNCHER_ID = "plan-obfuscator-launcher";
const FALLBACK_FRAME_CLASS = "plan-obfuscator-fallback-frame";
const RESTORED_CLASS = "plan-obfuscator-restored-frame";
const NATIVE_CLASS = "plan-obfuscator-native-concealed";

interface FrameMessage {
  source?: string;
  type?: string;
  height?: number;
  renderId?: string;
  unsafeId?: string;
  autoSend?: boolean;
  protected?: boolean;
  dirty?: boolean;
  hasText?: boolean;
}

function siteEnabled(settings: ExtensionSettings, site: SupportedSite): boolean {
  return site === "chatgpt" ? settings.enabledChatGpt : settings.enabledClaude;
}

function sendMessage<T extends RuntimeResponse>(message: object): Promise<T> {
  return chrome.runtime.sendMessage(message) as Promise<T>;
}

export class ExtensionIntegration {
  private settings: ExtensionSettings | null = null;
  private composer: HTMLElement | null = null;
  private frame: HTMLIFrameElement | null = null;
  private launcher: HTMLButtonElement | null = null;
  private fallbackOpen = false;
  private observer: MutationObserver | null = null;
  private adapter: SiteAdapter;
  private conversationId = "";
  private lastUrl = location.href;
  private refreshQueued = false;
  private responseTimer: number | null = null;
  private navigationTimer: number | null = null;
  private protectedState = false;
  private dirtyState = false;
  private hasText = false;
  private allowNextSend = false;
  private processed = new Map<string, string>();
  private responseBaselineEstablished = false;
  private migrateProvisionalOnNavigation = false;

  constructor(adapter: SiteAdapter) {
    this.adapter = adapter;
    this.conversationId = adapter.getConversationId(new URL(location.href));
  }

  async start(): Promise<void> {
    const response = await sendMessage<RuntimeResponse>({ type: "GET_SETTINGS" });
    if (!response.ok || !("settings" in response)) return;
    this.settings = response.settings;
    this.installListeners();
    this.observer = this.adapter.watch(document, () => this.queueRefresh());
    this.queueRefresh();
    this.navigationTimer = window.setInterval(() => this.checkNavigation(), 1000);
  }

  stop(): void {
    this.observer?.disconnect();
    this.observer = null;
    if (this.responseTimer !== null) window.clearTimeout(this.responseTimer);
    if (this.navigationTimer !== null) window.clearInterval(this.navigationTimer);
    this.responseTimer = null;
    this.navigationTimer = null;
    this.unmountComposer();
    this.removeLauncher();
  }

  private installListeners(): void {
    window.addEventListener("message", (event) => void this.onFrameMessage(event));
    window.addEventListener("resize", () => this.queueRefresh(), { passive: true });
    window.addEventListener("scroll", () => this.positionFrame(), { passive: true, capture: true });
    document.addEventListener("click", (event) => this.guardSend(event), true);
    document.addEventListener("submit", (event) => this.guardSend(event), true);
    chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
      if (!message || typeof message !== "object" || !("type" in message)) return false;
      const typed = message as { type: string };
      if (typed.type === "GET_CONTEXT") {
        sendResponse({
          site: this.adapter.id,
          conversationId: this.conversationId,
          composerFound: Boolean(this.composer?.isConnected)
        });
        return false;
      }
      if (typed.type === "REFRESH_SETTINGS") {
        void this.reloadSettings();
      }
      return false;
    });
  }

  private async reloadSettings(): Promise<void> {
    const response = await sendMessage<RuntimeResponse>({ type: "GET_SETTINGS" });
    if (response.ok && "settings" in response) {
      this.settings = response.settings;
      this.queueRefresh();
    }
  }

  private checkNavigation(): void {
    if (location.href === this.lastUrl) return;
    const previous = this.conversationId;
    this.lastUrl = location.href;
    const current = this.adapter.getConversationId(new URL(location.href));
    if (current !== previous) {
      if (!previous && current && this.migrateProvisionalOnNavigation) {
        void sendMessage<RuntimeResponse>({
          type: "MIGRATE_CONVERSATION",
          site: this.adapter.id,
          fromConversationId: previous,
          conversationId: current
        });
      } else if (!previous || !current) {
        void sendMessage<RuntimeResponse>({
          type: "RESET_PROVISIONAL",
          site: this.adapter.id
        });
      }
      this.conversationId = current;
      this.migrateProvisionalOnNavigation = false;
      this.protectedState = false;
      this.dirtyState = false;
      this.hasText = false;
      this.allowNextSend = false;
      this.processed.clear();
      this.responseBaselineEstablished = false;
      this.configureFrame();
      this.postToComposer("RESET_CONVERSATION");
      if (this.composer) this.adapter.writeComposerText(this.composer, "");
    }
    this.queueRefresh();
  }

  private queueRefresh(): void {
    if (this.refreshQueued) return;
    this.refreshQueued = true;
    window.requestAnimationFrame(() => {
      this.refreshQueued = false;
      this.refresh();
    });
  }

  private refresh(): void {
    this.checkNavigation();
    if (!this.settings || !siteEnabled(this.settings, this.adapter.id)) {
      this.unmountComposer();
      this.removeLauncher();
      this.clearRestoredViews();
      return;
    }
    this.ensureLauncher();
    if (!this.settings.hideOriginal) {
      for (const element of document.querySelectorAll(".plan-obfuscator-original-hidden")) {
        element.classList.remove("plan-obfuscator-original-hidden");
      }
    }
    if (!this.settings.autoRestore) this.clearRestoredViews();
    const found = this.adapter.findComposer(document);
    if (found && this.composerIsUsable(found) && found !== this.composer) {
      this.attachComposer(found);
    }
    if (this.composer && (!this.composer.isConnected || !this.composerIsUsable(this.composer))) {
      this.restoreNativeComposer();
      if (!this.fallbackOpen) this.removeFrame();
    }
    if (this.composer?.isConnected) this.ensureFrame();
    this.updateLauncher();
    this.positionFrame();
    if (this.settings.autoRestore) this.scheduleResponseScan();
  }

  private composerIsUsable(composer: HTMLElement): boolean {
    const rect = composer.getBoundingClientRect();
    return rect.width >= 80 && rect.height >= 20;
  }

  private attachComposer(composer: HTMLElement): void {
    this.restoreNativeComposer();
    this.fallbackOpen = false;
    this.composer = composer;
    composer.classList.add(NATIVE_CLASS);
    composer.dataset.planObfuscatorOriginalTabindex = composer.getAttribute("tabindex") ?? "";
    composer.setAttribute("tabindex", "-1");
    this.ensureFrame();
    this.updateLauncher();
    this.positionFrame();
  }

  private ensureLauncher(): void {
    if (this.launcher?.isConnected) return;
    const launcher = document.createElement("button");
    launcher.id = LAUNCHER_ID;
    launcher.type = "button";
    launcher.textContent = "🛡 Обфусцировать";
    launcher.title = "Открыть защищённый редактор Plan Obfuscator";
    launcher.setAttribute("aria-controls", FRAME_ID);
    launcher.setAttribute("aria-expanded", "false");
    launcher.addEventListener("click", () => this.toggleFallback());
    (document.body ?? document.documentElement).append(launcher);
    this.launcher = launcher;
  }

  private updateLauncher(): void {
    if (!this.launcher) return;
    const composerAttached = Boolean(this.composer?.isConnected);
    const label = this.fallbackOpen ? "× Закрыть" : "🛡 Обфусцировать";
    const expanded = String(this.fallbackOpen);
    if (this.launcher.hidden !== composerAttached) this.launcher.hidden = composerAttached;
    if (this.launcher.textContent !== label) this.launcher.textContent = label;
    if (this.launcher.getAttribute("aria-expanded") !== expanded) {
      this.launcher.setAttribute("aria-expanded", expanded);
    }
  }

  private toggleFallback(): void {
    if (this.composer?.isConnected) {
      this.frame?.focus({ preventScroll: false });
      return;
    }
    this.fallbackOpen = !this.fallbackOpen;
    if (this.fallbackOpen) this.ensureFrame();
    else this.removeFrame();
    this.updateLauncher();
    this.positionFrame();
  }

  private removeLauncher(): void {
    this.launcher?.remove();
    this.launcher = null;
  }

  private ensureFrame(): void {
    if (this.frame?.isConnected) return;
    const frame = document.createElement("iframe");
    frame.id = FRAME_ID;
    frame.title = "Защищённый редактор Plan Obfuscator";
    frame.src = chrome.runtime.getURL("composer-frame.html");
    frame.allow = "clipboard-write";
    frame.addEventListener("load", () => this.configureFrame());
    (document.body ?? document.documentElement).append(frame);
    this.frame = frame;
  }

  private removeFrame(): void {
    this.frame?.remove();
    this.frame = null;
  }

  private configureFrame(): void {
    if (!this.frame?.contentWindow || !this.settings) return;
    const contentSettings: ContentSettings = {
      enabled: siteEnabled(this.settings, this.adapter.id),
      sendGuard: this.settings.sendGuard,
      autoRestore: this.settings.autoRestore,
      hideOriginal: this.settings.hideOriginal,
      restoreVisibleMessages: this.settings.restoreVisibleMessages
    };
    this.frame.contentWindow.postMessage(
      {
        source: "plan-obfuscator-content",
        type: "CONFIGURE",
        site: this.adapter.id,
        conversationId: this.conversationId,
        settings: contentSettings
      },
      "*"
    );
  }

  private positionFrame(): void {
    if (!this.frame) return;
    if (!this.composer?.isConnected) {
      if (!this.fallbackOpen) {
        this.frame.style.display = "none";
        return;
      }
      this.frame.classList.add(FALLBACK_FRAME_CLASS);
      this.frame.style.display = "block";
      this.frame.style.left = "auto";
      this.frame.style.top = "auto";
      this.frame.style.right = "16px";
      this.frame.style.bottom = "68px";
      this.frame.style.width = "min(680px, calc(100vw - 32px))";
      this.frame.style.height = "180px";
      return;
    }
    this.frame.classList.remove(FALLBACK_FRAME_CLASS);
    this.frame.style.right = "auto";
    this.frame.style.bottom = "auto";
    const rect = this.composer.getBoundingClientRect();
    if (rect.width < 80 || rect.height < 24) {
      this.frame.style.display = "none";
      return;
    }
    this.frame.style.display = "block";
    this.frame.style.left = `${Math.max(0, rect.left)}px`;
    this.frame.style.top = `${Math.max(0, rect.top)}px`;
    this.frame.style.width = `${rect.width}px`;
    this.frame.style.height = `${Math.max(72, rect.height)}px`;
  }

  private restoreNativeComposer(): void {
    if (!this.composer) return;
    this.composer.classList.remove(NATIVE_CLASS);
    const originalTab = this.composer.dataset.planObfuscatorOriginalTabindex;
    if (originalTab === "") this.composer.removeAttribute("tabindex");
    else if (originalTab !== undefined) this.composer.setAttribute("tabindex", originalTab);
    delete this.composer.dataset.planObfuscatorOriginalTabindex;
    this.composer = null;
  }

  private unmountComposer(): void {
    this.restoreNativeComposer();
    this.removeFrame();
    this.fallbackOpen = false;
  }

  private clearRestoredViews(): void {
    for (const frame of document.querySelectorAll(`.${RESTORED_CLASS}`)) frame.remove();
    for (const element of document.querySelectorAll(".plan-obfuscator-original-hidden")) {
      element.classList.remove("plan-obfuscator-original-hidden");
    }
    this.processed.clear();
  }

  private postToComposer(type: string, extra: Record<string, unknown> = {}): void {
    this.frame?.contentWindow?.postMessage(
      { source: "plan-obfuscator-content", type, ...extra },
      "*"
    );
  }

  private async onFrameMessage(event: MessageEvent): Promise<void> {
    const message = event.data as FrameMessage;
    if (!message || message.source !== "plan-obfuscator-frame") return;
    if (event.source === this.frame?.contentWindow) {
      if (message.type === "FRAME_READY") this.configureFrame();
      if (message.type === "STATE") {
        this.protectedState = Boolean(message.protected);
        this.dirtyState = Boolean(message.dirty);
        this.hasText = Boolean(message.hasText);
        if (!this.protectedState && this.composer) this.adapter.writeComposerText(this.composer, "");
      }
      if (message.type === "SAFE_TEXT" && typeof (event.data as { text?: unknown }).text === "string") {
        const text = (event.data as { text: string }).text;
        this.protectedState = true;
        this.dirtyState = false;
        if (this.composer) this.adapter.writeComposerText(this.composer, text);
      }
      if (message.type === "UNSAFE_READY" && message.unsafeId) {
        const response = await sendMessage<RuntimeResponse>({
          type: "CONSUME_UNSAFE_OVERRIDE",
          site: this.adapter.id,
          conversationId: this.conversationId,
          unsafeId: message.unsafeId
        });
        if (!response.ok || !("unsafeText" in response) || response.unsafeText === null) return;
        if (this.composer) this.adapter.writeComposerText(this.composer, response.unsafeText);
        this.allowNextSend = true;
        window.setTimeout(() => {
          this.allowNextSend = false;
        }, 15_000);
        if (message.autoSend) this.adapter.findSendControl(document)?.click();
      }
      if (message.type === "SEND_GESTURE") this.requestSendFromFrame();
      return;
    }

    if (message.type === "RESTORED_HEIGHT" && message.renderId && message.height) {
      const frame = [...document.querySelectorAll<HTMLIFrameElement>(`.${RESTORED_CLASS}`)]
        .find((item) => item.contentWindow === event.source && item.dataset.renderId === message.renderId);
      if (frame) frame.style.height = `${Math.min(Math.max(message.height, 64), 4000)}px`;
    }
  }

  private stopEvent(event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  private requestSendFromFrame(): void {
    if (!this.settings) return;
    if (this.protectedState) {
      if (!this.conversationId) this.migrateProvisionalOnNavigation = true;
      this.adapter.findSendControl(document)?.click();
    } else if (this.settings.sendGuard === "off") {
      this.postToComposer("REQUEST_UNSAFE", { autoSend: true });
    } else {
      this.postToComposer("GUARD_BLOCKED", { mode: this.settings.sendGuard });
    }
  }

  private guardSend(event: Event): void {
    if (!this.settings || !this.adapter.isSendingGesture(event, document)) return;
    if (this.allowNextSend) {
      this.allowNextSend = false;
      window.setTimeout(() => this.postToComposer("RESET_AFTER_SEND"), 500);
      return;
    }
    const nativeText = this.composer ? this.adapter.readComposerText(this.composer) : "";
    if (this.protectedState && nativeText.length > 0) {
      if (!this.conversationId) this.migrateProvisionalOnNavigation = true;
      window.setTimeout(() => this.postToComposer("RESET_AFTER_SEND"), 500);
      return;
    }
    if (!this.hasText && !nativeText.trim()) return;
    this.stopEvent(event);
    const mode: SendGuardMode = this.settings.sendGuard;
    if (mode === "off") {
      this.postToComposer("REQUEST_UNSAFE", { autoSend: true });
    } else {
      this.postToComposer("GUARD_BLOCKED", { mode });
    }
  }

  private scheduleResponseScan(): void {
    if (this.responseTimer !== null) window.clearTimeout(this.responseTimer);
    this.responseTimer = window.setTimeout(() => {
      this.responseTimer = null;
      void this.scanResponses();
    }, 650);
  }

  private async scanResponses(): Promise<void> {
    if (!this.settings?.autoRestore || this.adapter.isStreaming(document)) return;
    const messages = this.adapter.listAssistantMessages(document);
    if (!this.settings.restoreVisibleMessages && !this.responseBaselineEstablished) {
      messages.forEach((message, index) => {
        this.processed.set(
          this.adapter.getMessageKey(message, index),
          this.adapter.getMessageContent(message)
        );
      });
      this.responseBaselineEstablished = true;
      return;
    }
    for (let index = 0; index < messages.length; index += 1) {
      const message = messages[index]!;
      if (this.adapter.isStreaming(document, message)) continue;
      const raw = this.adapter.getMessageContent(message);
      if (markerCount(raw) === 0) continue;
      const key = this.adapter.getMessageKey(message, index);
      if (this.processed.get(key) === raw) {
        this.keepOriginalHidden(message);
        continue;
      }
      const response = await sendMessage<RuntimeResponse>({
        type: "PREPARE_RESTORE",
        site: this.adapter.id,
        conversationId: this.conversationId,
        rawText: raw
      });
      if (!response.ok || !("renderId" in response) || !response.renderId) continue;
      this.installRestoredFrame(message, key, response.renderId);
      this.processed.set(key, raw);
    }
  }

  private keepOriginalHidden(message: HTMLElement): void {
    if (!this.settings?.hideOriginal) return;
    this.adapter.getMessageBody(message).classList.add("plan-obfuscator-original-hidden");
  }

  private installRestoredFrame(message: HTMLElement, key: string, renderId: string): void {
    const body = this.adapter.getMessageBody(message);
    const existing = document.querySelector<HTMLIFrameElement>(
      `.${RESTORED_CLASS}[data-message-key="${CSS.escape(key)}"]`
    );
    existing?.remove();
    const frame = document.createElement("iframe");
    frame.className = RESTORED_CLASS;
    frame.dataset.messageKey = key;
    frame.dataset.renderId = renderId;
    frame.title = "Восстановленный ответ Plan Obfuscator";
    frame.src = `${chrome.runtime.getURL("restored-frame.html")}#${encodeURIComponent(renderId)}`;
    body.insertAdjacentElement("afterend", frame);
    if (this.settings?.hideOriginal) body.classList.add("plan-obfuscator-original-hidden");
  }
}

const adapter = adapterFor(new URL(location.href));
export const activeIntegration = adapter ? new ExtensionIntegration(adapter) : null;
if (activeIntegration) void activeIntegration.start();
