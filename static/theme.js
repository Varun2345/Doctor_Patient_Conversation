/* Apply the saved theme, or default to dark, before styles load. */
"use strict";
try {
  document.documentElement.dataset.theme = localStorage.getItem("consult-recorder-theme") === "light" ? "light" : "dark";
} catch (_) {
  document.documentElement.dataset.theme = "dark";
}
