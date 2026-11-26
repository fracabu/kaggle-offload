# kaggle-offload: Qwen3-TTS a lavori raggruppati, su GPU T4.
# Un lancio = tutti i lavori in coda: installazione e caricamento dei modelli si pagano una volta sola.
# Lavoro: {"nome": "x", "testo": "...", "lingua": "Italian", "voce": {"design": "descrizione della voce"}}
#     o:  {"nome": "y", "testo": "...", "voce": {"clona": "x"}}   (clona la voce di un lavoro precedente)
# Uscite: /kaggle/working/out/<nome>.wav e /kaggle/working/metriche.json
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


def trova_cache():
    for radice, cartelle, file in os.walk('/kaggle/input'):
        if 'ko-cache.json' in file:
            return radice
        if radice.count(os.sep) > 6:
            cartelle[:] = []
    return None


C = trova_cache()


def installa():
    w = f'{C}/wheels' if C else None
    if w and os.path.isdir(w) and os.listdir(w):
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--no-index', '--find-links', w, 'qwen-tts'])
        if r.returncode == 0:
            return 'cache'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'qwen-tts'], check=True)
    return 'internet'


fonte_pip = installa()
install_s = time.time() - t0

import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel


def percorso(repo):
    p = f'{C}/models/{repo.replace("/", "--")}' if C else None
    return p if p and os.path.isdir(p) else repo


def carica(repo):
    return Qwen3TTSModel.from_pretrained(percorso(repo), device_map='cuda:0', dtype=torch.float16,
                                         attn_implementation='sdpa')


prodotti, carica_s, calcolo_s = [], 0.0, 0.0


def genera(j, fn):
    global calcolo_s
    torch.cuda.synchronize()
    t = time.time()
    wavs, sr = fn()
    torch.cuda.synchronize()
    dt = time.time() - t
    calcolo_s += dt
    sf.write(f'{OUT}/{j["nome"]}.wav', wavs[0], sr)
    durata = len(wavs[0]) / sr
    prodotti.append({'nome': j['nome'], 'calcolo_s': round(dt, 2), 'audio_s': round(durata, 2)})
    print(f'{j["nome"]}: {durata:.2f} s di audio in {dt:.2f} s', flush=True)


design = [j for j in JOBS if 'design' in j['voce']]
cloni = [j for j in JOBS if 'clona' in j['voce']]
testi = {j['nome']: j['testo'] for j in JOBS}

if design:
    t = time.time()
    mod = carica(DESIGN)
    carica_s += time.time() - t
    for j in design:
        genera(j, lambda: mod.generate_voice_design(text=j['testo'], language=j.get('lingua', 'Italian'),
                                                    instruct=j['voce']['design']))
    del mod
    torch.cuda.empty_cache()

if cloni:
    t = time.time()
    mod = carica(BASE)
    carica_s += time.time() - t
    for j in cloni:
        rif = j['voce']['clona']
        prompt = mod.create_voice_clone_prompt(ref_audio=f'{OUT}/{rif}.wav', ref_text=testi[rif],
                                               x_vector_only_mode=False)
        genera(j, lambda: mod.generate_voice_clone(text=j['testo'], language=j.get('lingua', 'Italian'),
                                                   voice_clone_prompt=prompt))

m = {'dispositivo': torch.cuda.get_device_name(0), 'pip_da': fonte_pip, 'cache': bool(C),
     'install_s': round(install_s, 1), 'carica_s': round(carica_s, 1),
     'preparazione_s': round(install_s + carica_s, 1), 'calcolo_s': round(calcolo_s, 1),
     'prodotto': {'quantita': round(sum(p['audio_s'] for p in prodotti), 1), 'unita': 'secondi di audio'},
     'lavori': prodotti, 'totale_s': round(time.time() - t0, 1)}
json.dump(m, open('/kaggle/working/metriche.json', 'w'), indent=1, ensure_ascii=False)
print(json.dumps(m, indent=1, ensure_ascii=False))
