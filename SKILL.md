---
name: kaggle-offload
description: >-
  Decides where a heavy job should run (on this PC, on Kaggle CPU, on Kaggle's free GPU or TPU, or on a
  paid service) by looking at the machine it runs on, the memory the model needs and the Kaggle quota
  left; runs it on Kaggle, batches similar jobs into one run, caches packages and weights, and opens a
  local dashboard with quota, running jobs, measured speed, estimated savings and credits across services
  with expiry dates. Use it when an AI model won't run on the PC or crawls on the CPU, when a GPU is
  needed, or when the user asks how much quota or credit is left, even if they don't mention Kaggle.
when_to_use: >-
  "run it on Kaggle", "use the free GPUs", "it won't start on my PC", "this takes forever", "so slow on
  the CPU", "I don't have a GPU", "how much quota is left?", "how many credits do I have?", "open the GPU
  dashboard", "benchmark my PC"; in Italian: "fallo girare su Kaggle", "usa le GPU gratis", "sul PC non
  parte", "ci mette una vita", "è lentissimo sulla CPU", "non ho la GPU", "quanta quota mi resta?",
  "quanti crediti ho?"; or when an AI job (TTS, Whisper, diffusion, face swap, motion control, LLM batch,
  fine-tuning) is about to run on a CPU or on a machine without an NVIDIA GPU.
---

# kaggle-offload

The tool is `scripts/ko.py` (Python standard library only, plus the Kaggle CLI). Below, `KO` means
`python "${CLAUDE_SKILL_DIR}/scripts/ko.py"`. Data (run ledger, quota, credits, benchmarks,
cache, queue) lives in `~/.kaggle-offload/`, never in the skill folder. Talk to the user in their
language; Italian command names (`decidi`, `coda`, `crediti`, `apri`) also work.

## Procedure

1. **Open the dashboard first.** `KO open` starts it in the background from port 8790 (or the first free
   one: the command prints the address) and opens the browser. `run`, `bench` and `cache` also start it if
   it isn't running.
2. **First use on a machine: `KO setup`.** It checks the CLI, token, quota and hardware. If it says
   `"ready": false`, walk the user through "Quick start" in the README: sign-up, phone verification
   (without it the GPU stays off), token. The user places the token: **never read or print it**.
3. **Decide where it runs.** Estimate the VRAM the model needs (fp16 weights ≈ 2 GB per billion
   parameters, plus headroom) and the duration, then `KO decide --vram <GB> --hours <h>` or
   `KO decide --cpu-only --hours <h>`. Tell the user the decision and the reason in one line.
   - `pc`: leave Kaggle out of it.
   - `kaggle-gpu` / `kaggle-cpu`: go to step 4.
   - `paid` or `wait-or-pay`: say so with the numbers (VRAM, hours left, refill date) and let the user
     decide. Never start paid services on your own.
4. **Run it.**
   - If a template exists in `templates/` (`qwen-tts`, `bench`, `cache`): write the jobs to a JSON file
     and `KO run --template <t> --jobs jobs.json --project <name>`.
   - **Several jobs of the same kind go in one run:** `KO queue add <t> --file jobs.json`, then
     `KO queue launch <t>`. Installation and model loading are paid once (97 s out of 320 in the first
     Qwen test).
   - Without a template: write a script and `KO run --script file.py --acc gpu|cpu|tpu`. Script rules:
     outputs in `/kaggle/working`; write `/kaggle/working/metrics.json` with at least `prep_s`,
     `compute_s` and, when it makes sense, `product: {amount, unit}`, because the dashboard derives speed
     and setup overhead from them; print progress with `flush=True`; no keys in the code (use Kaggle
     Secrets).
   - The first time a script works and will be needed again, **turn it into a template**: a folder in
     `templates/<name>/` with `main.py` (jobs arrive in `JOBS = json.loads('__JOBS__')`) and
     `template.json` (`description`, `acc`, `vram_gb`, `unit`, `cache_pip`, `cache_hf`, `tested`,
     `origin`). Don't write templates for models that were never run.
5. **Cache.** When a template is used a second time, suggest `KO cache <t>`: it runs CPU-only (no quota),
   stores on Kaggle the wheels missing from the image and the Hugging Face weights; later runs mount them
   instead of downloading again. The cache never comes down to the PC.
6. **Long jobs without a GPU** (Whisper on CPU, conversions, scraping, ffmpeg over many files): above a
   quarter of an hour, suggest Kaggle CPU (`--acc cpu`). It uses no quota and leaves the PC free. Kaggle
   has 4 cores: not always faster than the PC, the point is not tying it up. The dashboard's benchmarks
   tell by how much.
7. **After every run** report in two or three lines: status, queue time, session, quota used, setup vs
   compute, downloaded results (path in `output`). If the run taught something (an error, a timing, a
   limit), add it to `DEVLOG.md`.

## Noticing slow jobs

Suggest the skill unprompted when you see: `torch.cuda.is_available()` false, `device='cpu'` in an AI
model, Whisper or a diffusion model started on the PC, an install blocked by native libraries (e.g.
Windows Smart App Control), or the user saying a job "takes forever". One line, with an estimate:
"There's no CUDA here: a Kaggle T4 does this in about X."

## Credits

`KO credits` shows the ledger; Kaggle quota updates itself. Other services are updated by hand with
`KO credits set --service <name> --remaining <n> --unit <u> --expires YYYY-MM-DD
--expiry-type expires|renews --note "..."`, whenever the user mentions a new number. ElevenLabs is read
automatically only if the user sets `ELEVENLABS_API_KEY` in the environment or an `elevenlabs_env_file`
in `~/.kaggle-offload/config.json`. **Never go fetching keys from other projects' files on your own.**

## Limits to mention when they matter

- About 30 GPU hours a week (exact figure in `KO quota`), 20 TPU hours, sessions up to 12 h (9 h on TPU).
  The week refills at the `refreshAt` date shown by `KO quota`.
- Quota counts from session start, setup included: that's why jobs are batched.
- Kernels are private, but code and data go through Kaggle's (Google's) servers: no personal data of
  clients or leads without the user's explicit OK. If privacy matters, the model has to run locally.
- A T4 has 16 GB (two per session at most), no bf16, no FlashAttention 2: larger models must be quantized.
- The dashboard's "savings" are an equivalence: GPU hours times the on-demand price of a T4
  (`gpu_price_per_hour` and `price_source` in config). Not real money saved if the job wouldn't have run
  otherwise.
