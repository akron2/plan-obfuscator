import type { RuntimeResponse, SupportedSite } from "../background/messages";
import type { ExtensionSettings, SendGuardMode } from "../core/types";

const controls = {
  enabledChatGpt: document.querySelector<HTMLInputElement>("#enabled-chatgpt")!,
  enabledClaude: document.querySelector<HTMLInputElement>("#enabled-claude")!,
  sendGuard: document.querySelector<HTMLSelectElement>("#send-guard")!,
  autoRestore: document.querySelector<HTMLInputElement>("#auto-restore")!,
  hideOriginal: document.querySelector<HTMLInputElement>("#hide-original")!,
  restoreVisibleMessages: document.querySelector<HTMLInputElement>("#restore-visible")!
};
const mappingStatus = document.querySelector<HTMLElement>("#mapping-status")!;
const clearCurrent = document.querySelector<HTMLButtonElement>("#clear-current")!;
const clearAll = document.querySelector<HTMLButtonElement>("#clear-all")!;
const notice = document.querySelector<HTMLElement>("#notice")!;
document.querySelector<HTMLElement>("#version")!.textContent = `v${chrome.runtime.getManifest().version}`;

let context: { site: SupportedSite; conversationId: string; composerFound: boolean } | null = null;

function runtime<T extends RuntimeResponse>(message: object): Promise<T> {
  return chrome.runtime.sendMessage(message) as Promise<T>;
}

function showNotice(text: string): void {
  notice.textContent = text;
  window.setTimeout(() => {
    if (notice.textContent === text) notice.textContent = "";
  }, 2200);
}

function fill(settings: ExtensionSettings): void {
  controls.enabledChatGpt.checked = settings.enabledChatGpt;
  controls.enabledClaude.checked = settings.enabledClaude;
  controls.sendGuard.value = settings.sendGuard;
  controls.autoRestore.checked = settings.autoRestore;
  controls.hideOriginal.checked = settings.hideOriginal;
  controls.restoreVisibleMessages.checked = settings.restoreVisibleMessages;
}

function read(): ExtensionSettings {
  return {
    enabledChatGpt: controls.enabledChatGpt.checked,
    enabledClaude: controls.enabledClaude.checked,
    sendGuard: controls.sendGuard.value as SendGuardMode,
    autoRestore: controls.autoRestore.checked,
    hideOriginal: controls.hideOriginal.checked,
    restoreVisibleMessages: controls.restoreVisibleMessages.checked
  };
}

async function activeTab(): Promise<chrome.tabs.Tab | null> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] ?? null;
}

async function notifyContent(): Promise<void> {
  const tab = await activeTab();
  if (tab?.id === undefined) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "REFRESH_SETTINGS" });
  } catch {
    // The active tab can be an unsupported Chrome page.
  }
}

async function save(): Promise<void> {
  const response = await runtime<RuntimeResponse>({ type: "SET_SETTINGS", settings: read() });
  if (response.ok && "settings" in response) {
    fill(response.settings);
    await notifyContent();
    showNotice("Настройки сохранены");
  }
}

async function loadContext(): Promise<void> {
  const tab = await activeTab();
  if (tab?.id === undefined) return;
  try {
    context = await chrome.tabs.sendMessage(tab.id, { type: "GET_CONTEXT" }) as {
      site: SupportedSite;
      conversationId: string;
      composerFound: boolean;
    };
  } catch {
    context = null;
  }
  if (!context) return;
  if (!context.conversationId) context.conversationId = `tab:${tab.id}`;
  if (!context.composerFound) {
    mappingStatus.textContent = `${context.site}: composer не найден (adapter fail-safe)`;
  }
  const response = await runtime<RuntimeResponse>({ type: "GET_MAPPING_STATS", ...context });
  if (response.ok && "symbolCount" in response) {
    if (context.composerFound) {
      mappingStatus.textContent = `${context.site}: ${response.symbolCount} symbols · adapter OK`;
    }
    clearCurrent.disabled = false;
  }
}

for (const control of Object.values(controls)) control.addEventListener("change", () => void save());

clearCurrent.addEventListener("click", async () => {
  if (!context || !window.confirm("Очистить mapping текущего conversation? Старые OBF-ответы больше не восстановятся.")) return;
  const response = await runtime<RuntimeResponse>({ type: "CLEAR_MAPPING", ...context });
  if (response.ok && "cleared" in response) {
    mappingStatus.textContent = `${context.site}: 0 symbols`;
    showNotice("Mapping очищен");
  }
});

clearAll.addEventListener("click", async () => {
  if (!window.confirm("Очистить все conversation mappings? Это действие необратимо.")) return;
  const response = await runtime<RuntimeResponse>({ type: "CLEAR_ALL_MAPPINGS" });
  if (response.ok && "cleared" in response) {
    showNotice(`Удалено mappings: ${response.cleared}`);
    await loadContext();
  }
});

void runtime<RuntimeResponse>({ type: "GET_SETTINGS" }).then((response) => {
  if (response.ok && "settings" in response) fill(response.settings);
});
void loadContext();
