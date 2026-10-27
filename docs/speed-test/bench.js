// Shared by worker.js (background thread) and index.html (when run on the page itself).
import { KokoroTTS, env } from "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/dist/kokoro.web.js";
import { phonemize } from "https://cdn.jsdelivr.net/npm/phonemizer@1.2.1/dist/phonemizer.js";

const MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
const VOICE = "am_michael";
const VOICE_URL = `https://huggingface.co/${MODEL}/resolve/main/voices/${VOICE}.bin`;

// Model choices: [device, dtype, approximate download]
export const MODELS = {
  cpu: ["wasm", "q8", "92 MB"],
  "gpu-half": ["webgpu", "fp16", "163 MB"],
  "gpu-full": ["webgpu", "fp32", "325 MB"],
};

// Written for this test. The first sentence is short so the first number arrives quickly.
export const SENTENCES = [
  "The lighthouse keeper climbed the stairs.",
  "He counted each step out of habit, the way his father had taught him.",
  "At the top, he wiped the salt from the great lens and checked the oil, then sat by the window with a cup of tea gone cold.",
  "Far below, the fishing boats were turning for home, their lanterns bobbing like fireflies on the dark water.",
  "He liked this hour best: the work done, the light turning, and the whole coast trusting him to stay awake.",
];

async function step(post, name, fn) {
  post({ type: "step-start", name });
  const t = performance.now();
  const result = await fn();
  post({ type: "step", name, ms: performance.now() - t });
  return result;
}

export async function bench(choice, post) {
  const [device, dtype] = MODELS[choice];
  const nav = self.navigator;
  if (device === "webgpu") {
    if (!nav.gpu) { post({ type: "unsupported" }); return; }
    const adapter = await nav.gpu.requestAdapter();
    if (!adapter) { post({ type: "unsupported" }); return; }
    const info = adapter.info || {};
    post({ type: "info-gpu", gpu: [info.vendor, info.architecture].filter(Boolean).join(" ") || "unknown",
           f16: adapter.features.has("shader-f16") });
  }
  const wasm = env.backends && env.backends.onnx && env.backends.onnx.wasm;
  const cores = nav.hardwareConcurrency || 1;
  const threads = device === "wasm" && self.crossOriginIsolated ? Math.max(1, Math.min(cores, 8)) : 1;
  if (wasm) wasm.numThreads = threads;
  post({ type: "info", threads, cores, isolated: !!self.crossOriginIsolated });

  const files = {};
  const tts = await step(post, "Load model", () => KokoroTTS.from_pretrained(MODEL, {
    dtype, device,
    progress_callback: (p) => {
      if (p.status === "progress" && p.total) {
        files[p.file] = [p.loaded, p.total];
        const [loaded, total] = Object.values(files).reduce(([a, b], [c, d]) => [a + c, b + d], [0, 0]);
        post({ type: "download", loaded, total });
      }
    },
  }));

  // Sentence 1, one part at a time, so a stall shows which part is stuck
  await step(post, "Fetch voice file", () => fetch(VOICE_URL).then((r) => r.arrayBuffer()));
  const phonemes = await step(post, "Pronunciation", () => phonemize(SENTENCES[0], "en-us").then((p) => p.join(" ")));
  const ids = await step(post, "Tokenize", () => tts.tokenizer(phonemes, { truncation: true }).input_ids);
  const first = await step(post, "Voice model (first run)", () => tts.generate_from_ids(ids, { voice: VOICE }));
  const send = (i, ms, out) => post({ type: "sentence", i, n: SENTENCES.length, ms,
    seconds: out.audio.length / out.sampling_rate, rate: out.sampling_rate, audio: out.audio }, [out.audio.buffer]);
  send(0, null, first);

  for (let i = 1; i < SENTENCES.length; i++) {
    post({ type: "step-start", name: `Sentence ${i + 1}` });
    const t = performance.now();
    const out = await tts.generate(SENTENCES[i], { voice: VOICE });
    send(i, performance.now() - t, out);
  }
  post({ type: "done" });
}
