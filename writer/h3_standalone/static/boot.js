import "/main.js";
import { boot } from "/scripts/app.js";
import { startHostLabels } from "/scripts/host_labels.js?v=0.2.0-learning.1";
import { startManagedGGUF } from "/scripts/managed_gguf.js";
import { startLearning } from "/scripts/learning.js?v=0.2.0-learning.1-recent5";

try {
  await boot();
  startHostLabels();
  startManagedGGUF();
  startLearning();
  document.documentElement.dataset.h3StandaloneShell = "ready";
  document.title = "H3 Learning Writer";
  document.querySelector("[data-host-status]")?.replaceChildren("Writer is ready.");
} catch (error) {
  document.querySelector("[data-host-status]")?.replaceChildren(
    `Startup failed: ${error?.message || error}`,
  );
  throw error;
}
