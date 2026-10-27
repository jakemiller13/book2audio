// Shared by worker.js (background thread) and index.html (fallback on the page itself).
import { KokoroTTS, env } from "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js";

const MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
const VOICE = "am_michael";

// Written for this test. The first sentence is short so the first number arrives quickly.
export const SENTENCES = [
  "The lighthouse keeper climbed the stairs.",
  "He counted each step out of habit, the way his father had taught him.",
  "At the top, he wiped the salt from the great lens and checked the oil, then sat by the window with a cup of tea gone cold.",
  "Far below, the fishing boats were turning for home, their lanterns bobbing like fireflies on the dark water.",
  "He liked this hour best: the work done, the light turning, and the whole coast trusting him to stay awake.",
];

export async function bench(device, post) {
  if (device === "webgpu" && !(self.navigator && self.navigator.gpu)) {
    post({ type: "unsupported" });
    return;
  }
  const wasm = env.backends && env.backends.onnx && env.backends.onnx.wasm;
  const cores = self.navigator.hardwareConcurrency || 1;
  const threads = device === "wasm" && self.crossOriginIsolated ? Math.max(1, Math.min(cores, 8)) : 1;
  if (wasm) wasm.numThreads = threads;
  post({ type: "info", threads, cores, isolated: !!self.crossOriginIsolated });

  const files = {};
  const t0 = performance.now();
  const tts = await KokoroTTS.from_pretrained(MODEL, {
    dtype: device === "webgpu" ? "fp32" : "q8",
    device,
    progress_callback: (p) => {
      if (p.status === "progress" && p.total) {
        files[p.file] = [p.loaded, p.total];
        const [loaded, total] = Object.values(files).reduce(([a, b], [c, d]) => [a + c, b + d], [0, 0]);
        post({ type: "download", loaded, total });
      }
    },
  });
  post({ type: "loaded", ms: performance.now() - t0, threads: wasm ? wasm.numThreads : threads });

  for (const [i, text] of SENTENCES.entries()) {
    post({ type: "sentence-start", i, n: SENTENCES.length });
    const t = performance.now();
    const out = await tts.generate(text, { voice: VOICE });
    const audio = out.audio;
    post({ type: "sentence", i, n: SENTENCES.length, ms: performance.now() - t,
           seconds: audio.length / out.sampling_rate, rate: out.sampling_rate, audio }, [audio.buffer]);
  }
  post({ type: "done" });
}
