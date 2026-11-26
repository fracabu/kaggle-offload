# kaggle-offload: cache builder. Runs on CPU only (outside the quota).
# Puts in /kaggle/working the pip packages the Kaggle image lacks (as wheels) and the weights of the
# Hugging Face models. Later runs mount this output as a kernel_source and do not download again.
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
    # Only what the Kaggle image does not already have: pip itself says so with --dry-run --report.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--dry-run', '--quiet', '--report',
                    '/tmp/pip-report.json', *J['pip']], check=True)
    rep = json.load(open('/tmp/pip-report.json'))
    pins = [f"{i['metadata']['name']}=={i['metadata']['version']}" for i in rep.get('install', [])]
    if pins:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-q', '-w', W, *pins], check=True)
m['packages'] = pins
m['pip_s'] = round(time.time() - t0, 1)

t1 = time.time()
from huggingface_hub import snapshot_download

for repo in J.get('hf', []):
    snapshot_download(repo_id=repo, local_dir=f'{OUT}/models/{repo.replace("/", "--")}')
m['hf_s'] = round(time.time() - t1, 1)


def folder_size(folder):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(folder) for f in fs)


m['gb'] = round(folder_size(OUT) / 2 ** 30, 2)
# ko-cache.json is how later runs find this cache under /kaggle/input: keep the name.
json.dump({'template': J['template'], 'pip': pins, 'hf': J.get('hf', [])}, open(f'{OUT}/ko-cache.json', 'w'))
m['prep_s'] = 0
m['compute_s'] = round(time.time() - t0, 1)
json.dump(m, open(f'{OUT}/metrics.json', 'w'), indent=1)
print(json.dumps(m, indent=1))
