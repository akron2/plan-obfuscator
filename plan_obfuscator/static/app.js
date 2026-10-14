const layout = document.querySelector("[data-app-layout]");
const conversation = document.querySelector("[data-conversation-scroll]");
const composer = document.querySelector("[data-composer]");
const composerInput = document.querySelector("[data-composer-input]");
const composerMode = document.querySelector("[data-composer-mode]");
const detectedKind = document.querySelector("[data-detected-kind]");
const fileInput = document.querySelector("[data-file-input]");
const selectedFile = document.querySelector("[data-selected-file]");
const pastedHtml = document.querySelector("[data-pasted-html]");
const DETECTION_SAMPLE_CHARS = 32768;
const MAX_CLIPBOARD_HTML_CHARS = 10 * 1024 * 1024;
let lastPastedPlain = composerInput && pastedHtml?.value ? composerInput.value : null;

function normalizeNewlines(value) {
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function clearPastedHtml() {
  lastPastedPlain = null;
  if (pastedHtml) pastedHtml.value = "";
}

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
    if (composerMode.value === "material" || composerMode.value === "question") {
      clearPastedHtml();
    }
    forceMode.closest(".classification-prompt")?.remove();
    updateDetection();
    composerInput?.focus();
  }

  const responseView = event.target.closest("[data-response-view]");
  if (responseView) {
    const responseId = responseView.dataset.responseId;
    const selectedView = responseView.dataset.responseView;
    document.querySelectorAll(`[data-response-id="${responseId}"]`).forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.responseView === selectedView));
    });
    for (const view of ["formatted", "source"]) {
      const panel = document.getElementById(`response-${view}-${responseId}`);
      if (panel) panel.hidden = view !== selectedView;
    }
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
  const sample = text.slice(0, DETECTION_SAMPLE_CHARS).trimStart();
  return /^(?:\/\*[\s\S]*?\*\/\s*|--[^\r\n]*(?:\r?\n|$)\s*)*(?:select|with|insert|update|delete|merge|explain|begin|declare|create|alter)\b/i.test(sample)
    || /(?:SQL Monitoring Report|Plan hash value|Predicate Information|BEGIN_OUTLINE_DATA|EXPLAINED SQL STATEMENT)/i.test(sample)
    || /^\s*\|\s*Id\s*\|\s*Operation\s*\|/im.test(sample);
}

function hasAtLeastLines(text, minLines) {
  let count = 1;
  let index = -1;
  while (count < minLines && (index = text.indexOf("\n", index + 1)) !== -1) {
    count += 1;
  }
  return count >= minLines;
}

function shouldCaptureClipboardHtml(plain, html) {
  if (!plain || !html || html.length > MAX_CLIPBOARD_HTML_CHARS) return false;
  const mode = composerMode?.value || "auto";
  if (mode === "material" || mode === "question") return false;
  if (mode === "response") return true;
  return !looksLikeMaterial(plain);
}

function inferredMode() {
  if (fileInput?.files?.length) return ["material", "Файл · материал"];
  const selected = composerMode?.value || "auto";
  if (selected !== "auto") {
    const labels = { material: "Материал", question: "Вопрос", response: "Ответ ИИ" };
    return [selected, labels[selected] || selected];
  }
  const text = composerInput?.value || "";
  const sample = text.slice(0, DETECTION_SAMPLE_CHARS);
  if (!sample.trim()) return ["auto", "Auto"];
  if (looksLikeMaterial(sample)) return ["material", "Auto · материал"];
  const prefix = composer?.dataset.casePrefix;
  if (prefix && new RegExp(`\\bOBF_${prefix}_[A-Z0-9_]+`, "i").test(sample)) {
    return ["response", "Auto · ответ ИИ"];
  }
  if (text.length >= 400 || hasAtLeastLines(sample, 9)) return ["auto", "Auto · не уверен"];
  return ["question", "Auto · вопрос"];
}

function updateDetection() {
  if (!detectedKind) return;
  const [kind, label] = inferredMode();
  detectedKind.textContent = label;
  detectedKind.className = `detected-kind ${kind}`;
}

if (composerInput) {
  composerInput.addEventListener("paste", (event) => {
    const clipboard = event.clipboardData;
    if (!clipboard || !pastedHtml) return;
    const plain = clipboard.getData("text/plain") || "";
    const html = clipboard.getData("text/html") || "";
    if (shouldCaptureClipboardHtml(plain, html)) {
      lastPastedPlain = plain;
      pastedHtml.value = html;
    } else {
      clearPastedHtml();
    }
  });
  composerInput.addEventListener("input", () => {
    if (
      lastPastedPlain !== null
      && normalizeNewlines(composerInput.value) !== normalizeNewlines(lastPastedPlain)
    ) {
      clearPastedHtml();
    }
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
composerMode?.addEventListener("change", () => {
  if (composerMode.value === "material" || composerMode.value === "question") {
    clearPastedHtml();
  }
  updateDetection();
});

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

function sanitizedMermaidSvg(svg) {
  const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
  if (parsed.querySelector("parsererror")) throw new Error("Invalid Mermaid SVG");
  parsed.querySelectorAll("script, image, foreignObject").forEach((node) => node.remove());
  parsed.querySelectorAll("*").forEach((node) => {
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (["href", "xlink:href", "src"].includes(name) && !value.startsWith("#")) {
        node.removeAttribute(attribute.name);
      }
      if (/url\(\s*["']?(?:https?:|\/\/|data:)/i.test(value)) {
        node.removeAttribute(attribute.name);
      }
      if (name.startsWith("on")) node.removeAttribute(attribute.name);
    }
  });
  return document.importNode(parsed.documentElement, true);
}

async function renderMermaidBlocks() {
  const blocks = [...document.querySelectorAll("[data-mermaid-block]")];
  if (!blocks.length || !window.mermaid) return;
  window.mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    htmlLabels: false,
    maxTextSize: 20000,
    maxEdges: 200,
    suppressErrorRendering: true,
    theme: "dark",
  });
  for (const [index, block] of blocks.entries()) {
    const source = block.querySelector(".mermaid-source");
    const target = block.querySelector("[data-mermaid-diagram]");
    const definition = source?.textContent || "";
    if (!source || !target || !definition || definition.length > 20000) continue;
    try {
      const result = await window.mermaid.render(`plan-obfuscator-mermaid-${index}`, definition);
      target.replaceChildren(sanitizedMermaidSvg(result.svg));
      target.hidden = false;
      source.hidden = true;
    } catch (_error) {
      block.classList.add("mermaid-error");
    }
  }
}

void renderMermaidBlocks();

window.addEventListener("load", () => {
  if (window.location.hash === "#context") return;
  if (conversation) conversation.scrollTop = conversation.scrollHeight;
  if (window.location.hash === "#composer") composerInput?.focus();
});
