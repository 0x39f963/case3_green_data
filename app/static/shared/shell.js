// Mobile tool-col toggle. Mirrors the canonical pattern in test_report.js.
document.addEventListener("DOMContentLoaded", () => {
  const pageShell = document.getElementById("pageShell");
  const toolCol = document.getElementById("toolCol");
  const toggle = document.getElementById("toolToggle");
  if (!pageShell || !toolCol || !toggle) return;

  const closeMenu = () => {
    toolCol.classList.remove("is-open");
    pageShell.classList.remove("tools-open");
    toggle.setAttribute("aria-expanded", "false");
  };

  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = toolCol.classList.toggle("is-open");
    pageShell.classList.toggle("tools-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  });

  document.addEventListener("click", (event) => {
    if (!toolCol.classList.contains("is-open")) return;
    if (toolCol.contains(event.target) || toggle.contains(event.target)) return;
    closeMenu();
  });
});
