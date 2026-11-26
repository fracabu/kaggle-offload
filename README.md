# kaggle-offload

**Una skill per Claude Code che porta il lavoro pesante sulle GPU gratuite di Kaggle quando il tuo PC non ce la fa, e ti mostra quanto consumi, quanto vai veloce e quanto risparmi.**

Kaggle dà circa **30 ore di GPU a settimana, gratis**, a chiunque si iscriva e verifichi il telefono: una NVIDIA Tesla T4 da 16 GB, sessioni fino a 12 ore. Non è una prova di un mese, si ricaricano ogni settimana. In tanti non lo sanno, e lasciano lavori di AI sulla CPU del portatile per ore. Questa skill decide da sola dove far girare un lavoro, lo lancia su Kaggle, aspetta e riporta i risultati.

![La dashboard locale di kaggle-offload](docs/dashboard.png)

> **In English.** A Claude Code skill that checks your machine and the job, decides whether to run it locally or on Kaggle's free weekly GPU quota (about 30 h of Tesla T4), launches it, batches similar jobs, caches packages and model weights on Kaggle, and opens a local dashboard with quota, speed and estimated savings. Docs and code comments are in Italian; issues and pull requests in English are welcome.

## Quanto cambia

Lo stesso calcolo (moltiplicazioni di matrici) misurato con `ko.py bench`, il 26/11/2026:

| Macchina | GFLOPS | Rispetto al portatile |
|---|---:|---:|
| Portatile, Ryzen 5 3550H senza GPU NVIDIA | 53 | 1× |
| Kaggle, solo CPU (Xeon 4 core) | 139 | 2,6× |
| Kaggle, GPU T4, precisione piena (fp32) | 3.507 | 66× |
| Kaggle, GPU T4, mezza precisione (fp16, quella dei modelli AI) | 21.470 | 405× |

Il 405× vale per quel calcolo, non per ogni lavoro. Spesso la differenza è più semplice: sul PC il modello non parte, su Kaggle sì.

## Da dove nasce

Dal limite dell'hardware. Il PC su cui è nata è un portatile con un Ryzen 5, 16 GB di RAM e una Radeon integrata: niente GPU NVIDIA, quindi niente CUDA, su cui gira quasi tutto il software di AI open source. In più, su Windows, Smart App Control bloccava le librerie native di un modello di sintesi vocale: non partiva proprio.

La stessa prova spostata su Kaggle ha prodotto 6 audio in 5 minuti e 20 secondi, a costo zero. Da lì l'idea: non rinunciare a un modello perché il PC non ce la fa, ma prendere in prestito la potenza che serve, quando serve, e vedere quanta se ne usa. L'evoluzione passo per passo, con i numeri veri, è in [`DIARIO.md`](DIARIO.md).

## Cosa fa

- **Decide dove far girare un lavoro**: sul PC, su Kaggle in sola CPU (non consuma quota), su Kaggle con GPU o TPU, oppure ti dice che servirebbe un servizio a pagamento. Guarda l'hardware del PC, la memoria che serve al modello e la quota rimasta.
- **Lancia su Kaggle e aspetta il risultato**, scarica le uscite e registra tutto: attesa, durata della sessione, quota consumata, tempo di preparazione contro tempo di calcolo.
- **Raggruppa i lavori dello stesso tipo** in un solo lancio: installazione e caricamento del modello si pagano una volta.
- **Tiene in cache** su Kaggle i pacchetti e i pesi dei modelli, preparati in sola CPU. Sullo stesso lotto la preparazione è scesa da 97 a 71 secondi.
- **Misura la velocità** con un benchmark: stessi calcoli su PC, Kaggle CPU e Kaggle GPU.
- **Tiene il conto dei crediti** di tutti i servizi AI che usi, con le scadenze, così non ne lasci scadere nessuno.
- **Mostra tutto in una dashboard locale**: quota della settimana, lanci in corso con il tempo che scorre, velocità, risparmio stimato, crediti.

## Cosa ci puoi fare

Le ore sono tue, l'uso lo decidi tu. I casi più comuni:

