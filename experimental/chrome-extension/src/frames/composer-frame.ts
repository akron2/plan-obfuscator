import type { ContentSettings, RuntimeResponse, SupportedSite } from "../background/messages";

const composer = document.querySelector<HTMLElement>("#composer")!;
const prompt = document.querySelector<HTMLTextAreaElement>("#prompt")!;
const protectButton = document.querySelector<HTMLButtonElement>("#protect")!;
const copyButton = document.querySelector<HTMLButtonElement>("#copy-safe")!;
const unsafeButton = document.querySelector<HTMLButtonElement>("#unsafe")!;
const statusText = document.querySelector<HTMLElement>("#status-text")!;

let site: SupportedSite | null = null;
let conversationId = "";
let settings: ContentSettings | null = null;
let protectedSnapshot = "";
let safeSnapshot = "";
let working = false;
let pendingAutoSend = false;

function runtime<T extends RuntimeResponse>(message: object): Promise<T> {
  return chrome.runtime.sendMessage(message) as Promise<T>;
}

function post(type: string, extra: Record<string, unknown> = {}): void {
  parent.postMessage({ source: "plan-obfuscator-frame", type, ...extra }, "*");
}

function setState(
  state: "idle" | "working" | "protected" | "dirty" | "warning" | "error",
  text: string
): void {
  composer.dataset.state = state;
  statusText.textContent = text;
  working = state === "working";
  protectButton.disabled = working;
  protectButton.textContent = working
    ? "◌ Анализ…"
    : state === "protected"
      ? "✓ Защищено"
      : "🛡 Обфусцировать";
  post("STATE", {
    protected: state === "protected",
    dirty: state === "dirty" || state === "warning",
    hasText: prompt.value.length > 0
  });
}

function markEdited(): void {
  unsafeButton.classList.add("hidden");
  if (!prompt.value) {
    protectedSnapshot = "";
    safeSnapshot = "";
    copyButton.classList.add("hidden");
    setState("idle", "Готово к защите");
    return;
  }
  if (protectedSnapshot && prompt.value === protectedSnapshot) {
    setState("protected", "Текст защищён");
    return;
  }
  safeSnapshot = "";
  copyButton.classList.add("hidden");
  setState("dirty", protectedSnapshot ? "Текст изменён — защитите снова" : "Есть незащищённый текст");
}

async function protect(): Promise<void> {
  if (working || !site) return;
  const text = prompt.value;
  if (!text.trim()) {
    setState("idle", "Введите текст");
    return;
  }
  setState("working", "Локальный анализ Oracle…");
  safeSnapshot = "";
  copyButton.classList.add("hidden");
  try {
    const response = await runtime<RuntimeResponse>({
      type: "OBFUSCATE",
      site,
      conversationId,
      text
    });
    if (!response.ok || !("obfuscatedText" in response)) {
      throw new Error(response.ok ? "Некорректный ответ расширения" : response.error);
    }
    if (response.validationStatus === "error") {
      throw new Error(response.findings.find((item) => item.severity === "error")?.message ?? "Ошибка проверки");
    }
    protectedSnapshot = text;
    safeSnapshot = response.obfuscatedText;
    copyButton.classList.remove("hidden");
    const warningCount = response.findings.filter((item) => item.severity === "warning").length;
    setState(
      "protected",
      warningCount > 0
        ? `Защищено: ${response.replacementCount} замен · ${warningCount} предупрежд.`
        : `Защищено: ${response.replacementCount} замен`
    );
    post("SAFE_TEXT", { text: response.obfuscatedText });
  } catch (error) {
    setState("error", error instanceof Error ? error.message : String(error));
  }
}

async function copySafeText(): Promise<void> {
  if (!safeSnapshot) return;
  try {
    await navigator.clipboard.writeText(safeSnapshot);
    setState("protected", "OBF-текст скопирован");
  } catch (error) {
    setState("error", error instanceof Error ? error.message : "Не удалось скопировать OBF-текст");
  }
}

async function storeUnsafe(autoSend: boolean): Promise<void> {
  if (!site || !prompt.value) return;
  if (!autoSend && !window.confirm("Отправить исходный текст без обфускации? Он станет доступен странице чата.")) {
    return;
  }
  try {
    const response = await runtime<RuntimeResponse>({
      type: "STORE_UNSAFE_OVERRIDE",
      site,
      conversationId,
      text: prompt.value
    });
    if (!response.ok || !("unsafeId" in response)) throw new Error("Не удалось создать override");
    unsafeButton.classList.add("hidden");
    setState("warning", autoSend ? "Отправка без защиты…" : "Исходный текст подготовлен на 15 секунд");
    post("UNSAFE_READY", { unsafeId: response.unsafeId, autoSend });
  } catch (error) {
    setState("error", error instanceof Error ? error.message : String(error));
  }
}

prompt.addEventListener("input", markEdited);
prompt.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  post("SEND_GESTURE");
});
protectButton.addEventListener("click", () => void protect());
copyButton.addEventListener("click", () => void copySafeText());
unsafeButton.addEventListener("click", (event) => {
  if (!event.isTrusted) return;
  void storeUnsafe(false);
});

window.addEventListener("message", (event: MessageEvent) => {
  if (event.source !== parent) return;
  const message = event.data as {
    source?: string;
    type?: string;
    site?: SupportedSite;
    conversationId?: string;
    settings?: ContentSettings;
    autoSend?: boolean;
    mode?: string;
  };
  if (message.source !== "plan-obfuscator-content") return;
  if (message.type === "CONFIGURE" && message.site && message.settings) {
    site = message.site;
    conversationId = message.conversationId ?? "";
    settings = message.settings;
    return;
  }
  if (message.type === "GUARD_BLOCKED") {
    unsafeButton.classList.remove("hidden");
    const mode = message.mode === "warn" ? "Предупреждение" : "Отправка заблокирована";
    setState("warning", `${mode}: сначала обфусцируйте текст`);
    return;
  }
  if (message.type === "REQUEST_UNSAFE") {
    pendingAutoSend = Boolean(message.autoSend);
    void runtime<RuntimeResponse>({ type: "GET_SETTINGS" }).then((response) => {
      if (response.ok && "settings" in response && response.settings.sendGuard === "off") {
        void storeUnsafe(pendingAutoSend);
      }
    });
    return;
  }
  if (message.type === "RESET_AFTER_SEND" || message.type === "RESET_CONVERSATION") {
    prompt.value = "";
    protectedSnapshot = "";
    safeSnapshot = "";
    copyButton.classList.add("hidden");
    unsafeButton.classList.add("hidden");
    setState(
      "idle",
      message.type === "RESET_AFTER_SEND"
        ? "Отправлено · готово к новому prompt"
        : "Новый conversation · mapping изолирован"
    );
  }
});

post("FRAME_READY");
setState("idle", "Готово к защите");
prompt.focus();
