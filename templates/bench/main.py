# kaggle-offload: benchmark. Misura i GFLOPS (moltiplicazioni di matrici) su CPU e, se c'e', su GPU.
# Scrive /kaggle/working/metriche.json, letto dalla dashboard.
import json
import os
import time

JOBS = json.loads('__JOBS__')
OUT = '/kaggle/working'
t0 = time.time()


def cpu_nome():
    try:
        for riga in open('/proc/cpuinfo'):
            if riga.startswith('model name'):
                return riga.split(':', 1)[1].strip()
    except OSError:
        pass
    return 'CPU'


def cronometra(fn, ripeti):
    t = time.perf_counter()
    for _ in range(ripeti):
        fn()
    return time.perf_counter() - t


import numpy as np

n = 2048
a = np.random.rand(n, n).astype(np.float32)
b = np.random.rand(n, n).astype(np.float32)
a @ b
migliore = min(cronometra(lambda: a @ b, 1) for _ in range(5))
m = {'cpu': f'{cpu_nome()} ({os.cpu_count()} core)', 'gflops_cpu_fp32': round(2 * n ** 3 / migliore / 1e9, 1)}
m['dispositivo'] = m['cpu']
m['gflops_fp32'] = m['gflops_cpu_fp32']

try:
    import torch
    if torch.cuda.is_available():
        m['dispositivo'] = f'{torch.cuda.get_device_name(0)} (x{torch.cuda.device_count()}, misurata una)'
        n = 8192
        for dtype, chiave in ((torch.float32, 'gflops_fp32'), (torch.float16, 'gflops_fp16')):
            x = torch.randn(n, n, device='cuda', dtype=dtype)
            y = torch.randn(n, n, device='cuda', dtype=dtype)
            for _ in range(3):
                x @ y
            torch.cuda.synchronize()
            ripeti = 10
            t = time.perf_counter()
            for _ in range(ripeti):
                x @ y
            torch.cuda.synchronize()
            m[chiave] = round(2 * n ** 3 * ripeti / (time.perf_counter() - t) / 1e9, 1)
            del x, y
            torch.cuda.empty_cache()
except ImportError:
    pass

m['preparazione_s'] = 0
m['calcolo_s'] = round(time.time() - t0, 1)
json.dump(m, open(f'{OUT}/metriche.json', 'w'), indent=1)
print(json.dumps(m, indent=1))
