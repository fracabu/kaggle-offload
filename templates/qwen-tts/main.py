# kaggle-offload: Qwen3-TTS with batched jobs, on a T4 GPU.
# One run = all the queued jobs: installing and loading the models is paid for only once.
# Job: {"name": "x", "text": "...", "language": "Italian", "voice": {"design": "description of the voice"}}
#  or: {"name": "y", "text": "...", "voice": {"clone": "x"}}   (clones the voice of an earlier job)
# Outputs: /kaggle/working/out/<name>.wav and /kaggle/working/metrics.json
# The Italian job keys of the first version (nome, testo, lingua, voce, clona) are still accepted.
# "language" is the language name Qwen expects; it defaults to "Italian" as it always did.
import json
import os
import subprocess
import sys
import time

JOBS = json.loads('__JOBS__')
OUT = '/kaggle/working/out'
os.makedirs(OUT, exist_ok=True)
DESIGN = 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
BASE = 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'
t0 = time.time()


def normalize_job(j):
    """Accepts both the English keys and the old Italian ones."""
    keys = {'nome': 'name', 'testo': 'text', 'lingua': 'language', 'voce': 'voice'}
    j = {keys.get(k, k): v for k, v in j.items() if not (k in keys and keys[k] in j)}
    if isinstance(j.get('voice'), dict) and 'clona' in j['voice'] and 'clone' not in j['voice']:
        j['voice'] = {('clone' if k == 'clona' else k): v for k, v in j['voice'].items()}
    return j


JOBS = [normalize_job(j) for j in JOBS]


def find_cache():
    for root, dirs, files in os.walk('/kaggle/input'):
        if 'ko-cache.json' in files:
            return root
        if root.count(os.sep) > 6:
            dirs[:] = []
    return None


C = find_cache()


def install():
    w = f'{C}/wheels' if C else None
    if w and os.path.isdir(w) and os.listdir(w):
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-index', '--find-links', w, 'qwen-tts'])
        if r.returncode == 0:
            return 'cache'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'qwen-tts'], check=True)
    return 'internet'


pip_from = install()
install_s = time.time() - t0

import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel


def model_path(repo):
    p = f'{C}/models/{repo.replace("/", "--")}' if C else None
    return p if p and os.path.isdir(p) else repo


def load(repo):
    return Qwen3TTSModel.from_pretrained(model_path(repo), device_map='cuda:0', dtype=torch.float16,
                                         attn_implementation='sdpa')


produced, load_s, compute_s = [], 0.0, 0.0


def generate(j, fn):
    global compute_s
    torch.cuda.synchronize()
    t = time.time()
    wavs, sr = fn()
    torch.cuda.synchronize()
    dt = time.time() - t
    compute_s += dt
    sf.write(f'{OUT}/{j["name"]}.wav', wavs[0], sr)
    duration = len(wavs[0]) / sr
    produced.append({'name': j['name'], 'compute_s': round(dt, 2), 'audio_s': round(duration, 2)})
    print(f'{j["name"]}: {duration:.2f} s of audio in {dt:.2f} s', flush=True)


designs = [j for j in JOBS if 'design' in j['voice']]
clones = [j for j in JOBS if 'clone' in j['voice']]
texts = {j['name']: j['text'] for j in JOBS}

if designs:
    t = time.time()
    mod = load(DESIGN)
    load_s += time.time() - t
    for j in designs:
        generate(j, lambda: mod.generate_voice_design(text=j['text'], language=j.get('language', 'Italian'),
                                                      instruct=j['voice']['design']))
    del mod
    torch.cuda.empty_cache()

if clones:
    t = time.time()
    mod = load(BASE)
    load_s += time.time() - t
    for j in clones:
        ref = j['voice']['clone']
        prompt = mod.create_voice_clone_prompt(ref_audio=f'{OUT}/{ref}.wav', ref_text=texts[ref],
                                               x_vector_only_mode=False)
        generate(j, lambda: mod.generate_voice_clone(text=j['text'], language=j.get('language', 'Italian'),
                                                     voice_clone_prompt=prompt))

m = {'device': torch.cuda.get_device_name(0), 'pip_from': pip_from, 'cache': bool(C),
     'install_s': round(install_s, 1), 'load_s': round(load_s, 1),
     'prep_s': round(install_s + load_s, 1), 'compute_s': round(compute_s, 1),
     'product': {'amount': round(sum(p['audio_s'] for p in produced), 1), 'unit': 'seconds of audio'},
     'jobs': produced, 'total_s': round(time.time() - t0, 1)}
json.dump(m, open('/kaggle/working/metrics.json', 'w'), indent=1, ensure_ascii=False)
print(json.dumps(m, indent=1, ensure_ascii=False))
