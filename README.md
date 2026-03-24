# Web Crawler

Scraper produktdata (navn, beskrivelse, produktnummer, pris, lagerstatus) fra nettbutikker og lagrer historikk i SQLite. Støtter varsling om prisendringer, REST API, visuelt dashboard og distribuert parallellkjøring i Docker.

Selektorer oppdages automatisk fra HTML-strukturen — ingen manuell konfigurering nødvendig for nye nettsteder.

---

## Filstruktur

```
web-crawler/
├── config.py               # Nettsted-konfigurasjoner
├── db.py                   # SQLite-lagring og historikkhenting
├── selector_discovery.py   # Auto-oppdagelse av CSS-selektorer
├── worker.py               # Distribuert crawler-worker (henter fra Redis-kø)
├── coordinator.py          # Kø-koordinator, heartbeat og work-stealing
├── crawler.py              # Enkel statisk scraper (requests + BeautifulSoup)
├── dynamic_crawler.py      # Dynamisk scraper (Selenium) for JavaScript-sider
├── api.py                  # REST API (FastAPI) — filtrering og CSV-eksport
├── dashboard.py            # Streamlit-dashboard med grafer og prishistorikk
├── reporter.py             # Prisovervåkning og endringsrapport
├── notifier.py             # Varsling via e-post og Slack
├── alerts.py               # Prisvarsel-logikk
├── utils.py                # CLI-args, robots.txt, eksport, scheduler
├── docker-compose.yml      # Redis + coordinator + skalerbare workers + API + dashboard
└── requirements.txt
```

---

## Kom i gang

```bash
pip install -r requirements.txt
```

> ChromeDriver lastes ned automatisk via `webdriver-manager`.

---

## Legge til et nytt nettsted

Åpne `config.py` og legg til `base_url` og om siden krever JavaScript:

```python
SITES = {
    "msorensen": { ... },

    "nytt-nettsted": {
        "name": "Nettstedsnavn",
        "base_url": "https://eksempel.no/produkter?page=",
        "dynamic": False,   # True hvis siden bruker JavaScript for å laste innhold
    },
}
```

Selektorer (hvilke HTML-elementer som inneholder navn, pris osv.) oppdages automatisk når du kjører crawleren første gang. De lagres i databasen og gjenbrukes ved fremtidige kjøringer. Hvis nettsiden endrer struktur, re-valideres og oppdages selektorer automatisk.

Manuell oppdagelse og test:
```bash
python selector_discovery.py --url https://eksempel.no/produkter?page=0 --site nytt-nettsted
python selector_discovery.py --site nytt-nettsted --validate
```

---

## Enkel kjøring (én prosess)

```bash
# Kjør med standardverdier
python dynamic_crawler.py

# Alle flagg
python dynamic_crawler.py \
  --site msorensen \
  --output produkter \
  --format csv,json,excel \
  --delay 2 \
  --pages 5 \
  --db produkter.db \
  --schedule 1h
```

| Flagg | Standard | Beskrivelse |
|---|---|---|
| `--site` | `msorensen` | Nettsted å scrape (fra `config.py`) |
| `--output` | `msorensen_products` | Filnavn uten endelse |
| `--format` | `csv` | Eksportformat: `csv`, `json`, `excel` |
| `--delay` | `1.5` | Sekunder mellom sider |
| `--pages` | `0` (alle) | Maks antall sider |
| `--proxies` | — | Kommaseparert: `http://1.2.3.4:8080,...` |
| `--db` | `products.db` | SQLite-databasefil |
| `--schedule` | — | Automatisk: `30m`, `2h`, `03:00` |

---

## Distribuert kjøring (Docker)

Kjør flere workers parallelt — hver worker henter oppgaver fra en delt Redis-kø. Når én worker er ferdig, hjelper den automatisk de andre (*work-stealing*).

```bash
mkdir -p data
docker compose up --scale worker=4
```

```
Koordinator          Redis                Workers (×4)
──────────           ─────                ────────────
Starter kø  ──────→  [side 0]  ←──────   Worker henter side 0
                     [side 1]  ←──────   Worker henter side 1
                         ↑               Finner produkter → lagrer
                     [side 2]            Legger side 2 i kø
                         ...
```

Tjenester som startes:

| Tjeneste | URL | Beskrivelse |
|---|---|---|
| `coordinator` | — | Seeder køer, overvåker workers, work-stealing |
| `worker` (×N) | — | Scraper sider, lagrer til SQLite |
| `api` | `localhost:8000/docs` | REST API med filtrering og eksport |
| `dashboard` | `localhost:8501` | Visuelt dashboard med grafer |
| `redis` | — | Kommunikasjonskanal mellom containere |

Koordinator og workers alene (uten Docker):
```bash
# Terminal 1
docker run -p 6379:6379 redis:7-alpine

# Terminal 2 — koordinator
python coordinator.py --sites msorensen

# Terminal 3, 4, 5 — workers
python worker.py
python worker.py
python worker.py
```

---

## REST API

```bash
# Hent siste produkter
GET /products/msorensen

# Filtrer
GET /products/msorensen?title=cohiba&max_price=500&in_stock=true

# Eksporter til CSV
GET /products/msorensen/export

# Prishistorikk for ett produkt
GET /products/msorensen/history/SKU-123

# Kjørehistorikk
GET /runs
```

---

## Dashboard

`http://localhost:8501` — viser prisutvikling over tid, lagerendringer og søk/filtrering i siste scrape-resultat.

---

## Prisovervåkning

Etter første kjøring skriver rapporten automatisk ut endringer:

```
====================================================
  ENDRINGSRAPPORT
====================================================
Nye produkter (2):
  +  Cohiba Siglo VI  [COH-006]
Prisendringer (1):
  ~  Montecristo No. 4  [MON-004]:  299 kr  →  279 kr
====================================================
```

Varsling via e-post og Slack konfigureres med miljøvariabler:

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_USER=din@epost.no
export SMTP_PASSWORD=passord
export SMTP_FROM=din@epost.no
export NOTIFY_EMAIL=mottaker@epost.no

export SLACK_WEBHOOK=https://hooks.slack.com/...
```

---

## Miljøvariabler

| Variabel | Standard | Beskrivelse |
|---|---|---|
| `DATABASE_FILE` | `products.db` | Sti til SQLite-database |
| `REDIS_URL` | `redis://localhost:6379` | Redis-tilkobling |
| `STEAL_THRESHOLD` | `60` | Sekunder før en worker regnes som hengt |
| `SELECTOR_MAX_AGE_DAYS` | `7` | Dager før selektorer re-valideres |
| `REQUEST_DELAY` | `1.5` | Sekunder mellom sider (workers) |
| `CHROME_BIN` | — | Sti til Chromium (settes automatisk i Docker) |
