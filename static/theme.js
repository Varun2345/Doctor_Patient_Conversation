/* Apply the saved theme before styles load to avoid a light flash on reload. */
"use strict";
try {
  document.documentElement.dataset.theme = localStorage.getItem("consult-recorder-theme") === "dark" ? "dark" : "light";
} catch (_) {
  document.documentElement.dataset.theme = "light";
}
