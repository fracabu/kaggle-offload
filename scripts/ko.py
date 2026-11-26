#!/usr/bin/env python3
"""kaggle-offload: decide where a job should run (the PC, Kaggle CPU-only, Kaggle with GPU/TPU, or a
paid service), launch it on Kaggle, measure usage and speed, keep track of credits and serve the
local dashboard.

    python ko.py -h            list of commands
    python ko.py open          start the dashboard (if it is not running) and open it in the browser

Data lives in ~/.kaggle-offload (or in $KAGGLE_OFFLOAD_HOME), never inside the skill folder.

Backward compatibility: the original Italian command names (decidi, coda, crediti, apri), their
actions and flags still work, and data written by the Italian version is migrated on first run
(see migrate_data_dir).
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import datetime as dt
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / 'templates'
DASHBOARD_HTML = SKILL / 'scripts' / 'dashboard.html'
DATA = Path(os.environ.get('KAGGLE_OFFLOAD_HOME', Path.home() / '.kaggle-offload'))
LEDGER = DATA / 'ledger.jsonl'
RUNNING = DATA / 'running.json'
CREDITS = DATA / 'credits.json'
BENCH = DATA / 'bench.json'
CONFIG = DATA / 'config.json'
CACHE = DATA / 'cache.json'
QUOTA = DATA / 'quota.json'
PROBE = DATA / 'probe.json'
QUEUE = DATA / 'queue'
RUNS = DATA / 'runs'
PORT_FILE = DATA / 'port.txt'

DEFAULT_CONFIG = {
    'port': 8790,  # if it is taken by another app, the first free one after it is used
    # Reference price for the "savings" tile: what the same GPU hour would cost on a rented machine.
    'gpu_price_per_hour': 0.55,
    'currency': 'USD',
    'price_source': 'an on-demand T4 on Lightning AI, November 2026',
    'eur_rate': None,             # e.g. 0.9: if set, the dashboard also shows euros
    'elevenlabs_env_file': None,  # .env file with ELEVENLABS_API_KEY, to read credits automatically
    'expiry_warning_days': 5,
}
ACCELERATORS = {'gpu': 'NvidiaTeslaT4', 'tpu': 'TpuV5E8', 'cpu': None}
FINAL_STATES = ('complete', 'error', 'cancel_acknowledged', 'cancel_requested', 'abandoned')
_lock = threading.Lock()


# ---------------------------------------------------------------- utilities

def now():
    return dt.datetime.now().astimezone()


def iso(t=None):
    return (t or now()).isoformat(timespec='seconds')


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def show(obj):
    print(json.dumps(obj, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- old (Italian) data -> English
# Everything the Italian version wrote is read through these normalizers, so old and new records look
# the same everywhere (dashboard included). They are idempotent: English data passes through unchanged.

def _ren(d, mapping):
    """Copy of dict d with keys renamed. If the new key is already there, it wins."""
    if not isinstance(d, dict):
        return d
    return {mapping.get(k, k): v for k, v in d.items() if not (k in mapping and mapping[k] in d)}


def _tr(value, table):
    return table.get(value, value) if isinstance(value, str) else value


def _jobs_n(n):
    return f'{n} job' + ('' if n == 1 else 's')


METRIC_KEYS = {'preparazione_s': 'prep_s', 'calcolo_s': 'compute_s', 'dispositivo': 'device',
               'pacchetti': 'packages', 'lavori': 'jobs', 'carica_s': 'load_s', 'totale_s': 'total_s',
               'pip_da': 'pip_from', 'prodotto': 'product', 'nome': 'name'}
RUN_KEYS = {'progetto': 'project', 'descrizione': 'description', 'stato': 'status', 'push': 'pushed_at',
            'fine': 'finished_at', 'attesa_s': 'wait_s', 'sessione_s': 'session_s', 'totale_s': 'total_s',
            'consumo_h': 'quota_used_h', 'metriche': 'metrics', 'unita': 'unit',
            'inizio_calcolo': 'compute_started_at', 'aggiornato': 'updated_at'}
CREDIT_KEYS = {'servizio': 'service', 'residuo': 'remaining', 'totale': 'total', 'unita': 'unit',
               'scadenza': 'expires', 'tipo_scadenza': 'expiry_type', 'nota': 'note', 'fonte': 'source',
               'aggiornato': 'updated_at', 'errore': 'error'}
CONFIG_KEYS = {'porta': 'port', 'prezzo_gpu_ora': 'gpu_price_per_hour', 'valuta': 'currency',
               'fonte_prezzo': 'price_source', 'cambio_eur': 'eur_rate',
               'soglia_scadenza_giorni': 'expiry_warning_days'}
QUOTA_KEYS = {'usate': 'used', 'rimaste': 'remaining', 'totali': 'total', 'ricarica': 'refresh_at'}
PROBE_KEYS = {'sistema': 'system', 'core': 'cores', 'gpu_nvidia': 'nvidia_gpus', 'altre_gpu': 'other_gpus',
              'letto': 'read_at', 'cuda_locale': 'local_cuda'}
BENCH_KEYS = {'nome': 'name', 'dettaglio': 'detail', 'misurato': 'measured_at', 'stato': 'status'}
CACHE_KEYS = {'creata': 'created_at', 'metriche': 'metrics'}

STATUS_VALUES = {'invio': 'sending', 'abbandonato': 'abandoned', 'scarico i risultati': 'downloading results',
                 'leggo il log': 'reading log', 'sconosciuto': 'unknown'}
UNIT_VALUES = {'secondi di audio': 'seconds of audio', 'ore': 'hours', 'caratteri': 'characters'}
EXPIRY_VALUES = {'scade': 'expires', 'si ricarica': 'renews', 'si-ricarica': 'renews', 'si_ricarica': 'renews'}
SOURCE_VALUES = {'manuale': 'manual', 'automatico': 'auto'}
OLD_PRICE_SOURCE = 'una T4 a consumo su Lightning AI, novembre 2026'
OLD_ARM_GPU = 'Apple Silicon (MPS: niente CUDA, alcuni modelli girano piu lenti o non girano)'
NEW_ARM_GPU = 'Apple Silicon (MPS: no CUDA, some models run slower or not at all)'


def _norm_device(s):
    """Device strings written by the old bench template: '(4 core)', '(x2, misurata una)'."""
    if not isinstance(s, str):
        return s
    s = s.replace(', misurata una)', ', one measured)')
    return re.sub(r'\((\d+) core\)', r'(\1 cores)', s)


def norm_metrics(m):
    if not isinstance(m, dict):
        return m
    m = _ren(m, METRIC_KEYS)
    for k in ('device', 'cpu'):
        if k in m:
            m[k] = _norm_device(m[k])
    if isinstance(m.get('product'), dict):
        p = _ren(m['product'], {'quantita': 'amount', 'unita': 'unit'})
        if 'unit' in p:
            p['unit'] = _tr(p['unit'], UNIT_VALUES)
        m['product'] = p
    if isinstance(m.get('jobs'), list):
        m['jobs'] = [_ren(j, METRIC_KEYS) for j in m['jobs']]
    return m


def _norm_description(s):
    if not isinstance(s, str):
        return s
    m = re.match(r'^cache di (.+) \(sola CPU, fuori quota\)$', s)
    if m:
        return f'cache for {m.group(1)} (CPU only, outside quota)'
    m = re.match(r'^(.+): (\d+) lavori in un solo lancio( \(con cache\))?$', s)
    if m:
        return f'{m.group(1)}: {_jobs_n(int(m.group(2)))} in one run' + (' (with cache)' if m.group(3) else '')
    return s


def norm_run(r):
    if not isinstance(r, dict):
        return r
    r = _ren(r, RUN_KEYS)
    if 'status' in r:
        r['status'] = _tr(r['status'], STATUS_VALUES)
    if 'description' in r:
        r['description'] = _norm_description(r['description'])
    if 'unit' in r:
        r['unit'] = _tr(r['unit'], UNIT_VALUES)
    if 'metrics' in r:
        r['metrics'] = norm_metrics(r['metrics'])
    return r


def norm_credit(c):
    if not isinstance(c, dict):
        return c
    c = _ren(c, CREDIT_KEYS)
    for key, table in (('expiry_type', EXPIRY_VALUES), ('source', SOURCE_VALUES), ('unit', UNIT_VALUES)):
        if key in c:
            c[key] = _tr(c[key], table)
    if isinstance(c.get('note'), str):
        c['note'] = _tr(c['note'], {'quota settimanale': 'weekly quota'})
        c['note'] = re.sub(r'^piano ', 'plan ', c['note'])
    return c


def norm_config(c):
    c = _ren(c, CONFIG_KEYS)
    if isinstance(c, dict) and c.get('price_source') == OLD_PRICE_SOURCE:
        c['price_source'] = DEFAULT_CONFIG['price_source']
    return c


def norm_quota(q):
    if not isinstance(q, dict):
        return q
    q = _ren(q, {'letta': 'read_at', 'errore': 'error'})
    return {k: (_ren(v, QUOTA_KEYS) if isinstance(v, dict) else v) for k, v in q.items()}


def norm_probe(p):
    if not isinstance(p, dict):
        return p
    p = _ren(p, PROBE_KEYS)
    if isinstance(p.get('nvidia_gpus'), list):
        p['nvidia_gpus'] = [_ren(g, {'nome': 'name'}) for g in p['nvidia_gpus']]
    if isinstance(p.get('other_gpus'), list):
        p['other_gpus'] = [_tr(g, {OLD_ARM_GPU: NEW_ARM_GPU}) for g in p['other_gpus']]
    return p


def norm_bench(b):
    if not isinstance(b, dict):
        return b
    out = {}
    for k, v in b.items():
        v = _ren(v, BENCH_KEYS)
        if isinstance(v, dict):
            if 'detail' in v:
                v['detail'] = _norm_device(v['detail'])
            if isinstance(v.get('name'), str):
                v['name'] = _tr(v['name'], {'Kaggle solo CPU': 'Kaggle CPU only'})
                v['name'] = re.sub(r'^Questo PC \(', 'This PC (', v['name'])
        out[k] = v
    return out


def norm_cache(c):
    if not isinstance(c, dict):
        return c
    out = {}
    for k, v in c.items():
        v = _ren(v, CACHE_KEYS)
        if isinstance(v, dict) and 'metrics' in v:
            v['metrics'] = norm_metrics(v['metrics'])
        out[k] = v
    return out


def norm_running(d):
    return {k: norm_run(v) for k, v in d.items()} if isinstance(d, dict) else d


def norm_credits(lst):
    return [norm_credit(c) for c in lst] if isinstance(lst, list) else lst


def read_config():
    c = dict(DEFAULT_CONFIG)
    c.update(norm_config(load_json(CONFIG, {})))
    return c


def read_quota():
    return norm_quota(load_json(QUOTA, None))


def read_probe():
    return norm_probe(load_json(PROBE, None))


def read_bench():
    return norm_bench(load_json(BENCH, {}))


def read_cache():
    return norm_cache(load_json(CACHE, {}))


def read_running():
    return norm_running(load_json(RUNNING, {}))


def read_credits():
    return norm_credits(load_json(CREDITS, []))


def read_ledger(limit=None):
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            rows.append(norm_run(json.loads(line)))
        except json.JSONDecodeError:
            continue  # a half-written line must not take the dashboard down
    return rows[-limit:] if limit else rows


def migrate_data_dir():
    """One-time, idempotent migration of the data dir written by the Italian version.

    1. Renames in-corso.json -> running.json, crediti.json -> credits.json, porta.txt -> port.txt and
       the coda/ folder -> queue/, but only when the new name does not exist yet (an old file whose new
       counterpart already exists is left alone, never overwritten).
    2. Rewrites the JSON files with English keys/values (unknown keys are kept as they are). Files are
       written to a temp file and then moved over the original, and only if something changed.
    3. Rewrites ledger.jsonl the same way, keeping a one-time copy in ledger.jsonl.bak-it.
    Reads also go through the normalizers, so a stale Italian-format file never breaks anything.
    """
    if not DATA.exists():
        return
    for old, new in (('in-corso.json', 'running.json'), ('crediti.json', 'credits.json'),
                     ('porta.txt', 'port.txt'), ('coda', 'queue')):
        if (DATA / old).exists() and not (DATA / new).exists():
            try:
                (DATA / old).rename(DATA / new)
            except OSError:
                pass  # another ko.py process got there first, or the file is in use: next run retries
    for path, default, norm in ((CONFIG, {}, norm_config), (CREDITS, [], norm_credits),
                                (RUNNING, {}, norm_running), (CACHE, {}, norm_cache),
                                (BENCH, {}, norm_bench), (QUOTA, None, norm_quota),
                                (PROBE, None, norm_probe)):
        if not path.exists():
            continue
        data = load_json(path, default)
        new = norm(copy.deepcopy(data))
        if new != data:
            save_json(path, new)
    if LEDGER.exists():
        lines = [x for x in LEDGER.read_text(encoding='utf-8').splitlines() if x.strip()]
        out, changed = [], False
        for line in lines:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)  # keep what we cannot read, untouched
                continue
            new = norm_run(copy.deepcopy(rec))
            changed = changed or new != rec
            out.append(json.dumps(new, ensure_ascii=False))
        if changed:
            bak = LEDGER.with_name(LEDGER.name + '.bak-it')
            if not bak.exists():
                shutil.copy2(LEDGER, bak)
            tmp = LEDGER.with_name(LEDGER.name + '.tmp')
            tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
            os.replace(tmp, LEDGER)


# ---------------------------------------------------------------- Kaggle CLI

_KBASE = None


def kbase():
    global _KBASE
    if _KBASE is None:
        r = subprocess.run([sys.executable, '-m', 'kaggle', '--version'], capture_output=True, text=True)
        if r.returncode == 0:
            _KBASE = [sys.executable, '-m', 'kaggle', '-W']
        elif shutil.which('kaggle'):
            _KBASE = [shutil.which('kaggle'), '-W']
        else:
            raise SystemExit('Kaggle CLI not found. Install it with:  pip install kaggle')
    return _KBASE


def kaggle(*args, check=True):
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    r = subprocess.run(kbase() + [str(a) for a in args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env)
    out = (r.stdout or '') + (r.stderr or '')
    if check and r.returncode != 0:
        raise RuntimeError(f'kaggle {" ".join(map(str, args))}: {out.strip()[:800]}')
    return out


def username():
    m = re.search(r'username:\s*(\S+)', kaggle('config', 'view', check=False))
    if not m or m.group(1) == 'None':
        raise SystemExit('Kaggle is not configured: put your API token in ~/.kaggle (see the README).')
    return m.group(1).lower()


def _hours(s):
    return float(str(s).strip().rstrip('h') or 0)


def quota_live():
    out = kaggle('quota', '--format', 'json')
    rows = json.loads(out[out.find('['):])
    q = {r['resource'].lower(): {'used': _hours(r['used']), 'remaining': _hours(r['remaining']),
                                 'total': _hours(r['total']), 'refresh_at': r['refreshAt']} for r in rows}
    q['read_at'] = iso()
    save_json(QUOTA, q)
    return q


def quota_safe():
    try:
        return quota_live()
    except Exception as e:  # no network, expired token...
        q = read_quota()
        if q:
            q['error'] = str(e)[:200]
        return q


# ---------------------------------------------------------------- this PC

def _ram_gb():
    try:
        if platform.system() == 'Windows':
            class MS(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2 ** 30, 1)
        if platform.system() == 'Darwin':
            return round(int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'])) / 2 ** 30, 1)
        return round(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2 ** 30, 1)
    except Exception:
        return None


def probe():
    info = {'host': platform.node(), 'system': f'{platform.system()} {platform.release()}',
            'cpu': platform.processor() or platform.machine(), 'cores': os.cpu_count(),
            'ram_gb': _ram_gb(), 'nvidia_gpus': [], 'other_gpus': [], 'read_at': iso()}
    smi = shutil.which('nvidia-smi')
    if smi:
        r = subprocess.run([smi, '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True)
        for line in r.stdout.strip().splitlines():
            name, mem = [x.strip() for x in line.rsplit(',', 1)]
            info['nvidia_gpus'].append({'name': name, 'vram_gb': round(float(mem) / 1024, 1)})
    if platform.system() == 'Windows':
        cmd = ('$c=(Get-CimInstance Win32_Processor | Select-Object -First 1).Name;'
               '$g=@((Get-CimInstance Win32_VideoController).Name);'
               '@{cpu=$c;gpu=$g} | ConvertTo-Json -Compress')
        r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, text=True)
        try:
            w = json.loads(r.stdout)
            info['cpu'] = (w.get('cpu') or info['cpu']).strip()
            gpus = w.get('gpu') or []
            gpus = [gpus] if isinstance(gpus, str) else gpus
            info['other_gpus'] = [g for g in gpus if 'NVIDIA' not in g.upper()]
        except json.JSONDecodeError:
            pass
    elif platform.system() == 'Darwin' and platform.machine() == 'arm64':
        info['other_gpus'] = [NEW_ARM_GPU]
    info['local_cuda'] = bool(info['nvidia_gpus'])
    save_json(PROBE, info)
    return info


def decide(vram_gb=0.0, cpu_only=False, hours=0.5):
    p = probe()
    q = quota_safe()
    pc_vram = max((g['vram_gb'] for g in p['nvidia_gpus']), default=0)
    remaining = q['gpu']['remaining'] if q and 'gpu' in q else None
    if cpu_only:
        if hours <= 0.25:
            d = ('pc', 'short CPU job: preparing it for Kaggle costs more than the job itself')
        else:
            d = ('kaggle-cpu', 'long CPU job: CPU-only Kaggle sessions do not use the weekly quota '
                               '(4 cores, about 30 GB of RAM, 12 hours max) and the PC stays free')
    elif pc_vram >= vram_gb + 1:
        d = ('pc', f'the PC has an NVIDIA GPU with {pc_vram} GB, enough for {vram_gb} GB')
    elif vram_gb > 30:
        d = ('paid', f'needs {vram_gb} GB of VRAM: more than the two T4s on Kaggle (2 x 16 GB)')
    elif remaining is not None and remaining < hours:
        d = ('wait-or-pay', f'{remaining} GPU hours left, {hours} needed: '
                            f'the quota resets on {q["gpu"]["refresh_at"]}')
    else:
        where = 'one 16 GB T4' if vram_gb <= 15 else 'two T4s: the model has to be split with device_map="auto"'
        extra = ' Beyond 12 hours the job has to be split into several runs with checkpoints.' if hours > 12 else ''
        pc_reason = (f'the PC\'s NVIDIA GPU has {pc_vram} GB' if pc_vram else 'the PC has no NVIDIA GPU (CUDA)')
        left = f'{remaining} hours' if remaining is not None else 'unknown'
        d = ('kaggle-gpu', f'{pc_reason}; on Kaggle: {where}, quota left {left}.{extra}')
    return {'where': d[0], 'reason': d[1],
            'pc': {k: p[k] for k in ('host', 'cpu', 'cores', 'ram_gb', 'nvidia_gpus', 'other_gpus')},
            'gpu_quota_left_h': remaining}


# ---------------------------------------------------------------- runs on Kaggle

def set_running(run_id, remove=False, **kw):
    with _lock:
        d = read_running()
        if remove:
            d.pop(run_id, None)
        else:
            d.setdefault(run_id, {}).update(kw)
        save_json(RUNNING, d)


def prepare_kernel(name, acc, code, cache_ref=None, internet=True):
    slug = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')[:50]
    run_id = now().strftime('%Y%m%d-%H%M%S') + '-' + slug
    kdir = RUNS / run_id / 'kernel'
    kdir.mkdir(parents=True)
    (kdir / 'main.py').write_text(code, encoding='utf-8')
    ref = f'{username()}/{slug}'
    save_json(kdir / 'kernel-metadata.json', {
        'id': ref, 'title': slug, 'code_file': 'main.py', 'language': 'python', 'kernel_type': 'script',
        'is_private': 'true', 'enable_gpu': 'true' if acc == 'gpu' else 'false',
        'enable_tpu': 'true' if acc == 'tpu' else 'false', 'enable_internet': 'true' if internet else 'false',
        'dataset_sources': [], 'competition_sources': [], 'kernel_sources': [cache_ref] if cache_ref else [],
        'model_sources': []})
    return run_id, ref, kdir


def kernel_status(ref):
    out = kaggle('kernels', 'status', ref, check=False)
    m = re.search(r'status "([^"]+)"', out)
    return m.group(1).split('.')[-1].lower() if m else 'unknown'


def _log_duration(outdir):
    for f in Path(outdir).glob('*.log'):
        try:
            rows = json.loads(f.read_text(encoding='utf-8'))
            return round(max(r.get('time', 0) for r in rows), 1)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def read_metrics(outdir):
    """Metrics written by the kernel: metrics.json (or metriche.json from the Italian version)."""
    for name in ('metrics.json', 'metriche.json'):
        m = load_json(Path(outdir) / name, None)
        if m is not None:
            return norm_metrics(m)
    return None


def launch(run_id, ref, kdir, acc, project, description='', download=True, timeout=None, unit=None):
    open_dashboard(browser=True, quiet=True)
    q0 = quota_safe()
    t_push = time.time()
    args = ['kernels', 'push', '-p', kdir]
    if ACCELERATORS[acc]:
        args += ['--accelerator', ACCELERATORS[acc]]
    if timeout:
        args += ['-t', int(timeout)]
    set_running(run_id, ref=ref, acc=acc, project=project, description=description, status='sending',
                pushed_at=iso())
    try:
        out = kaggle(*args)
    except RuntimeError as e:
        set_running(run_id, remove=True)
        raise SystemExit(f'Push to Kaggle failed: {e}')
    if 'error' in out.lower() and 'success' not in out.lower():
        set_running(run_id, remove=True)
        raise SystemExit(f'Push to Kaggle failed: {out.strip()[:600]}')
    t_run, status = None, 'queued'
    while status not in FINAL_STATES:
        time.sleep(10)
        status = kernel_status(ref)
        if status == 'running' and t_run is None:
            t_run = time.time()
            set_running(run_id, compute_started_at=iso())
        set_running(run_id, status=status, updated_at=iso())
        if time.time() - t_push > 13 * 3600:
            status = 'abandoned'
    t_end = time.time()
    set_running(run_id, status='downloading results' if download else 'reading log')
    outdir = RUNS / run_id / 'output'
    outdir.mkdir(exist_ok=True)
    if download:
        kaggle('kernels', 'output', ref, '-p', outdir, '-q', check=False)
    else:  # only log and metrics: the cache stays on Kaggle, it does not come down to the PC
        kaggle('kernels', 'output', ref, '-p', outdir, '-q', '--file-pattern',
               r'(\.log$|metrics\.json$|metriche\.json$)', check=False)
    time.sleep(15)  # the quota is updated with a bit of delay
    q1 = quota_safe()
    metrics = read_metrics(outdir)
    # Each run only counts its own resource: a CPU run uses no quota. With several GPU runs in
    # parallel the quota difference is shared, so it is an upper bound.
    used = {}
    if acc in ('gpu', 'tpu') and q0 and q1 and acc in q0 and acc in q1 \
            and q0[acc]['refresh_at'] == q1[acc]['refresh_at']:
        used[acc] = round(q1[acc]['used'] - q0[acc]['used'], 2)
    rec = {'id': run_id, 'ref': ref, 'project': project, 'description': description, 'acc': acc,
           'status': status, 'pushed_at': iso(dt.datetime.fromtimestamp(t_push).astimezone()),
           'finished_at': iso(), 'wait_s': round((t_run or t_end) - t_push),
           'session_s': _log_duration(outdir), 'total_s': round(t_end - t_push), 'quota_used_h': used,
           'metrics': metrics, 'unit': unit, 'output': str(outdir)}
    with _lock:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    set_running(run_id, remove=True)
    return rec


TEMPLATE_KEYS = {'descrizione': 'description', 'unita': 'unit', 'collaudato': 'tested', 'origine': 'origin'}


def read_template_meta(name):
    """template.json, also accepting the Italian keys of the original version."""
    return _ren(load_json(TEMPLATES / name / 'template.json', {}), TEMPLATE_KEYS)


def code_from_template(name, jobs):
    t = TEMPLATES / name
    if not (t / 'main.py').exists():
        avail = ', '.join(sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir()))
        raise SystemExit(f'Template "{name}" does not exist. Available: {avail}')
    meta = read_template_meta(name)
    src = (t / 'main.py').read_text(encoding='utf-8')
    return src.replace("'__JOBS__'", repr(json.dumps(jobs, ensure_ascii=False))), meta


def run_template(template, jobs, name=None, project='', acc=None, download=True, timeout=None,
                 use_cache=True, description=''):
    code, meta = code_from_template(template, jobs)
    acc = acc or meta.get('acc', 'gpu')
    cache_ref = read_cache().get(template, {}).get('ref') if use_cache else None
    run_id, ref, kdir = prepare_kernel(name or f'ko-{template}', acc, code, cache_ref)
    descr = description or f'{template}: {_jobs_n(len(jobs))} in one run' + (' (with cache)' if cache_ref else '')
    return launch(run_id, ref, kdir, acc, project, descr, download, timeout, meta.get('unit'))


# ---------------------------------------------------------------- commands

def cmd_probe(a):
    show(probe())


def cmd_quota(a):
    show(quota_live())


def cmd_decide(a):
    show(decide(a.vram, a.cpu_only, a.hours))


def cmd_run(a):
    if a.template:
        jobs = json.loads(Path(a.jobs).read_text(encoding='utf-8')) if a.jobs else []
        rec = run_template(a.template, jobs, a.name, a.project, a.acc, not a.no_download, a.timeout,
                           not a.no_cache, a.description)
    elif a.script:
        code = Path(a.script).read_text(encoding='utf-8')
        acc = a.acc or 'gpu'
        run_id, ref, kdir = prepare_kernel(a.name or Path(a.script).stem, acc, code, internet=not a.no_internet)
        rec = launch(run_id, ref, kdir, acc, a.project, a.description or Path(a.script).name,
                     not a.no_download, a.timeout)
    else:
        raise SystemExit('Pass --template or --script')
    show(rec)


def _read_jsonl(f):
    return [json.loads(r) for r in f.read_text(encoding='utf-8').splitlines() if r.strip()]


def cmd_queue(a):
    QUEUE.mkdir(parents=True, exist_ok=True)
    if a.action == 'list':
        show({f.stem: _read_jsonl(f) for f in QUEUE.glob('*.jsonl')})
        return
    if not a.template:
        raise SystemExit('Pass the template name')
    f = QUEUE / f'{a.template}.jsonl'
    if a.action == 'add':
        if not (a.file or a.job):
            raise SystemExit('Pass --job (one job as JSON) or --file (a JSON file)')
        new = json.loads(Path(a.file).read_text(encoding='utf-8')) if a.file else [json.loads(a.job)]
        with f.open('a', encoding='utf-8') as h:
            for j in (new if isinstance(new, list) else [new]):
                h.write(json.dumps(j, ensure_ascii=False) + '\n')
        print(f'Queued for {a.template}: {_jobs_n(len(f.read_text(encoding="utf-8").splitlines()))}')
    elif a.action == 'launch':
        jobs = _read_jsonl(f) if f.exists() else []
        if not jobs:
            raise SystemExit(f'Queue for {a.template} is empty')
        rec = run_template(a.template, jobs, project=a.project)
        if rec['status'] == 'complete':
            f.unlink()
        show(rec)
    elif a.action == 'clear':
        f.unlink(missing_ok=True)


def cmd_cache(a):
    meta = read_template_meta(a.template)
    pip_pkgs, hf = meta.get('cache_pip', []), meta.get('cache_hf', [])
    if not (pip_pkgs or hf):
        raise SystemExit(f'Template {a.template} does not declare anything to cache')
    code, _ = code_from_template('cache', [{'template': a.template, 'pip': pip_pkgs, 'hf': hf}])
    run_id, ref, kdir = prepare_kernel(f'ko-cache-{a.template}', 'cpu', code)
    rec = launch(run_id, ref, kdir, 'cpu', a.project or 'cache',
                 f'cache for {a.template} (CPU only, outside quota)', download=False)
    if rec['status'] == 'complete':
        c = read_cache()
        c[a.template] = {'ref': ref, 'created_at': iso(), 'pip': pip_pkgs, 'hf': hf,
                         'metrics': rec.get('metrics')}
        save_json(CACHE, c)
    show(rec)


def _gflops_local():
    try:
        import numpy as np
    except ImportError:
        return None
    n = 2048
    a = np.random.rand(n, n).astype(np.float32)
    b = np.random.rand(n, n).astype(np.float32)
    a @ b
    best = min(_time_it(lambda: a @ b) for _ in range(5))
    return round(2 * n ** 3 / best / 1e9, 1)


def _time_it(fn):
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def cmd_bench(a):
    open_dashboard(browser=True, quiet=True)
    p = probe()
    res = read_bench()
    res['pc'] = {'name': f'This PC ({p["host"]})', 'detail': p['cpu'], 'gflops_fp32': _gflops_local(),
                 'measured_at': iso()}
    save_json(BENCH, res)
    outcomes = {}

    def one(acc):
        code, _ = code_from_template('bench', [{'acc': acc}])
        run_id, ref, kdir = prepare_kernel(f'ko-bench-{acc}', acc, code)
        outcomes[acc] = launch(run_id, ref, kdir, acc, 'benchmark', f'benchmark {acc.upper()}')

    threads = [threading.Thread(target=one, args=(acc,)) for acc in (['cpu'] if a.cpu_only else ['cpu', 'gpu'])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    res = read_bench()
    for acc, rec in outcomes.items():
        m = rec.get('metrics') or {}
        res[f'kaggle_{acc}'] = {'name': f'Kaggle {"GPU" if acc == "gpu" else "CPU only"}',
                                'detail': m.get('device'), 'gflops_fp32': m.get('gflops_fp32'),
                                'gflops_fp16': m.get('gflops_fp16'), 'measured_at': iso(),
                                'status': rec['status']}
    save_json(BENCH, res)
    show(res)


def eleven_auto(c):
    key = os.environ.get('ELEVENLABS_API_KEY')
    if not key and c.get('elevenlabs_env_file') and Path(c['elevenlabs_env_file']).exists():
        for line in Path(c['elevenlabs_env_file']).read_text(encoding='utf-8').splitlines():
            m = re.match(r'\s*ELEVENLABS_API_KEY\s*=\s*["\']?([^"\'\s]+)', line)
            if m:
                key = m.group(1)
                break
    if not key:
        return None
    req = urllib.request.Request('https://api.elevenlabs.io/v1/user/subscription', headers={'xi-api-key': key})
    s = json.load(urllib.request.urlopen(req, timeout=15))
    reset = s.get('next_character_count_reset_unix')
    return {'service': 'ElevenLabs', 'remaining': s['character_limit'] - s['character_count'],
            'total': s['character_limit'], 'unit': 'characters',
            'expires': dt.date.fromtimestamp(reset).isoformat() if reset else None,
            'expiry_type': 'renews', 'note': f'plan {s.get("tier", "?")}', 'source': 'auto',
            'updated_at': iso()}


def refresh_credits_auto():
    c = read_config()
    cred = {x['service']: x for x in read_credits()}
    q = quota_safe()
    if q and 'gpu' in q:
        for r, name in (('gpu', 'Kaggle GPU'), ('tpu', 'Kaggle TPU')):
            cred[name] = {'service': name, 'remaining': q[r]['remaining'], 'total': q[r]['total'],
                          'unit': 'hours', 'expires': q[r]['refresh_at'][:10], 'expiry_type': 'renews',
                          'note': 'weekly quota', 'source': 'auto', 'updated_at': q['read_at']}
    try:
        e = eleven_auto(c)
        if e:
            cred['ElevenLabs'] = e
    except Exception as ex:
        if 'ElevenLabs' in cred:
            cred['ElevenLabs']['error'] = str(ex)[:120]
    save_json(CREDITS, list(cred.values()))
    return list(cred.values())


def cmd_credits(a):
    cred = {x['service']: x for x in read_credits()}
    if a.action in ('set', 'remove') and not a.service:
        raise SystemExit('Pass --service')
    if a.action == 'set':
        x = cred.get(a.service, {'service': a.service})
        for k in ('remaining', 'total', 'unit', 'expires', 'expiry_type', 'note'):
            v = getattr(a, k)
            if v is not None:
                x[k] = v
        x.update(source='manual', updated_at=iso())
        cred[a.service] = x
        save_json(CREDITS, list(cred.values()))
    elif a.action == 'remove':
        cred.pop(a.service, None)
        save_json(CREDITS, list(cred.values()))
    elif a.action == 'refresh':
        refresh_credits_auto()
    show(read_credits())


# ---------------------------------------------------------------- dashboard

def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def is_ours(port):
    """True if this dashboard (and not another app, or the old Italian one) answers on the port."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=1) as r:
            return json.load(r).get('app') == 'kaggle-offload' and r.headers.get('X-Kaggle-Offload') == '2'
    except Exception:
        return False


