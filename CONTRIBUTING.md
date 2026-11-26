# Contributing to kaggle-offload

Thanks for being here. The goal is simple: help as many people as possible use Kaggle's free GPU hours, without wasting them. Any contribution in that direction is welcome.

## What helps most

1. **Templates for new models.** This is the most useful contribution. Wanted:
   - Whisper (batch transcription of audio and video);
   - SDXL or Flux (batch image generation);
   - LoRA training (consistent images of a product or a character);
   - image and video upscaling;
   - a small LLM for batch text work (summarize, classify, translate, extract).
2. **Tests on macOS and Linux.** The skill has only been tested on Windows 11. Running `ko.py setup`, `ko.py bench --cpu-only` and `ko.py open` on another system makes a valuable issue.
3. **Fixes and ideas.** A bug, a Kaggle limit that changed, a way to waste less quota.

## Writing a template

A template is a folder in `templates/<name>/` with two files.

**`main.py`**, the script that runs on Kaggle:
- Jobs arrive in `JOBS = json.loads('__JOBS__')`: `ko.py` replaces `'__JOBS__'` with the job list as JSON. One run processes the whole list, so installation and model loading are paid once.
- Outputs go to `/kaggle/working`.
- Write `/kaggle/working/metrics.json` with at least `prep_s` (installing and loading) and `compute_s` (the actual work) and, when it makes sense, `product: {"amount": ..., "unit": "..."}` (for example seconds of audio, or images). The dashboard derives speed and setup overhead from these.
- If you use the cache, look for `ko-cache.json` under `/kaggle/input` and fall back to installing from the internet when it isn't there: see `templates/qwen-tts/main.py`.
- Print progress with `print(..., flush=True)`.
- No keys in the code: if you need any, use Kaggle Secrets.

**`template.json`**:

```json
{
 "description": "what it does, in one line",
 "acc": "gpu",
 "vram_gb": 8,
 "unit": "seconds of audio",
 "cache_pip": ["package"],
 "cache_hf": ["organization/model"],
 "tested": false,
 "origin": "when and how it was tested, with timings"
}
```

**The most important rule:** set `"tested": true` only after a real run on Kaggle succeeded. In the pull request, include that run's numbers: GPU, session length, setup, compute, quota used. You'll find them in the output of `ko.py run`.

## Code rules

- `scripts/ko.py` uses **only the Python standard library**, plus the Kaggle CLI. No new dependencies on the user's machine: heavy packages belong in templates, on Kaggle.
- The dashboard (`scripts/dashboard.html`) is a single file, with no external libraries or CDNs.
- User data lives in `~/.kaggle-offload/` and never enters the repo: no tokens, run logs or credits in pull requests.
- The skill never reads the Kaggle token, nor keys of other services from other projects' files. Keys are linked by the user.
- The original author works in Italian, so Italian command names still work as aliases. New code and docs are in English.

## The devlog

[`DEVLOG.md`](DEVLOG.md) tells how the project evolves, with real numbers. If your contribution taught something (a measured timing, an error, a limit), add a dated entry.

## Proposing a change

1. Open an issue to discuss larger changes. For a new template or a small fix you can go straight to a pull request.
2. Fork, create a branch (`template-whisper`, `fix-port-linux`...), make your changes.
3. Check that `python scripts/ko.py setup` and `python scripts/ko.py open` still work.
4. Open the pull request explaining what changes and, for a template, with the numbers from the test run.

## Conduct

Discuss the work, kindly. Personal attacks, harassment and offensive content are not welcome.
