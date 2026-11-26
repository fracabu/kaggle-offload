#!/usr/bin/env python3
"""kaggle-offload: decide dove far girare un lavoro (PC, Kaggle in sola CPU, Kaggle con GPU/TPU,
servizio a pagamento), lo lancia su Kaggle, misura consumi e velocita', tiene il conto dei crediti
e serve la dashboard locale.

    python ko.py -h            elenco dei comandi
    python ko.py apri          avvia la dashboard (se non gira gia') e la apre nel browser

I dati stanno in ~/.kaggle-offload (o in $KAGGLE_OFFLOAD_HOME), mai nella cartella della skill.
"""
from __future__ import annotations

import argparse
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
INCORSO = DATA / 'in-corso.json'
CREDITI = DATA / 'crediti.json'
BENCH = DATA / 'bench.json'
CONFIG = DATA / 'config.json'
CACHE = DATA / 'cache.json'
QUOTA = DATA / 'quota.json'
PROBE = DATA / 'probe.json'
CODA = DATA / 'coda'
RUNS = DATA / 'runs'

DEFAULT_CONFIG = {
    'porta': 8790,  # se e' occupata da un'altra app si passa alla prima libera
    # Prezzo di riferimento per il "risparmio": quanto costerebbe la stessa ora di GPU a noleggio.
    'prezzo_gpu_ora': 0.55,
    'valuta': 'USD',
    'fonte_prezzo': 'una T4 a consumo su Lightning AI, novembre 2026',
    'cambio_eur': None,          # es. 0.9: se impostato la dashboard mostra anche gli euro
    'elevenlabs_env_file': None,  # file .env con ELEVENLABS_API_KEY, per leggere i crediti in automatico
    'soglia_scadenza_giorni': 5,
}
ACCELERATORI = {'gpu': 'NvidiaTeslaT4', 'tpu': 'TpuV5E8', 'cpu': None}
FINALI = ('complete', 'error', 'cancel_acknowledged', 'cancel_requested', 'abbandonato')
_lock = threading.Lock()


# ---------------------------------------------------------------- utilita'

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


def config():
    c = dict(DEFAULT_CONFIG)
    c.update(load_json(CONFIG, {}))
    return c


def ledger(limit=None):
    if not LEDGER.exists():
        return []
    righe = [json.loads(r) for r in LEDGER.read_text(encoding='utf-8').splitlines() if r.strip()]
    return righe[-limit:] if limit else righe


def stampa(obj):
    print(json.dumps(obj, indent=1, ensure_ascii=False))


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
            raise SystemExit('Kaggle CLI non trovata. Installala con:  pip install kaggle')
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
        raise SystemExit('Kaggle non configurato: segui "Prima volta" nel README (token in ~/.kaggle).')
    return m.group(1).lower()


def _ore(s):
    return float(str(s).strip().rstrip('h') or 0)


def quota_live():
    out = kaggle('quota', '--format', 'json')
    righe = json.loads(out[out.find('['):])
    q = {r['resource'].lower(): {'usate': _ore(r['used']), 'rimaste': _ore(r['remaining']),
                                 'totali': _ore(r['total']), 'ricarica': r['refreshAt']} for r in righe}
    q['letta'] = iso()
    save_json(QUOTA, q)
    return q


def quota_sicura():
    try:
        return quota_live()
    except Exception as e:  # rete assente, token scaduto...
        q = load_json(QUOTA, None)
        if q:
            q['errore'] = str(e)[:200]
        return q


