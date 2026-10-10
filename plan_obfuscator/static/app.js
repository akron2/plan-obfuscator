const layout = document.querySelector("[data-app-layout]");
const conversation = document.querySelector("[data-conversation-scroll]");
const composer = document.querySelector("[data-composer]");
const composerInput = document.querySelector("[data-composer-input]");
const composerMode = document.querySelector("[data-composer-mode]");
const detectedKind = document.querySelector("[data-detected-kind]");
const fileInput = document.querySelector("[data-file-input]");
const selectedFile = document.querySelector("[data-selected-file]");

function copyTextFrom(target) {
  return target?.value ?? target?.textContent ?? "";
}

document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy-target]");
  if (copyButton) {
    const target = document.getElementById(copyButton.dataset.copyTarget);
    if (!target) return;
    await navigator.clipboard.writeText(copyTextFrom(target));
    const original = copyButton.textContent;
    copyButton.textContent = "Скопировано";
    copyButton.classList.add("copied");
    if (copyButton.hasAttribute("data-prompt-copied") && composerInput) {
      composerInput.placeholder = "Вставьте ответ ChatGPT или Claude…";
      composerInput.focus();
    }
    window.setTimeout(() => {
      copyButton.textContent = original;
      copyButton.classList.remove("copied");
    }, 1400);
  }

  const forceMode = event.target.closest("[data-force-mode]");
  if (forceMode && composerMode) {
    composerMode.value = forceMode.dataset.forceMode;
    forceMode.closest(".classification-prompt")?.remove();
    updateDetection();
    composerInput?.focus();
  }

  if (event.target.closest("[data-sidebar-open]")) layout?.classList.add("sidebar-open");
  if (event.target.closest("[data-sidebar-close]")) layout?.classList.remove("sidebar-open");

  if (event.target.closest("[data-scroll-context]")) {
    const context = document.getElementById("context");
    const details = context?.querySelector(":scope > details");
    if (details) details.open = true;
    context?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

const caseFilter = document.querySelector("[data-case-filter]");
if (caseFilter) {
  caseFilter.addEventListener("input", () => {
    const value = caseFilter.value.trim().toLowerCase();
    document.querySelectorAll("[data-case-title]").forEach((card) => {
      card.hidden = !card.dataset.caseTitle.includes(value);
    });
  });
}

const HISTORY_KEY = "plan-obfuscator.history-mode";
function setHistoryMode(mode) {
  const resolved = mode === "all" ? "all" : "latest";
  if (layout) layout.dataset.historyMode = resolved;
  document.querySelectorAll("[data-history-mode-button]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.historyModeButton === resolved));
  });
  localStorage.setItem(HISTORY_KEY, resolved);
}

document.querySelectorAll("[data-history-mode-button]").forEach((button) => {
  button.addEventListener("click", () => {
    setHistoryMode(button.dataset.historyModeButton);
    window.requestAnimationFrame(() => {
      if (conversation) conversation.scrollTop = conversation.scrollHeight;
    });
  });
});
setHistoryMode(localStorage.getItem(HISTORY_KEY) || "latest");

function autoGrow() {
  if (!composerInput) return;
  composerInput.style.height = "auto";
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 240)}px`;
}

function looksLikeMaterial(text) {
  const trimmed = text.trim();
  return /^(?:\/\*[\s\S]*?\*\/\s*)*(?:select|with|insert|update|delete|merge|explain|begin|declare)\b/i.test(trimmed)
    || /(?:SQL Monitoring Report|Plan hash value|Predicate Information|BEGIN_OUTLINE_DATA)/i.test(trimmed)
    || /^\s*\|\s*Id\s*\|\s*Operation\s*\|/im.test(trimmed);
}

function inferredMode() {
  if (fileInput?.files?.length) return ["material", "Файл · материал"];
  const selected = composerMode?.value || "auto";
  if (selected !== "auto") {
    const labels = { material: "Материал", question: "Вопрос", response: "Ответ ИИ" };
    return [selected, labels[selected] || selected];
  }
  const text = composerInput?.value || "";
  if (!text.trim()) return ["auto", "Auto"];
  if (looksLikeMaterial(text)) return ["material", "Auto · материал"];
  const prefix = composer?.dataset.casePrefix;
  if (prefix && new RegExp(`\\bOBF_${prefix}_[A-Z0-9_]+`, "i").test(text)) {
    return ["response", "Auto · ответ ИИ"];
  }
  if (text.length >= 400 || text.split(/\r?\n/).length >= 9) return ["auto", "Auto · не уверен"];
  return ["question", "Auto · вопрос"];
}

function updateDetection() {
  if (!detectedKind) return;
  const [kind, label] = inferredMode();
  detectedKind.textContent = label;
  detectedKind.className = `detected-kind ${kind}`;
}

if (composerInput) {
  composerInput.addEventListener("input", () => {
    autoGrow();
    updateDetection();
  });
  composerInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      composer?.requestSubmit();
    }
  });
  autoGrow();
}
composerMode?.addEventListener("change", updateDetection);

function showSelectedFile() {
  const file = fileInput?.files?.[0];
  if (!selectedFile) return;
  selectedFile.hidden = !file;
  selectedFile.textContent = file ? file.name : "";
  updateDetection();
}
fileInput?.addEventListener("change", showSelectedFile);

if (composer) {
  composer.addEventListener("dragover", (event) => {
    event.preventDefault();
    composer.classList.add("dragging");
  });
  composer.addEventListener("dragleave", () => composer.classList.remove("dragging"));
  composer.addEventListener("drop", (event) => {
    event.preventDefault();
    composer.classList.remove("dragging");
    if (fileInput && event.dataTransfer?.files?.length) {
      fileInput.files = event.dataTransfer.files;
      showSelectedFile();
    }
  });
}

updateDetection();

window.addEventListener("load", () => {
  if (window.location.hash === "#context") return;
  if (conversation) conversation.scrollTop = conversation.scrollHeight;
  if (window.location.hash === "#composer") composerInput?.focus();
});
