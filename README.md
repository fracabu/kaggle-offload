# kaggle-offload

Una skill per Claude Code che porta il lavoro pesante dove c'è la potenza per farlo, e tiene il conto
di quanto costa.

## Da dove nasce

Dal limite dell'hardware che c'è adesso. Il PC su cui è nata è un portatile con un Ryzen 5 3550H,
16 GB di RAM e una Radeon integrata: niente GPU NVIDIA, quindi niente CUDA, che è quello su cui gira
quasi tutto il software di AI open source. In più, su Windows, Smart App Control bloccava le librerie
native di scipy nell'ambiente di Qwen3-TTS: il modello non partiva proprio.

Il 26/11/2026 la stessa prova è stata spostata su Kaggle, che regala circa 30 ore di GPU a settimana:
6 audio in 5 minuti e 20 secondi su una Tesla T4, a costo zero. Da lì l'idea: non rinunciare a un
modello perché il PC non ce la fa, ma prendere in prestito la potenza che serve, quando serve, e
vedere quanto se ne usa. Molti non sanno che queste ore gratuite esistono, e lasciano la CPU a
macinare per ore un lavoro che una GPU fa in minuti.

L'evoluzione passo per passo è in [`DIARIO.md`](DIARIO.md).

## Cosa fa

- **Decide dove far girare un lavoro**: sul PC, su Kaggle in sola CPU (fuori quota), su Kaggle con
  GPU o TPU, o su un servizio a pagamento. Guarda l'hardware del PC su cui è lanciata, la memoria che
  serve al modello e la quota rimasta.
- **Lancia su Kaggle e aspetta il risultato**, poi scarica le uscite e registra tutto: attesa, durata
  della sessione, quota consumata, tempo di preparazione contro tempo di calcolo.
- **Raggruppa i lavori dello stesso tipo** in un solo lancio, così installazione e caricamento del
  modello si pagano una volta.
- **Tiene in cache** su Kaggle i pacchetti e i pesi dei modelli, preparati in sola CPU.
- **Misura la velocità** con un benchmark: stessi calcoli su PC, Kaggle CPU e Kaggle GPU.
- **Tiene il conto dei crediti** di tutti i servizi (Kaggle, Flow, ElevenLabs, Dreamina...) con le
  scadenze.
- **Mostra tutto in una dashboard locale**: quota, lanci in corso con il tempo che scorre, velocità,
  risparmio stimato, crediti.

## Prima volta

1. Iscriviti su [kaggle.com](https://www.kaggle.com) e verifica il numero di telefono dalle
   impostazioni dell'account: senza, GPU e internet nei notebook non si accendono.
2. Dalle impostazioni dell'account crea un token API e mettilo in `~/.kaggle/` come indica Kaggle.
3. `pip install kaggle`
4. `python scripts/ko.py setup`: se risponde `"pronto": true` sei a posto.

## Comandi

| Comando | Cosa fa |
|---|---|
| `ko.py apri` | avvia la dashboard e la apre nel browser |
| `ko.py setup` | controlla CLI, token, quota e hardware |
| `ko.py decidi --vram 8 --ore 0.5` | dove conviene far girare un lavoro GPU |
| `ko.py decidi --solo-cpu --ore 2` | idem per un lavoro solo CPU |
| `ko.py run --template qwen-tts --jobs lavori.json` | lancia un template |
| `ko.py run --script mio.py --acc gpu` | lancia uno script proprio |
| `ko.py coda aggiungi qwen-tts --file lavori.json` / `coda lancia qwen-tts` | raggruppa e lancia |
| `ko.py cache qwen-tts` | prepara la cache di un template (sola CPU) |
| `ko.py bench` | benchmark PC contro Kaggle CPU e GPU |
| `ko.py crediti` / `crediti imposta ...` | conto dei crediti |
| `ko.py quota` | quota Kaggle della settimana |

## Dati e riservatezza

I dati stanno in `~/.kaggle-offload/` sul PC, non nel repo. Il token Kaggle resta dove l'ha messo
l'utente e la skill non lo legge: usa la CLI. I kernel sono privati, ma codice e dati passano dai
server di Kaggle: niente dati personali di terzi senza un consenso esplicito.
