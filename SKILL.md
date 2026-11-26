---
name: kaggle-offload
description: >-
  Decide dove far girare un lavoro pesante (sul PC, su Kaggle in sola CPU, su Kaggle con GPU o TPU
  gratis, o su un servizio a pagamento) guardando l'hardware del PC su cui è lanciata, la memoria che
  serve al modello e la quota Kaggle rimasta; lo lancia su Kaggle, raggruppa i lavori simili in un solo
  lancio, tiene in cache pacchetti e pesi, e apre una dashboard locale con quota, lanci in corso,
  velocità misurata, risparmio stimato e crediti di tutti i servizi con le scadenze. Usala quando un
  modello AI non gira sul PC o ci mette una vita sulla CPU, quando serve una GPU, quando si vuole
  sapere quanta quota o quanti crediti restano, anche se l'utente non nomina Kaggle.
when_to_use: >-
  "fallo girare su Kaggle", "usa le GPU gratis", "sul PC non parte", "ci mette una vita", "è
  lentissimo sulla CPU", "non ho la GPU", "quanta quota mi resta?", "quanti crediti ho?", "apri la
  dashboard delle GPU", "benchmark del PC"; oppure quando un lavoro di AI (TTS, Whisper, diffusione,
  face swap, motion control, fine-tuning) sta per girare su CPU o su un PC senza GPU NVIDIA.
---

# kaggle-offload

Lo strumento è `scripts/ko.py` (solo libreria standard di Python, più la Kaggle CLI). Da qui in poi
`KO` vuol dire `python ~/.claude/skills/kaggle-offload/scripts/ko.py`. I dati (registro dei lanci,
quota, crediti, benchmark, cache, coda) stanno in `~/.kaggle-offload/`, mai nella cartella della skill.

## Procedura

1. **Apri la dashboard per prima cosa.** `KO apri`: parte in background dalla porta 8790 (o dalla prima
   libera: l'indirizzo lo stampa il comando) e si apre nel browser. Anche `run`, `bench` e `cache` la aprono da soli se non gira già.
2. **Primo uso su un PC: `KO setup`.** Controlla CLI, token, quota e hardware. Se dice
   `pronto: false`, guida l'utente con "Prima volta" del README: iscrizione, verifica del telefono
   (senza, la GPU non si accende), token. Il token lo mette l'utente: **non leggerlo e non stamparlo**.
3. **Decidi dove gira.** Stima la VRAM che serve al modello (pesi in fp16 ≈ 2 GB per miliardo di
   parametri, più un margine) e la durata, poi `KO decidi --vram <GB> --ore <h>` oppure
   `KO decidi --solo-cpu --ore <h>`. Riporta all'utente la decisione e il motivo in una riga.
   - `pc`: lascia perdere Kaggle.
   - `kaggle-gpu` / `kaggle-cpu`: vai al passo 4.
   - `a-pagamento` o `aspetta-o-paga`: dillo con i numeri (VRAM, ore rimaste, data di ricarica) e
     lascia decidere all'utente. Non attivare servizi a pagamento da solo.
4. **Lancia.**
   - Se esiste un template in `templates/` (`qwen-tts`, `bench`, `cache`): scrivi i lavori in un JSON e
     `KO run --template <t> --jobs lavori.json --progetto <nome>`.
   - **Più lavori dello stesso tipo vanno in un solo lancio:** `KO coda aggiungi <t> --file lavori.json`,
     poi `KO coda lancia <t>`. Installazione e caricamento del modello si pagano una volta sola
     (nella prova del Qwen erano 97 s su 320).
   - Se non c'è un template: scrivi uno script e `KO run --script file.py --acc gpu|cpu|tpu`. Regole per
     lo script: le uscite in `/kaggle/working`; scrivi `/kaggle/working/metriche.json` con almeno
     `preparazione_s`, `calcolo_s` e, se ha senso, `prodotto: {quantita, unita}`, perché la dashboard ne
     ricava velocità e quota di preparazione; stampa i progressi con `flush=True`; nessuna chiave nel
     codice (vanno in Kaggle Secrets).
   - La prima volta che uno script funziona e servirà ancora, **trasformalo in template**: cartella in
     `templates/<nome>/` con `main.py` (i lavori arrivano in `JOBS = json.loads('__JOBS__')`) e
     `template.json` (`acc`, `vram_gb`, `unita`, `cache_pip`, `cache_hf`, `collaudato`, `origine`).
     Non scrivere template per modelli mai provati.
5. **Cache.** Quando un template viene usato per la seconda volta, proponi `KO cache <t>`: gira in sola
   CPU (fuori quota), salva su Kaggle le wheel che mancano all'immagine e i pesi Hugging Face; da lì i
   lanci li montano invece di riscaricarli. La cache non scende sul PC.
6. **Lavori lunghi senza GPU** (Whisper su CPU, conversioni, scraping, ffmpeg su tanti file): sopra il
   quarto d'ora proponi Kaggle in sola CPU (`--acc cpu`). Non consuma quota e il PC resta libero.
   Kaggle ha 4 core: non sempre è più veloce del PC, il vantaggio è non tenerlo occupato. I dati dei
   benchmark nella dashboard dicono quanto.
7. **Dopo ogni lancio** riporta in due o tre righe: stato, attesa, sessione, quota consumata,
   preparazione contro calcolo, risultati scaricati (percorso in `output`). Se il lancio ha insegnato
   qualcosa (un errore, un tempo, un limite), aggiungilo a `DIARIO.md`: è il materiale dei post.

## Accorgersi dei lavori lenti

Proponi la skill anche senza che l'utente la chieda quando vedi: `torch.cuda.is_available()` falso,
`device='cpu'` in un modello AI, Whisper o un modello di diffusione lanciati sul PC, un'installazione
bloccata da librerie native (es. Smart App Control su Windows), o l'utente che dice che il lavoro "ci
mette una vita". Una riga sola, con la stima: «Sul PC non c'è CUDA: su Kaggle una T4 lo fa in circa X.»

## Crediti

`KO crediti` mostra il conto; la quota Kaggle si aggiorna da sola. Gli altri servizi si aggiornano a
mano con `KO crediti imposta --servizio <nome> --residuo <n> --unita <u> --scadenza AAAA-MM-GG
--tipo-scadenza "scade"|"si ricarica" --nota "..."`, ogni volta che l'utente dice un numero nuovo.
ElevenLabs si legge in automatico solo se l'utente imposta `ELEVENLABS_API_KEY` nell'ambiente o un
`elevenlabs_env_file` in `~/.kaggle-offload/config.json`. **Non andare a prendere chiavi dai file di
altri progetti di tua iniziativa.**

## Limiti da dire quando contano

- Quota GPU di circa 30 h a settimana (quella esatta è in `KO quota`), TPU 20 h, massimo 9 h per sessione TPU e 12 h per sessione GPU o CPU.
  La settimana si ricarica alla data `refreshAt` di `KO quota`.
- La quota si conta dall'avvio della sessione, preparazione compresa: per questo i lavori si raggruppano.
- I kernel sono privati, ma il codice e i dati passano dai server di Kaggle (Google): niente dati
  personali di clienti o lead senza l'ok esplicito dell'utente.
- Il "risparmio" della dashboard è un'equivalenza: ore di GPU per il prezzo a consumo di una T4
  (`prezzo_gpu_ora` e `fonte_prezzo` in config). Non è denaro risparmiato davvero se senza Kaggle il
  lavoro non si sarebbe fatto.
