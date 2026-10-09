document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy-target]");
  if (copyButton) {
    const target = document.getElementById(copyButton.dataset.copyTarget);
    if (!target) return;
    await navigator.clipboard.writeText(target.value || target.textContent || "");
    const original = copyButton.textContent;
    copyButton.textContent = "Скопировано";
    window.setTimeout(() => { copyButton.textContent = original; }, 1200);
  }
});

document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

const filter = document.querySelector("[data-case-filter]");
if (filter) {
  filter.addEventListener("input", () => {
    const value = filter.value.trim().toLowerCase();
    document.querySelectorAll("[data-case-title]").forEach((card) => {
      card.hidden = !card.dataset.caseTitle.includes(value);
    });
  });
}

