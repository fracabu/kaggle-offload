# Diario di kaggle-offload

Come si evolve la skill, con i numeri veri. È anche la fonte dei post.

## 26/11/2026 — la prova che l'ha fatta nascere

- **Il limite:** portatile con Ryzen 5 3550H, 16 GB di RAM, Radeon integrata. Niente CUDA. Qwen3-TTS
  installato in locale non parte: Smart App Control blocca le DLL di scipy.
- **La prova su Kaggle:** stesso modello (Qwen3-TTS 1.7B, VoiceDesign e Base) su una Tesla T4.
  6 audio in 5 min 20 s. Dentro ci sono 38 s di installazione e 59 s di caricamento dei modelli:
  97 s su 320, quasi un terzo, solo per prepararsi. Ogni frase di 7-9 s ha chiesto 19-34 s di calcolo.
- **Esito:** per la voce del personaggio resta ElevenLabs, che suona più naturale. Ma la strada per i
  modelli open che sul PC non girano è aperta, a costo zero.
- **Quota dopo le prove del giorno:** 0,22 h di GPU su 30.

## 26/11/2026 — nasce la skill

- **Idea:** non sapere più "se il PC ce la fa", ma lasciare che la skill decida dove far girare il
  lavoro e mostri quanto si consuma, si risparmia e quanto si va veloci.
- **Scoperta utile:** la Kaggle CLI 2.2.5 ha `kaggle quota`, che dà ore usate, rimaste e data di
  ricarica. I consumi si leggono dal dato vero, non si stimano.
- **Scelte:**
  - Il nome è `kaggle-offload`.
  - Nella prima versione ci sono i lavori raggruppati con la cache, i lavori in sola CPU fuori quota e
    il conto di tutti i crediti, con una dashboard locale.
  - I template si scrivono man mano, solo per modelli già provati.
- **Primo benchmark, stesse moltiplicazioni di matrici:**

  | Macchina | GFLOPS | Rispetto al PC |
  |---|---|---|
  | PC (Ryzen 5 3550H) | 53 | 1× |
  | Kaggle solo CPU (Xeon 2,2 GHz, 4 core) | 139 | 2,6× |
  | Kaggle GPU T4, fp32 | 3.507 | 66× |
  | Kaggle GPU T4, fp16 | 21.470 | 405× |

  Il benchmark in sola CPU è costato zero quota, quello GPU 0,01 h: 35 s di sessione dopo 19 s di attesa.
- **Bug trovati collaudando:**
  - Due lanci in parallelo si attribuivano a vicenda la quota consumata. Ora ogni lancio conta solo la
    propria risorsa.
  - La porta della dashboard era già occupata da un'altra app sul PC, e la skill credeva fosse la sua.
    Ora si riconosce con `/api/ping` e, se serve, passa alla prima porta libera.
- **Lezione:** collegare ElevenLabs leggendo la chiave dal `.env` di un altro progetto è stato
  bloccato dal controllo dei permessi. È giusto: le chiavi le collega l'utente, la skill non va a
  cercarle.

## 26/11/2026 — collaudo di cache e Qwen-TTS

- **Cache:** 10 pacchetti pip e i due modelli Qwen, 8,52 GB salvati su Kaggle in 56 s di sessione in
  sola CPU. Zero quota.
- **Qwen-TTS con la cache:** stesse 6 battute della prova del mattino, nello stesso lancio.

  | | Mattino (senza cache) | Sera (con cache) |
  |---|---|---|
  | Installazione | 38,4 s | 24,9 s |
  | Caricamento modelli | 58,6 s | 46,6 s |
  | Preparazione totale | 97 s | 71 s (−26%) |
  | Calcolo | 145,5 s | 131,6 s |
  | Lancio intero | 320 s | 261 s (−18%) |

  Sessione 274 s, 0,08 h di GPU. 46,6 s di audio prodotti: circa 2,8 s di calcolo per secondo di audio.
- **Lezione:** la cache taglia la preparazione ma non la azzera. I 25 s che restano sono pip che
  installa 10 wheel dal disco (dentro c'è anche gradio, l'interfaccia web, che arriva con le dipendenze di qwen-tts); il caricamento resta
  lettura dei pesi. Con un lotto grande pesano sempre meno: è lì che si guadagna.

## Da fare

- Provare `coda` (aggiungi, lancia) e `run --script`, mai lanciati finora.