def active_port():
    """The dashboard's port if it is already running, otherwise None."""
    try:
        p = int(PORT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        p = read_config()['port']
    return p if is_ours(p) else None


def pick_port():
    base = read_config()['port']
    for p in range(base, base + 30):
        if not port_in_use(p):
            return p
    raise SystemExit(f'No free port between {base} and {base + 29}')


def full_state():
    return {'now': iso(), 'config': read_config(), 'probe': read_probe(), 'quota': read_quota(),
            'running': read_running(), 'runs': read_ledger(300), 'bench': read_bench(),
            'credits': read_credits(), 'cache': read_cache(),
            'queue': {f.stem: len([r for r in f.read_text(encoding='utf-8').splitlines() if r.strip()])
                      for f in QUEUE.glob('*.jsonl')} if QUEUE.exists() else {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._send(DASHBOARD_HTML.read_bytes(), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/ping'):
            self._send(b'{"app": "kaggle-offload"}', 'application/json', extra={'X-Kaggle-Offload': '2'})
        elif self.path.startswith(('/api/state', '/api/stato')):  # /api/stato: old name, kept as alias
            self._send(json.dumps(full_state(), ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')
        else:
            self._send(b'not found', 'text/plain', 404)


def _updater():
    rounds = 0
    while True:
        try:
            if rounds % 10 == 0:
                refresh_credits_auto()  # also reads the quota
            elif read_running():
                quota_safe()
        except Exception:
            pass
        rounds += 1
        time.sleep(60)


def cmd_dashboard(a):
    active = active_port()
    if active:
        print(f'Dashboard already running at http://127.0.0.1:{active}')
        if not a.no_browser:
            webbrowser.open(f'http://127.0.0.1:{active}')
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if not PROBE.exists():
        probe()
    port = pick_port()
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    PORT_FILE.write_text(str(port))
    threading.Thread(target=_updater, daemon=True).start()
    print(f'Dashboard at http://127.0.0.1:{port}  (Ctrl+C to stop it)', flush=True)
    if not a.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(f'http://127.0.0.1:{port}',)).start()
    srv.serve_forever()


def open_dashboard(browser=True, quiet=False):
    """Start the dashboard in a separate process if it is not running yet, then open it."""
    active = active_port()
    if active:
        if browser and not quiet:
            webbrowser.open(f'http://127.0.0.1:{active}')
        return active
    args = [sys.executable, str(Path(__file__).resolve()), 'dashboard'] + ([] if browser else ['--no-browser'])
    kw = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'stdin': subprocess.DEVNULL}
    if platform.system() == 'Windows':
        kw['creationflags'] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    subprocess.Popen(args, **kw)
    for _ in range(40):
        active = active_port()
        if active:
            return active
        time.sleep(0.25)
    return None


def cmd_open(a):
    port = open_dashboard(browser=True)
    print(f'http://127.0.0.1:{port}' if port else 'The dashboard did not start: try python ko.py dashboard')


def cmd_setup(a):
    DATA.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        save_json(CONFIG, DEFAULT_CONFIG)
    result = {'data': str(DATA), 'config': str(CONFIG)}
    try:
        kbase()
        result['kaggle_cli'] = kaggle('--version').strip()
        result['user'] = username()
        q = quota_live()
        result['gpu_quota'] = q.get('gpu')
        result['ready'] = True
    except (SystemExit, RuntimeError) as e:
        result['ready'] = False
        result['problem'] = str(e)
    result['pc'] = probe()
    show(result)


# ---------------------------------------------------------------- command line

# Italian names from the original version: still accepted, but not shown in --help.
ACTION_ALIASES = {'aggiungi': 'add', 'vedi': 'list', 'lancia': 'launch', 'svuota': 'clear',
                  'imposta': 'set', 'togli': 'remove', 'aggiorna': 'refresh'}
CREDIT_ACTION_ALIASES = {**ACTION_ALIASES, 'vedi': 'show'}  # 'vedi' meant "show" for credits, "list" for queue


def _action(table):
    return lambda s: table.get(s, s)


def _expiry_type(s):
    return EXPIRY_VALUES.get(s, s)


def _opt(parser, flag, old_flag=None, **kw):
    """add_argument with a hidden alias flag (the old Italian one) storing into the same dest."""
    kw.setdefault('dest', flag.lstrip('-').replace('-', '_'))
    parser.add_argument(flag, **kw)
    if old_flag:
        parser.add_argument(old_flag, **{**kw, 'help': argparse.SUPPRESS})


def main():
    migrate_quietly()
    ap = argparse.ArgumentParser(prog='ko.py', description='kaggle-offload: where to run the work, and what it costs')
    sub = ap.add_subparsers(dest='cmd', required=True, metavar='command')

    def command(name, help, old=None):
        p = sub.add_parser(name, help=help)
        if old:  # hidden alias: registered in the parser map, so it works but is not listed in --help
            sub._name_parser_map[old] = p
        return p

    command('setup', 'check CLI, token and quota; create ~/.kaggle-offload').set_defaults(f=cmd_setup)
    command('probe', 'hardware of this PC').set_defaults(f=cmd_probe)
    command('quota', 'Kaggle quota for the week').set_defaults(f=cmd_quota)

    s = command('decide', 'where a job should run', old='decidi')
    _opt(s, '--vram', type=float, default=0, help='GB of VRAM the model needs')
    _opt(s, '--cpu-only', '--solo-cpu', action='store_true',
         help='the job does not use the GPU (Whisper on CPU, ffmpeg...)')
    _opt(s, '--hours', '--ore', type=float, default=0.5, help='estimated duration in hours')
    s.set_defaults(f=cmd_decide)

    s = command('run', 'launch a job on Kaggle and wait for the result')
    s.add_argument('--template')
    s.add_argument('--jobs', help='JSON file with the list of jobs for the template')
    s.add_argument('--script', help='your own Python script, run as it is')
    _opt(s, '--name', '--nome')
    _opt(s, '--project', '--progetto', default='')
    _opt(s, '--description', '--descrizione', default='')
    s.add_argument('--acc', choices=['cpu', 'gpu', 'tpu'])
    s.add_argument('--timeout', type=int, help='maximum run time in seconds')
    _opt(s, '--no-download', '--no-scarica', action='store_true', help='download only the log and metrics')
    _opt(s, '--no-cache', '--senza-cache', action='store_true')
    s.add_argument('--no-internet', action='store_true')
    s.set_defaults(f=cmd_run)

    s = command('queue', 'group jobs and launch them together', old='coda')
    s.add_argument('action', type=_action(ACTION_ALIASES), choices=['add', 'list', 'launch', 'clear'])
    s.add_argument('template', nargs='?')
    s.add_argument('--job', help='one job as JSON')
    s.add_argument('--file', help='JSON file with one or more jobs')
    _opt(s, '--project', '--progetto', default='')
    s.set_defaults(f=cmd_queue)

    s = command('cache', 'build the cache (packages and weights) of a template, CPU only')
    s.add_argument('template')
    _opt(s, '--project', '--progetto', default='')
    s.set_defaults(f=cmd_cache)

    s = command('bench', 'measure GFLOPS of the PC, Kaggle CPU and Kaggle GPU')
    _opt(s, '--cpu-only', '--solo-cpu', action='store_true', help='skip the GPU run (uses no GPU quota)')
    s.set_defaults(f=cmd_bench)

    s = command('credits', 'credit balance of all services', old='crediti')
    s.add_argument('action', nargs='?', default='show', type=_action(CREDIT_ACTION_ALIASES),
                   choices=['show', 'set', 'remove', 'refresh'])
    _opt(s, '--service', '--servizio')
    _opt(s, '--remaining', '--residuo', type=float)
    _opt(s, '--total', '--totale', type=float)
    _opt(s, '--unit', '--unita')
    _opt(s, '--expires', '--scadenza', help='YYYY-MM-DD')
    _opt(s, '--expiry-type', '--tipo-scadenza', type=_expiry_type, choices=['expires', 'renews'],
         help='"expires" or "renews"')
    _opt(s, '--note', '--nota')
    s.set_defaults(f=cmd_credits)

    s = command('dashboard', 'serve the dashboard (stays in the foreground)')
    _opt(s, '--no-browser', '--senza-browser', action='store_true')
    s.set_defaults(f=cmd_dashboard)

    command('open', 'start the dashboard in the background and open it', old='apri').set_defaults(f=cmd_open)
    a = ap.parse_args()
    a.f(a)


def migrate_quietly():
    try:
        migrate_data_dir()
    except Exception as e:  # a migration problem must never block a command
        print(f'warning: data migration skipped ({e})', file=sys.stderr)


if __name__ == '__main__':
    main()
