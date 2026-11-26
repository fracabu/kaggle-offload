# kaggle-offload: benchmark. Measures GFLOPS (matrix multiplications) on CPU and, if there is one, on GPU.
# Writes /kaggle/working/metrics.json, which the dashboard reads.
import json
import os
import time

JOBS = json.loads('__JOBS__')
OUT = '/kaggle/working'
t0 = time.time()


def cpu_name():
    try:
        for line in open('/proc/cpuinfo'):
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return 'CPU'


def time_it(fn, repeat):
    t = time.perf_counter()
    for _ in range(repeat):
        fn()
    return time.perf_counter() - t


import numpy as np

n = 2048
a = np.random.rand(n, n).astype(np.float32)
b = np.random.rand(n, n).astype(np.float32)
a @ b
best = min(time_it(lambda: a @ b, 1) for _ in range(5))
m = {'cpu': f'{cpu_name()} ({os.cpu_count()} cores)', 'gflops_cpu_fp32': round(2 * n ** 3 / best / 1e9, 1)}
m['device'] = m['cpu']
m['gflops_fp32'] = m['gflops_cpu_fp32']

try:
    import torch
    if torch.cuda.is_available():
        m['device'] = f'{torch.cuda.get_device_name(0)} (x{torch.cuda.device_count()}, one measured)'
        n = 8192
        for dtype, key in ((torch.float32, 'gflops_fp32'), (torch.float16, 'gflops_fp16')):
            x = torch.randn(n, n, device='cuda', dtype=dtype)
            y = torch.randn(n, n, device='cuda', dtype=dtype)
            for _ in range(3):
                x @ y
            torch.cuda.synchronize()
            repeat = 10
            t = time.perf_counter()
            for _ in range(repeat):
                x @ y
            torch.cuda.synchronize()
            m[key] = round(2 * n ** 3 * repeat / (time.perf_counter() - t) / 1e9, 1)
            del x, y
            torch.cuda.empty_cache()
except ImportError:
    pass

m['prep_s'] = 0
m['compute_s'] = round(time.time() - t0, 1)
json.dump(m, open(f'{OUT}/metrics.json', 'w'), indent=1)
print(json.dumps(m, indent=1))