# ---------------------------------------------------------------- questo PC

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
    info = {'host': platform.node(), 'sistema': f'{platform.system()} {platform.release()}',
            'cpu': platform.processor() or platform.machine(), 'core': os.cpu_count(),
            'ram_gb': _ram_gb(), 'gpu_nvidia': [], 'altre_gpu': [], 'letto': iso()}
    smi = shutil.which('nvidia-smi')
    if smi:
        r = subprocess.run([smi, '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True)
        for riga in r.stdout.strip().splitlines():
            nome, mem = [x.strip() for x in riga.rsplit(',', 1)]
            info['gpu_nvidia'].append({'nome': nome, 'vram_gb': round(float(mem) / 1024, 1)})
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
            info['altre_gpu'] = [g for g in gpus if 'NVIDIA' not in g.upper()]
        except json.JSONDecodeError:
            pass
    elif platform.system() == 'Darwin' and platform.machine() == 'arm64':
        info['altre_gpu'] = ['Apple Silicon (MPS: niente CUDA, alcuni modelli girano piu lenti o non girano)']
    info['cuda_locale'] = bool(info['gpu_nvidia'])
    save_json(PROBE, info)
    return info


def decidi(vram_gb=0.0, solo_cpu=False, ore=0.5):
    p = probe()
    q = quota_sicura()
    vram_pc = max((g['vram_gb'] for g in p['gpu_nvidia']), default=0)
    rimaste = q['gpu']['rimaste'] if q and 'gpu' in q else None
    if solo_cpu:
        if ore <= 0.25:
            d = ('pc', 'lavoro CPU breve: prepararlo per Kaggle costa piu del lavoro stesso')
        else:
            d = ('kaggle-cpu', 'lavoro CPU lungo: le sessioni Kaggle in sola CPU non consumano la quota '
                               'settimanale (4 core, circa 30 GB di RAM, massimo 12 ore) e il PC resta libero')
    elif vram_pc >= vram_gb + 1:
        d = ('pc', f'il PC ha una GPU NVIDIA da {vram_pc} GB, basta per {vram_gb} GB')
    elif vram_gb > 30:
        d = ('a-pagamento', f'servono {vram_gb} GB di VRAM: oltre le due T4 di Kaggle (2 x 16 GB)')
    elif rimaste is not None and rimaste < ore:
        d = ('aspetta-o-paga', f'restano {rimaste} ore GPU, ne servono {ore}: '
                               f'la quota si ricarica il {q["gpu"]["ricarica"]}')
    else:
        dove = 'una T4 da 16 GB' if vram_gb <= 15 else 'due T4: il modello va diviso con device_map="auto"'
        extra = ' Sopra le 12 ore va spezzato in piu lanci con checkpoint.' if ore > 12 else ''
        motivo_pc = (f'la GPU NVIDIA del PC ha {vram_pc} GB' if vram_pc else 'il PC non ha una GPU NVIDIA (CUDA)')
        d = ('kaggle-gpu', f'{motivo_pc}; su Kaggle {dove}, quota rimasta {rimaste} ore.{extra}')
    return {'dove': d[0], 'motivo': d[1], 'pc': {k: p[k] for k in ('host', 'cpu', 'core', 'ram_gb', 'gpu_nvidia', 'altre_gpu')},
            'quota_gpu_rimasta_h': rimaste}


# ---------------------------------------------------------------- lanci su Kaggle

def set_incorso(run_id, rimuovi=False, **kw):
    with _lock:
        d = load_json(INCORSO, {})
        if rimuovi:
            d.pop(run_id, None)
        else:
            d.setdefault(run_id, {}).update(kw)
        save_json(INCORSO, d)


def prepara_kernel(nome, acc, codice, cache_ref=None, internet=True):
    slug = re.sub(r'[^a-z0-9-]+', '-', nome.lower()).strip('-')[:50]
    run_id = now().strftime('%Y%m%d-%H%M%S') + '-' + slug
    kdir = RUNS / run_id / 'kernel'
    kdir.mkdir(parents=True)
    (kdir / 'main.py').write_text(codice, encoding='utf-8')
    ref = f'{username()}/{slug}'
    save_json(kdir / 'kernel-metadata.json', {
        'id': ref, 'title': slug, 'code_file': 'main.py', 'language': 'python', 'kernel_type': 'script',
        'is_private': 'true', 'enable_gpu': 'true' if acc == 'gpu' else 'false',
        'enable_tpu': 'true' if acc == 'tpu' else 'false', 'enable_internet': 'true' if internet else 'false',
        'dataset_sources': [], 'competition_sources': [], 'kernel_sources': [cache_ref] if cache_ref else [],
        'model_sources': []})
    return run_id, ref, kdir


def stato_kernel(ref):
    out = kaggle('kernels', 'status', ref, check=False)
    m = re.search(r'status "([^"]+)"', out)
    return m.group(1).split('.')[-1].lower() if m else 'sconosciuto'


def _durata_log(outdir):
    for f in Path(outdir).glob('*.log'):
        try:
            righe = json.loads(f.read_text(encoding='utf-8'))
            return round(max(r.get('time', 0) for r in righe), 1)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def lancia(run_id, ref, kdir, acc, progetto, descrizione='', scarica=True, timeout=None, unita=None):
    apri_dashboard(browser=True, silenzioso=True)
    q0 = quota_sicura()
    t_push = time.time()
    args = ['kernels', 'push', '-p', kdir]
    if ACCELERATORI[acc]:
        args += ['--accelerator', ACCELERATORI[acc]]
    if timeout:
        args += ['-t', int(timeout)]
    set_incorso(run_id, ref=ref, acc=acc, progetto=progetto, descrizione=descrizione, stato='invio', push=iso())
    try:
        out = kaggle(*args)
    except RuntimeError as e:
        set_incorso(run_id, rimuovi=True)
        raise SystemExit(f'Invio a Kaggle fallito: {e}')
    if 'error' in out.lower() and 'success' not in out.lower():
        set_incorso(run_id, rimuovi=True)
        raise SystemExit(f'Invio a Kaggle fallito: {out.strip()[:600]}')
    t_run, stato = None, 'queued'
    while stato not in FINALI:
        time.sleep(10)
        stato = stato_kernel(ref)
        if stato == 'running' and t_run is None:
            t_run = time.time()
            set_incorso(run_id, inizio_calcolo=iso())
        set_incorso(run_id, stato=stato, aggiornato=iso())
        if time.time() - t_push > 13 * 3600:
            stato = 'abbandonato'
    t_fine = time.time()
    set_incorso(run_id, stato='scarico i risultati' if scarica else 'leggo il log')
    outdir = RUNS / run_id / 'output'
    outdir.mkdir(exist_ok=True)
    if scarica:
        kaggle('kernels', 'output', ref, '-p', outdir, '-q', check=False)
    else:  # solo log e metriche: la cache resta su Kaggle, non scende sul PC
        kaggle('kernels', 'output', ref, '-p', outdir, '-q', '--file-pattern', r'(\.log$|metriche\.json$)', check=False)
    time.sleep(15)  # la quota si aggiorna con un po' di ritardo
    q1 = quota_sicura()
    metriche = load_json(outdir / 'metriche.json', None)
    # Ogni lancio conta solo la propria risorsa: un lancio CPU non consuma quota. Con piu' lanci GPU in
    # parallelo la differenza di quota e' condivisa, quindi vale come limite superiore.
    consumo = {}
    if acc in ('gpu', 'tpu') and q0 and q1 and acc in q0 and acc in q1 \
            and q0[acc]['ricarica'] == q1[acc]['ricarica']:
        consumo[acc] = round(q1[acc]['usate'] - q0[acc]['usate'], 2)
    rec = {'id': run_id, 'ref': ref, 'progetto': progetto, 'descrizione': descrizione, 'acc': acc,
           'stato': stato, 'push': iso(dt.datetime.fromtimestamp(t_push).astimezone()), 'fine': iso(),
           'attesa_s': round((t_run or t_fine) - t_push), 'sessione_s': _durata_log(outdir),
           'totale_s': round(t_fine - t_push), 'consumo_h': consumo, 'metriche': metriche,
           'unita': unita, 'output': str(outdir)}
    with _lock:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    set_incorso(run_id, rimuovi=True)
    return rec


def codice_da_template(nome, jobs):
    t = TEMPLATES / nome
    if not (t / 'main.py').exists():
        disp = ', '.join(sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir()))
        raise SystemExit(f'Template "{nome}" inesistente. Disponibili: {disp}')
    meta = load_json(t / 'template.json', {})
    src = (t / 'main.py').read_text(encoding='utf-8')
    return src.replace("'__JOBS__'", repr(json.dumps(jobs, ensure_ascii=False))), meta


def esegui_template(template, jobs, nome=None, progetto='', acc=None, scarica=True, timeout=None,
                    usa_cache=True, descrizione=''):
    codice, meta = codice_da_template(template, jobs)
    acc = acc or meta.get('acc', 'gpu')
    cache_ref = load_json(CACHE, {}).get(template, {}).get('ref') if usa_cache else None
    run_id, ref, kdir = prepara_kernel(nome or f'ko-{template}', acc, codice, cache_ref)
    descr = descrizione or f'{template}: {len(jobs)} lavori in un solo lancio' + (' (con cache)' if cache_ref else '')
    return lancia(run_id, ref, kdir, acc, progetto, descr, scarica, timeout, meta.get('unita'))


# ---------------------------------------------------------------- comandi

def cmd_probe(a):
    stampa(probe())


def cmd_quota(a):
    stampa(quota_live())


def cmd_decidi(a):
    stampa(decidi(a.vram, a.solo_cpu, a.ore))


def cmd_run(a):
    if a.template:
        jobs = json.loads(Path(a.jobs).read_text(encoding='utf-8')) if a.jobs else []
        rec = esegui_template(a.template, jobs, a.nome, a.progetto, a.acc, not a.no_scarica, a.timeout,
                              not a.senza_cache, a.descrizione)
    elif a.script:
        codice = Path(a.script).read_text(encoding='utf-8')
        acc = a.acc or 'gpu'
        run_id, ref, kdir = prepara_kernel(a.nome or Path(a.script).stem, acc, codice, internet=not a.no_internet)
        rec = lancia(run_id, ref, kdir, acc, a.progetto, a.descrizione or Path(a.script).name,
                     not a.no_scarica, a.timeout)
    else:
        raise SystemExit('Serve --template oppure --script')
    stampa(rec)


def cmd_coda(a):
    CODA.mkdir(parents=True, exist_ok=True)
    if a.azione == 'vedi':
        stampa({f.stem: [json.loads(r) for r in f.read_text(encoding='utf-8').splitlines() if r.strip()]
                for f in CODA.glob('*.jsonl')})
        return
    if not a.template:
        raise SystemExit('Serve il nome del template')
    f = CODA / f'{a.template}.jsonl'
    if a.azione == 'aggiungi':
        nuovi = json.loads(Path(a.file).read_text(encoding='utf-8')) if a.file else [json.loads(a.job)]
        with f.open('a', encoding='utf-8') as h:
            for j in (nuovi if isinstance(nuovi, list) else [nuovi]):
                h.write(json.dumps(j, ensure_ascii=False) + '\n')
        print(f'In coda per {a.template}: {len(f.read_text(encoding="utf-8").splitlines())} lavori')
    elif a.azione == 'lancia':
        jobs = [json.loads(r) for r in f.read_text(encoding='utf-8').splitlines() if r.strip()] if f.exists() else []
        if not jobs:
            raise SystemExit(f'Coda di {a.template} vuota')
        rec = esegui_template(a.template, jobs, progetto=a.progetto)
        if rec['stato'] == 'complete':
            f.unlink()
        stampa(rec)
    elif a.azione == 'svuota':
        f.unlink(missing_ok=True)


def cmd_cache(a):
    t = TEMPLATES / a.template
    meta = load_json(t / 'template.json', {})
    pip_pkgs, hf = meta.get('cache_pip', []), meta.get('cache_hf', [])
    if not (pip_pkgs or hf):
        raise SystemExit(f'Il template {a.template} non dichiara niente da mettere in cache')
    codice, _ = codice_da_template('cache', [{'template': a.template, 'pip': pip_pkgs, 'hf': hf}])
    run_id, ref, kdir = prepara_kernel(f'ko-cache-{a.template}', 'cpu', codice)
    rec = lancia(run_id, ref, kdir, 'cpu', a.progetto or 'cache', f'cache di {a.template} (sola CPU, fuori quota)',
                 scarica=False)
    if rec['stato'] == 'complete':
        c = load_json(CACHE, {})
        c[a.template] = {'ref': ref, 'creata': iso(), 'pip': pip_pkgs, 'hf': hf, 'metriche': rec.get('metriche')}
        save_json(CACHE, c)
    stampa(rec)


def _gflops_locale():
    try:
        import numpy as np
    except ImportError:
        return None
    n = 2048
    a = np.random.rand(n, n).astype(np.float32)
    b = np.random.rand(n, n).astype(np.float32)
    a @ b
    migliore = min(_cronometra(lambda: a @ b) for _ in range(5))
    return round(2 * n ** 3 / migliore / 1e9, 1)


def _cronometra(fn):
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def cmd_bench(a):
    apri_dashboard(browser=True, silenzioso=True)
    p = probe()
    ris = load_json(BENCH, {})
    ris['pc'] = {'nome': f'Questo PC ({p["host"]})', 'dettaglio': p['cpu'], 'gflops_fp32': _gflops_locale(),
                 'misurato': iso()}
    save_json(BENCH, ris)
    esiti = {}

    def uno(acc):
        codice, _ = codice_da_template('bench', [{'acc': acc}])
        run_id, ref, kdir = prepara_kernel(f'ko-bench-{acc}', acc, codice)
        esiti[acc] = lancia(run_id, ref, kdir, acc, 'benchmark', f'benchmark {acc.upper()}')

    fili = [threading.Thread(target=uno, args=(acc,)) for acc in (['cpu'] if a.solo_cpu else ['cpu', 'gpu'])]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    ris = load_json(BENCH, {})
    for acc, rec in esiti.items():
        m = rec.get('metriche') or {}
        ris[f'kaggle_{acc}'] = {'nome': f'Kaggle {"GPU" if acc == "gpu" else "solo CPU"}',
                                'dettaglio': m.get('dispositivo'), 'gflops_fp32': m.get('gflops_fp32'),
                                'gflops_fp16': m.get('gflops_fp16'), 'misurato': iso(), 'stato': rec['stato']}
    save_json(BENCH, ris)
    stampa(ris)


def eleven_auto(c):
    key = os.environ.get('ELEVENLABS_API_KEY')
    if not key and c.get('elevenlabs_env_file') and Path(c['elevenlabs_env_file']).exists():
        for riga in Path(c['elevenlabs_env_file']).read_text(encoding='utf-8').splitlines():
            m = re.match(r'\s*ELEVENLABS_API_KEY\s*=\s*["\']?([^"\'\s]+)', riga)
            if m:
                key = m.group(1)
                break
    if not key:
        return None
    req = urllib.request.Request('https://api.elevenlabs.io/v1/user/subscription', headers={'xi-api-key': key})
    s = json.load(urllib.request.urlopen(req, timeout=15))
    reset = s.get('next_character_count_reset_unix')
    return {'servizio': 'ElevenLabs', 'residuo': s['character_limit'] - s['character_count'],
            'totale': s['character_limit'], 'unita': 'caratteri',
            'scadenza': dt.date.fromtimestamp(reset).isoformat() if reset else None,
            'tipo_scadenza': 'si ricarica', 'nota': f'piano {s.get("tier", "?")}', 'fonte': 'automatico',
            'aggiornato': iso()}


def aggiorna_crediti_auto():
    c = config()
    cred = {x['servizio']: x for x in load_json(CREDITI, [])}
    q = quota_sicura()
    if q and 'gpu' in q:
        for r, nome in (('gpu', 'Kaggle GPU'), ('tpu', 'Kaggle TPU')):
            cred[nome] = {'servizio': nome, 'residuo': q[r]['rimaste'], 'totale': q[r]['totali'], 'unita': 'ore',
                          'scadenza': q[r]['ricarica'][:10], 'tipo_scadenza': 'si ricarica',
                          'nota': 'quota settimanale', 'fonte': 'automatico', 'aggiornato': q['letta']}
    try:
        e = eleven_auto(c)
        if e:
            cred['ElevenLabs'] = e
    except Exception as ex:
        if 'ElevenLabs' in cred:
            cred['ElevenLabs']['errore'] = str(ex)[:120]
    save_json(CREDITI, list(cred.values()))
    return list(cred.values())


def cmd_crediti(a):
    cred = {x['servizio']: x for x in load_json(CREDITI, [])}
    if a.azione == 'imposta':
        x = cred.get(a.servizio, {'servizio': a.servizio})
        for k in ('residuo', 'totale', 'unita', 'scadenza', 'tipo_scadenza', 'nota'):
            v = getattr(a, k)
            if v is not None:
                x[k] = v
        x.update(fonte='manuale', aggiornato=iso())
        cred[a.servizio] = x
        save_json(CREDITI, list(cred.values()))
    elif a.azione == 'togli':
        cred.pop(a.servizio, None)
        save_json(CREDITI, list(cred.values()))
    elif a.azione == 'aggiorna':
        aggiorna_crediti_auto()
    stampa(load_json(CREDITI, []))


# ---------------------------------------------------------------- dashboard

PORTA_FILE = DATA / 'porta.txt'


def porta_occupata(porta):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', porta)) == 0


def e_nostra(porta):
    """True se sulla porta risponde questa dashboard (e non un'altra app)."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{porta}/api/ping', timeout=1) as r:
            return json.load(r).get('app') == 'kaggle-offload'
    except Exception:
        return False


def porta_attiva():
    """La porta della dashboard se gira gia', altrimenti None."""
    try:
        p = int(PORTA_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        p = config()['porta']
    return p if e_nostra(p) else None


def scegli_porta():
    base = config()['porta']
    for p in range(base, base + 30):
        if not porta_occupata(p):
            return p
    raise SystemExit(f'Nessuna porta libera fra {base} e {base + 29}')


def stato_completo():
    return {'ora': iso(), 'config': config(), 'probe': load_json(PROBE, None), 'quota': load_json(QUOTA, None),
            'in_corso': load_json(INCORSO, {}), 'lanci': ledger(300), 'bench': load_json(BENCH, {}),
            'crediti': load_json(CREDITI, []), 'cache': load_json(CACHE, {}),
            'coda': {f.stem: len([r for r in f.read_text(encoding='utf-8').splitlines() if r.strip()])
                     for f in CODA.glob('*.jsonl')} if CODA.exists() else {}}


class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _invia(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._invia(DASHBOARD_HTML.read_bytes(), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/ping'):
            self._invia(b'{"app": "kaggle-offload"}', 'application/json')
        elif self.path.startswith('/api/stato'):
            self._invia(json.dumps(stato_completo(), ensure_ascii=False).encode('utf-8'),
                        'application/json; charset=utf-8')
        else:
            self._invia(b'not found', 'text/plain', 404)


def _aggiornatore():
    giri = 0
    while True:
        try:
            if giri % 10 == 0:
                aggiorna_crediti_auto()  # legge anche la quota
            elif load_json(INCORSO, {}):
                quota_sicura()
        except Exception:
            pass
        giri += 1
        time.sleep(60)


def cmd_dashboard(a):
    attiva = porta_attiva()
    if attiva:
        print(f'Dashboard gia attiva su http://127.0.0.1:{attiva}')
        if not a.senza_browser:
            webbrowser.open(f'http://127.0.0.1:{attiva}')
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if not PROBE.exists():
        probe()
    porta = scegli_porta()
    srv = ThreadingHTTPServer(('127.0.0.1', porta), Gestore)
    PORTA_FILE.write_text(str(porta))
    threading.Thread(target=_aggiornatore, daemon=True).start()
    print(f'Dashboard su http://127.0.0.1:{porta}  (Ctrl+C per fermarla)', flush=True)
    if not a.senza_browser:
        threading.Timer(0.8, webbrowser.open, args=(f'http://127.0.0.1:{porta}',)).start()
    srv.serve_forever()


def apri_dashboard(browser=True, silenzioso=False):
    """Avvia la dashboard in un processo separato se non gira gia', poi la apre."""
    attiva = porta_attiva()
    if attiva:
        if browser and not silenzioso:
            webbrowser.open(f'http://127.0.0.1:{attiva}')
        return attiva
    args =[sys.executable, str(Path(__file__).resolve()), 'dashboard'] + ([] if browser else ['--senza-browser'])
    kw = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'stdin': subprocess.DEVNULL}
    if platform.system() == 'Windows':
        kw['creationflags'] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    subprocess.Popen(args, **kw)
    for _ in range(40):
        attiva = porta_attiva()
        if attiva:
            return attiva
        time.sleep(0.25)
    return None


def cmd_apri(a):
    porta = apri_dashboard(browser=True)
    print(f'http://127.0.0.1:{porta}' if porta else 'La dashboard non e partita: prova python ko.py dashboard')


def cmd_setup(a):
    DATA.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        save_json(CONFIG, DEFAULT_CONFIG)
    esito = {'dati': str(DATA), 'config': str(CONFIG)}
    try:
        kbase()
        esito['kaggle_cli'] = kaggle('--version').strip()
        esito['utente'] = username()
        q = quota_live()
        esito['quota_gpu'] = q.get('gpu')
        esito['pronto'] = True
    except (SystemExit, RuntimeError) as e:
        esito['pronto'] = False
        esito['problema'] = str(e)
    esito['pc'] = probe()
    stampa(esito)


def main():
    ap = argparse.ArgumentParser(prog='ko.py', description='kaggle-offload: dove far girare il lavoro, e quanto costa')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup', help='controlla CLI, token e quota; crea ~/.kaggle-offload').set_defaults(f=cmd_setup)
    sub.add_parser('probe', help='hardware di questo PC').set_defaults(f=cmd_probe)
    sub.add_parser('quota', help='quota Kaggle della settimana').set_defaults(f=cmd_quota)
    s = sub.add_parser('decidi', help='dove conviene far girare un lavoro')
    s.add_argument('--vram', type=float, default=0, help='GB di VRAM che servono al modello')
    s.add_argument('--solo-cpu', action='store_true', help='il lavoro non usa la GPU (Whisper su CPU, ffmpeg...)')
    s.add_argument('--ore', type=float, default=0.5, help='durata stimata in ore')
    s.set_defaults(f=cmd_decidi)
    s = sub.add_parser('run', help='lancia un lavoro su Kaggle e aspetta il risultato')
    s.add_argument('--template')
    s.add_argument('--jobs', help='file JSON con la lista dei lavori per il template')
    s.add_argument('--script', help='script Python proprio da far girare cosi com\'e')
    s.add_argument('--nome')
    s.add_argument('--progetto', default='')
    s.add_argument('--descrizione', default='')
    s.add_argument('--acc', choices=['cpu', 'gpu', 'tpu'])
    s.add_argument('--timeout', type=int, help='secondi massimi di esecuzione')
    s.add_argument('--no-scarica', action='store_true', help='scarica solo log e metriche')
    s.add_argument('--senza-cache', action='store_true')
    s.add_argument('--no-internet', action='store_true')
    s.set_defaults(f=cmd_run)
    s = sub.add_parser('coda', help='raggruppa lavori e lanciali insieme')
    s.add_argument('azione', choices=['aggiungi', 'vedi', 'lancia', 'svuota'])
    s.add_argument('template', nargs='?')
    s.add_argument('--job', help='un lavoro in JSON')
    s.add_argument('--file', help='file JSON con uno o piu lavori')
    s.add_argument('--progetto', default='')
    s.set_defaults(f=cmd_coda)
    s = sub.add_parser('cache', help='prepara la cache (pacchetti e pesi) di un template, in sola CPU')
    s.add_argument('template')
    s.add_argument('--progetto', default='')
    s.set_defaults(f=cmd_cache)
    s = sub.add_parser('bench', help='misura i GFLOPS di PC, Kaggle CPU e Kaggle GPU')
    s.add_argument('--solo-cpu', action='store_true', help='non usa quota GPU')
    s.set_defaults(f=cmd_bench)
    s = sub.add_parser('crediti', help='conto dei crediti di tutti i servizi')
    s.add_argument('azione', nargs='?', default='vedi', choices=['vedi', 'imposta', 'togli', 'aggiorna'])
    s.add_argument('--servizio')
    s.add_argument('--residuo', type=float)
    s.add_argument('--totale', type=float)
    s.add_argument('--unita')
    s.add_argument('--scadenza', help='AAAA-MM-GG')
    s.add_argument('--tipo-scadenza', dest='tipo_scadenza', help='"scade" oppure "si ricarica"')
    s.add_argument('--nota')
    s.set_defaults(f=cmd_crediti)
    s = sub.add_parser('dashboard', help='serve la dashboard (resta in primo piano)')
    s.add_argument('--senza-browser', action='store_true')
    s.set_defaults(f=cmd_dashboard)
    sub.add_parser('apri', help='avvia la dashboard in background e la apre').set_defaults(f=cmd_apri)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
