# Contribuire a kaggle-offload

Grazie se sei qui. L'obiettivo del progetto è semplice: far usare a più persone possibile le ore di GPU gratuite di Kaggle, senza sprecarle. Ogni contributo che va in quella direzione è benvenuto.

> **In English:** contributions in English are welcome. Open an issue or a pull request and write in the language you prefer.

## Cosa serve di più

1. **Template per nuovi modelli.** È il contributo più utile. Quelli che mancano:
   - Whisper (trascrizione di audio e video a lotti);
   - Flux o SDXL (immagini a lotti);
   - addestramento LoRA (immagini coerenti di un prodotto o di un personaggio);
   - upscaling di immagini e video;
   - un modello linguistico piccolo per elaborare testi a lotti.
2. **Prove su macOS e Linux.** La skill è collaudata solo su Windows 11. `ko.py setup`, `ko.py bench` e `ko.py apri` su un altro sistema bastano per una issue preziosa.
3. **Correzioni e idee.** Un bug, un limite di Kaggle cambiato, un modo per sprecare meno quota.

## Come scrivere un template

Un template è una cartella in `templates/<nome>/` con due file.

**`main.py`**: lo script che gira su Kaggle.
- I lavori arrivano in `JOBS = json.loads('__JOBS__')`: `ko.py` sostituisce `'__JOBS__'` con la lista dei lavori in JSON. Un lancio esegue tutti i lavori della lista, così installazione e caricamento del modello si pagano una volta.
- Le uscite vanno in `/kaggle/working`.
- Scrivi `/kaggle/working/metriche.json` con almeno `preparazione_s` (installazione e caricamento) e `calcolo_s` (lavoro vero) e, se ha senso, `prodotto: {"quantita": ..., "unita": "..."}` (per esempio secondi di audio o immagini). Da qui la dashboard ricava velocità e costo di preparazione.
- Se usi la cache, cercala con `ko-cache.json` sotto `/kaggle/input` e, se non c'è, installa da internet: guarda `templates/qwen-tts/main.py`.
- Stampa i progressi con `print(..., flush=True)`.
- Nessuna chiave nel codice: se servono, vanno nei Secrets di Kaggle.

**`template.json`**:

```json
{
 "descrizione": "cosa fa, in una riga",
 "acc": "gpu",
 "vram_gb": 8,
 "unita": "secondi di audio",
 "cache_pip": ["pacchetto"],
 "cache_hf": ["organizzazione/modello"],
 "collaudato": false,
 "origine": "quando e come è stato provato, con i tempi"
}
```

**La regola più importante:** `"collaudato": true` solo dopo un lancio vero su Kaggle andato a buon fine. Nella pull request scrivi i numeri di quel lancio: GPU, sessione, preparazione, calcolo, quota consumata. Li trovi nell'output di `ko.py run`.

## Regole per il codice

- `scripts/ko.py` usa **solo la libreria standard** di Python, più la Kaggle CLI. Niente dipendenze nuove sul PC: i pacchetti pesanti vanno nei template, su Kaggle.
- La dashboard (`scripts/dashboard.html`) è un file solo, senza librerie esterne né CDN.
- Nomi, messaggi e commenti sono in italiano. Se scrivi in inglese va bene lo stesso: li adattiamo noi.
- I dati dell'utente stanno in `~/.kaggle-offload/` e non entrano mai nel repo: niente token, registri dei lanci o crediti nelle pull request.
- La skill non legge il token Kaggle né chiavi di altri servizi da file di altri progetti. Le chiavi le collega l'utente.

## Il diario

[`DIARIO.md`](DIARIO.md) racconta come si evolve il progetto, con i numeri veri. Se il tuo contributo ha insegnato qualcosa (un tempo misurato, un errore, un limite), aggiungi una voce datata.

## Come proporre una modifica

1. Apri una issue per discutere modifiche grandi. Per un template nuovo o una correzione piccola puoi andare dritto alla pull request.
2. Fai un fork, crea un branch (`template-whisper`, `fix-porta-linux`...), fai le modifiche.
3. Controlla che `python scripts/ko.py setup` e `python scripts/ko.py apri` funzionino ancora.
4. Apri la pull request spiegando cosa cambia e, per un template, con i numeri del lancio di prova.

## Comportamento

Si discute nel merito, con gentilezza. Non sono ammessi attacchi personali, molestie o contenuti offensivi.