- far girare modelli open che sul PC non partono: immagini, sintesi vocale, modelli linguistici piccoli e medi;
- trascrivere ore di audio e video con Whisper;
- addestrare o rifinire un modello sui tuoi dati, per esempio con LoRA;
- elaborare migliaia di testi o immagini in un colpo;
- lasciare a Kaggle i lavori lunghi che la GPU non la usano (conversioni, elaborazioni su tanti file): non consumano quota e il PC resta libero.

## Installazione

Serve Python 3. `ko.py` usa solo la libreria standard, più la Kaggle CLI.

```bash
git clone https://github.com/fracabu/kaggle-offload ~/.claude/skills/kaggle-offload
pip install kaggle
```

Poi, una volta sola:

1. Iscriviti su [kaggle.com](https://www.kaggle.com) e **verifica il numero di telefono** nelle impostazioni dell'account: senza, GPU e internet nei notebook non si accendono.
2. Nelle impostazioni dell'account crea un token API e mettilo dove indica Kaggle (`~/.kaggle/`).
3. `python ~/.claude/skills/kaggle-offload/scripts/ko.py setup`: se risponde `"pronto": true` sei a posto.

Provata su Windows 11. Il codice è pensato anche per macOS e Linux, ma lì non è ancora stato collaudato: se la provi, raccontacelo in una issue.

## Come si usa

**Con Claude Code** basta chiedere: "fallo girare su Kaggle", "sul PC non parte", "ci mette una vita sulla CPU", "quanta quota mi resta?". La skill si attiva anche da sola quando un lavoro di AI sta per girare su un PC senza GPU NVIDIA. Le istruzioni per Claude sono in [`SKILL.md`](SKILL.md).

**Da riga di comando**, con `ko.py` in `scripts/`:

| Comando | Cosa fa |
|---|---|
| `ko.py apri` | avvia la dashboard e la apre nel browser |
| `ko.py setup` | controlla CLI, token, quota e hardware |
| `ko.py decidi --vram 8 --ore 0.5` | dove conviene far girare un lavoro GPU |
| `ko.py decidi --solo-cpu --ore 2` | lo stesso per un lavoro solo CPU |
| `ko.py run --template qwen-tts --jobs lavori.json` | lancia un template |
| `ko.py run --script mio.py --acc gpu` | lancia uno script tuo |
| `ko.py coda aggiungi qwen-tts --file lavori.json` · `ko.py coda lancia qwen-tts` | raggruppa e lancia |
| `ko.py cache qwen-tts` | prepara la cache di un template (sola CPU) |
| `ko.py bench` | benchmark PC contro Kaggle CPU e GPU |
| `ko.py crediti` · `ko.py crediti imposta ...` | conto dei crediti |
| `ko.py quota` | quota Kaggle della settimana |

## Template

Un template è un lavoro già provato su Kaggle, pronto da rilanciare a lotti: una cartella in `templates/` con `main.py` e `template.json`.

| Template | Cosa fa | Stato |
|---|---|---|
| `bench` | benchmark GFLOPS su CPU e GPU | collaudato |
| `cache` | salva su Kaggle pacchetti pip e pesi Hugging Face di un altro template | collaudato |
| `qwen-tts` | Qwen3-TTS 1.7B: voci da una descrizione e cloni | collaudato |

Mancano molti modelli utili: Whisper, Flux e SDXL, addestramento LoRA, upscaling. **È il modo più semplice per contribuire**: vedi [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Dati e riservatezza

I dati della skill (registro dei lanci, quota, crediti, benchmark) stanno in `~/.kaggle-offload/` sul tuo PC, mai nel repo. Il token Kaggle resta dove l'hai messo: la skill non lo legge, usa la CLI. I kernel sono privati, ma codice e dati passano dai server di Kaggle (Google): non mandarci dati personali o riservati.

Il "risparmio" della dashboard è un'equivalenza: ore di GPU per il prezzo a consumo di una T4 su un servizio cloud. Non è denaro risparmiato davvero se senza Kaggle quel lavoro non l'avresti fatto.

## Contribuire

Template nuovi, prove su macOS e Linux, correzioni, idee: tutto è benvenuto. Parti da [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licenza

[MIT](LICENSE).
