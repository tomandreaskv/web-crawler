# Web Crawler — msorensen.no

Scraper produktdata (navn, beskrivelse, produktnummer, pris, lagerstatus) fra [msorensen.no/sigarer](https://www.msorensen.no/sigarer) og eksporterer til CSV, JSON eller Excel. Lagrer historikk i SQLite og varsler om prisendringer.

## Filstruktur

```
web-crawler/
├── config.py           # Nettsted-konfigurasjoner og selektorer
├── db.py               # SQLite-lagring og historikkhenting
├── reporter.py         # Prisovervåkning og endringsrapport
├── utils.py            # Felles: CLI-args, robots.txt, eksport, scheduler
├── dynamic_crawler.py  # Dynamisk scraper (Selenium) — anbefalt
├── crawler.py          # Statisk scraper (requests + BeautifulSoup)
└── requirements.txt
```

## Installasjon

```bash
pip install -r requirements.txt
```

> ChromeDriver lastes ned automatisk via `webdriver-manager`.

## Bruk

**Grunnleggende:**
```bash
python dynamic_crawler.py
```

**Med alle flagg:**
```bash
python dynamic_crawler.py \
  --site msorensen \
  --output produkter \
  --format csv,json,excel \
  --delay 2 \
  --pages 5 \
  --db produkter.db \
  --schedule 1h
```

## CLI-flagg

| Flagg | Standard | Beskrivelse |
|---|---|---|
| `--site` | `msorensen` | Nettsted å scrape (se `config.py`) |
| `--output` | `msorensen_products` | Filnavn uten endelse |
| `--format` | `csv` | Eksportformat: `csv`, `json`, `excel` (kombiner med komma) |
| `--delay` | `1.5` | Sekunder mellom sider |
| `--pages` | `0` (alle) | Maks antall sider |
| `--proxies` | — | Kommaseparert liste: `http://1.2.3.4:8080,http://5.6.7.8:8080` |
| `--db` | `products.db` | SQLite-databasefil |
| `--schedule` | — | Automatisk kjøring: `30m`, `2h`, `02:00` |

## Eksempler

```bash
# Eksporter til JSON og Excel i tillegg til CSV
python dynamic_crawler.py --format csv,json,excel

# Kjør automatisk hver natt kl. 03:00
python dynamic_crawler.py --schedule 03:00

# Kjør hvert 30. minutt med proxy-rotasjon
python dynamic_crawler.py --schedule 30m --proxies http://1.2.3.4:8080,http://5.6.7.8:8080

# Scrape bare de 3 første sidene
python dynamic_crawler.py --pages 3
```

## Legge til nytt nettsted

Åpne `config.py` og legg til en ny entry i `SITES`:

```python
SITES = {
    "msorensen": { ... },
    "nytt-nettsted": {
        "name": "Nettstedsnavn",
        "base_url": "https://eksempel.no/produkter?page=",
        "selectors": {
            "names":          ("css",   "h2.product-title"),
            "descriptions":   ("css",   "p.product-description"),
            "productnumbers": ("css",   "span.sku"),
            "product_links":  ("css",   "a.product-link"),
            "prices":         ("xpath", "//span[@class='price']"),
            "quantities":     ("css",   "span.stock"),
        },
    },
}
```

Kjør deretter:
```bash
python dynamic_crawler.py --site nytt-nettsted
```

## Prisovervåkning

Etter første kjøring vil hver påfølgende kjøring automatisk skrive ut en rapport:

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

All historikk lagres i SQLite (`products.db`) slik at du kan spore prisutvikling over tid.
