# Tavola per zaino — configuratore 3D

Sito statico per progettare e scaricare una tavola strutturale leggera da stampare in 3D, da mettere nella tasca laptop di uno zaino senza struttura.

- **Fori isogrid**: reticolo triangolare di fori arrotondati; le nervature dritte in tre direzioni danno rigidità a flessione e torsione.
- **Sezionamento automatico**: se la tavola non entra nel piatto, viene divisa nel minor numero di pezzi possibile. I tagli corrono lungo l'asse delle nervature, così ogni giunzione è una lunga superficie da incollare.
- **Incastri** a testa tonda o a coda di rondine, con gioco per la colla e rastremazione verticale che fa da battuta.
- **Export** in STL (binario e ASCII), 3MF e OBJ: tavola intera, sezionata con incastri, sezionata senza incastri, in un unico ZIP con parametri e istruzioni di stampa.

Il generatore è scritto in Python (`py/tavola_zaino.py`) e gira **nel browser** grazie a [Pyodide](https://pyodide.org): non serve nessun server, quindi il sito funziona su GitHub Pages.

## Pubblicare su GitHub Pages

1. Crea un repository (ad esempio `tavola-zaino`) e carica **tutto il contenuto di questa cartella nella radice** del repository, compreso il file `.nojekyll`.
2. Su GitHub apri **Settings → Pages**.
3. In *Build and deployment* scegli **Source: Deploy from a branch**, poi **Branch: `main`**, cartella **`/ (root)`**, e salva.
4. Dopo un minuto il sito è online su `https://<tuo-utente>.github.io/<nome-repository>/`.

Non c'è nessun passaggio di build: ogni push su `main` aggiorna il sito.

## Provarlo in locale

Il generatore gira in un Web Worker, che i browser non avviano da `file://`. Serve quindi un piccolo server:

```bash
cd tavola-zaino-web
python3 -m http.server 8000
```

Poi apri <http://localhost:8000>. Il primo avvio scarica Pyodide, numpy e shapely (circa 15 MB, poi restano in cache).

## Usarlo da riga di comando

Lo stesso file Python funziona anche fuori dal browser:

```bash
pip install numpy shapely mapbox_earcut
python3 py/tavola_zaino.py --dump-params parametri.json      # parametri di default
python3 py/tavola_zaino.py --params parametri.json \
        --variants whole,split_keys,split_plain \
        --formats stl,3mf --quality standard --out tavola.zip
```

Il file JSON di parametri è lo stesso che il sito salva con **Salva parametri** e riapre con **Apri parametri**.

## Struttura

```
index.html              pagina
assets/style.css        stile (tema chiaro e scuro automatico)
assets/app.js           controlli, stato, link condivisibili, esportazione
assets/viewer.js        anteprima 3D (three.js) e sfondo a curve di livello
assets/worker.js        avvia Pyodide e chiama il generatore Python
assets/vendor/          earcut.js (licenza ISC), usato per triangolare le facce
py/tavola_zaino.py      generatore parametrico (numpy + shapely)
.nojekyll               evita che GitHub Pages elabori il sito con Jekyll
```

Dipendenze caricate da CDN con versione fissata: Pyodide 0.29.3 e three.js 0.170.0 (jsDelivr), font Barlow Semi Condensed (Google Fonts).

## Aggiungere un parametro

1. Aggiungi il campo alla classe `Params` in `py/tavola_zaino.py` e usalo nel codice.
2. Aggiungi una voce al gruppo giusto in `GROUPS`, in cima a `assets/app.js` (etichetta, unità, minimo, massimo, passo).

Il valore di default arriva sempre da Python, quindi non va ripetuto nel JavaScript.

## Stampa e montaggio

- Layer superiori e inferiori: **0** (solo perimetri e infill).
- Perimetri: 2 (3 se serve più rigidità). Infill gyroid 5–10%.
- I pezzi nella cartella `piatto` sono già ruotati e centrati per il piatto.
- Incolla con epossidica sulle facce di taglio e nelle sedi, su un piano dritto, seguendo l'ordine di montaggio indicato nel file `LEGGIMI.txt`: ogni pezzo si cala dall'alto sui precedenti.
- Prima di stampare tutto, prova un incastro: il gioco si regola con *Gioco per lato*.
