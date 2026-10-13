import type { RuntimeResponse } from "../background/messages";

const renderId = decodeURIComponent(location.hash.slice(1));
const content = document.querySelector<HTMLElement>("#content")!;
const toggle = document.querySelector<HTMLButtonElement>("#toggle")!;
const copy = document.querySelector<HTMLButtonElement>("#copy")!;
const warning = document.querySelector<HTMLElement>("#warning")!;
let restored = "";
let raw = "";
let showingRaw = false;

function postHeight(): void {
  parent.postMessage(
    {
      source: "plan-obfuscator-frame",
      type: "RESTORED_HEIGHT",
      renderId,
      height: document.documentElement.scrollHeight
    },
    "*"
  );
}

function render(): void {
  content.textContent = showingRaw ? raw : restored;
  toggle.textContent = showingRaw ? "Показать восстановленный" : "Показать OBF";
  window.requestAnimationFrame(postHeight);
}

toggle.addEventListener("click", () => {
  showingRaw = !showingRaw;
  render();
});
copy.addEventListener("click", async () => {
  await navigator.clipboard.writeText(showingRaw ? raw : restored);
  copy.textContent = "Скопировано";
  window.setTimeout(() => {
    copy.textContent = "Копировать";
  }, 1200);
});

void (chrome.runtime.sendMessage({ type: "GET_RENDER", renderId }) as Promise<RuntimeResponse>)
  .then((response) => {
    if (!response.ok || !("render" in response) || !response.render) {
      throw new Error("Временные данные ответа уже недоступны");
    }
    restored = response.render.restoredText;
    raw = response.render.rawText;
    if (response.render.unknownMarkers.length > 0) {
      warning.textContent = `Не удалось восстановить markers: ${response.render.unknownMarkers.join(", ")}`;
      warning.classList.remove("hidden");
    }
    render();
  })
  .catch((error: unknown) => {
    content.textContent = error instanceof Error ? error.message : String(error);
    postHeight();
  });

new ResizeObserver(postHeight).observe(document.documentElement);
