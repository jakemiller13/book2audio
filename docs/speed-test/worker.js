// Runs the speed test off the page's main thread so the page stays responsive.
import { bench } from "./bench.js";

self.onmessage = async (e) => {
  try {
    await bench(e.data.device, (msg, transfer) => self.postMessage(msg, transfer || []));
  } catch (err) {
    self.postMessage({ type: "error", message: String((err && err.stack) || err) });
  }
};
self.postMessage({ type: "ready" });
