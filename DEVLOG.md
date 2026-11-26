# kaggle-offload devlog

How the skill evolves, with real numbers.

## 26 Nov 2026: the experiment that started it

- **The limit:** a laptop with a Ryzen 5 3550H, 16 GB of RAM and integrated Radeon graphics. No CUDA. Qwen3-TTS installed locally wouldn't start: Windows Smart App Control blocked scipy's native libraries.
- **The test on Kaggle:** same model (Qwen3-TTS 1.7B, VoiceDesign and Base) on a Tesla T4. Six audio clips in 5 min 20 s. That includes 38 s of installation and 59 s of model loading: 97 s out of 320, almost a third, just getting ready. Each 7-9 s sentence took 19-34 s of compute.
- **Outcome:** for that project's character voice a commercial TTS still sounded more natural. But the road to open models that won't run on the laptop was open, at no cost.
- **Quota after the day's tests:** 0.22 GPU hours out of 30.

## 26 Nov 2026: the skill is born

- **Idea:** stop wondering "can my PC handle this?" and let the skill decide where a job runs, and show what it costs, saves and how fast it goes.
- **Useful discovery:** Kaggle CLI 2.2.5 has `kaggle quota`, which returns hours used, hours left and the refill date. Usage is read from real data, not estimated.
- **Choices:**
  - First version: batched jobs with a cache, CPU-only jobs that use no quota, and a ledger of credits across services, all on a local dashboard.
  - Templates are written as needed, and only for models that have actually been run.
- **First benchmark, same matrix multiplications:**

  | Machine | GFLOPS | vs the laptop |
  |---|---:|---:|
  | Laptop (Ryzen 5 3550H) | 53 | 1× |
  | Kaggle CPU only (Xeon 2.2 GHz, 4 cores) | 139 | 2.6× |
  | Kaggle GPU T4, fp32 | 3,507 | 66× |
  | Kaggle GPU T4, fp16 | 21,470 | 405× |

  The CPU-only benchmark used no quota; the GPU one used 0.01 h: a 35 s session after 19 s in the queue.
- **Bugs found while testing:**
  - Two runs in parallel credited each other's quota usage. Now each run counts only its own resource.
  - The dashboard's port was already taken by another app on the PC, and the skill thought it was its own. Now it identifies itself through `/api/ping` and moves to the first free port if needed.
- **Lesson:** linking a TTS service by reading its API key from another project's `.env` was blocked by Claude Code's permission check. Rightly so: keys are linked by the user, the skill doesn't go looking for them.

## 26 Nov 2026: cache and Qwen-TTS tested

- **Cache:** 10 pip packages and both Qwen models, 8.52 GB stored on Kaggle in a 56 s CPU-only session. No quota used.
- **Qwen-TTS with the cache:** the same six lines as the morning test, in one run.

  | | Morning (no cache) | Evening (cache) |
  |---|---:|---:|
  | Installation | 38.4 s | 24.9 s |
  | Model loading | 58.6 s | 46.6 s |
  | Total setup | 97 s | 71 s (−26%) |
  | Compute | 145.5 s | 131.6 s |
  | Whole run | 320 s | 261 s (−18%) |

  Session 274 s, 0.08 GPU hours. 46.6 s of audio produced: about 2.8 s of compute per second of audio.
- **Lesson:** the cache cuts setup time but doesn't remove it. The remaining 25 s are pip installing 10 wheels from disk (including gradio, the web UI that comes with qwen-tts' dependencies); loading is still reading the weights. The bigger the batch, the less they weigh.

## 26 Nov 2026: open source

- The repo goes public under the MIT license, with an English README, a contributing guide, and code and dashboard translated to English. Italian commands keep working as aliases, and existing data is migrated on first run.

## To do

- Try `queue` (add, launch) and `run --script`, never run so far.
- Templates wanted: Whisper, SDXL with LoRA training, a small LLM for batch text work, upscaling.
