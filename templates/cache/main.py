# kaggle-offload: costruttore di cache. Gira in sola CPU (fuori quota).
# Mette in /kaggle/working i pacchetti pip che mancano all'immagine Kaggle (come wheel) e i pesi dei
# modelli Hugging Face. I lanci successivi montano questo output come kernel_source e non riscaricano.
import json
import os
import subprocess
import sys
import time

JOBS = json.loads('__JOBS__')
J = JOBS[0]
OUT = '/kaggle/working'
t0 = time.time()
m = {'template': J['template']}

W = f'{OUT}/wheels'
os.makedirs(W, exist_ok=True)
pins = []
if J.get('pip'):
    # Solo cio' che l'immagine Kaggle non ha gia': lo dice pip stesso con --dry-run --report.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--dry-run', '--quiet', '--report',
                    '/tmp/pip-report.json', *J['pip']], check=True)
    rep = json.load(open('/tmp/pip-report.json'))
    pins = [f"{i['metadata']['name']}=={i['metadata']['version']}" for i in rep.get('install', [])]
    if pins:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-q', '-w', W, *pins], check=True)
m['pacchetti'] = pins
m['pip_s'] = round(time.time() - t0, 1)

t1 = time.time()
from huggingface_hub import snapshot_download

for repo in J.get('hf', []):
    snapshot_download(repo_id=repo, local_dir=f'{OUT}/models/{repo.replace("/", "--")}')
m['hf_s'] = round(time.time() - t1, 1)


def dimensione(cartella):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(cartella) for f in fs)


m['gb'] = round(dimensione(OUT) / 2 ** 30, 2)
json.dump({'template': J['template'], 'pip': pins, 'hf': J.get('hf', [])}, open(f'{OUT}/ko-cache.json', 'w'))
m['preparazione_s'] = 0
m['calcolo_s'] = round(time.time() - t0, 1)
json.dump(m, open(f'{OUT}/metriche.json', 'w'), indent=1)
print(json.dumps(m, indent=1))
