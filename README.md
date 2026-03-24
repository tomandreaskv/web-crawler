# Web Crawler — msorensen.no

Scraper produktdata (navn, beskrivelse, produktnummer, pris, lagerstatus) fra [msorensen.no/sigarer](https://www.msorensen.no/sigarer) og eksporterer til CSV.

## Filer

| Fil | Beskrivelse |
|---|---|
| `crawler.py` | Statisk scraper med `requests` + `BeautifulSoup`. Rask, men fungerer ikke for JS-rendret innhold. |
| `dynamic_crawler.py` | Dynamisk scraper med `Selenium`. Anbefalt — håndterer JavaScript-rendret innhold. |

## Installasjon

```bash
pip install -r requirements.txt
```

> ChromeDriver lastes ned automatisk via `webdriver-manager`. Ingen manuell installasjon nødvendig.

## Bruk

**Dynamisk scraper (anbefalt):**
```bash
python dynamic_crawler.py
```

**Statisk scraper:**
```bash
python crawler.py
```

Output lagres til `msorensen_products.csv` med kolonnene:
`Link`, `Product number`, `Title`, `Description`, `Price`, `Quantity`, `Date`

## Konfigurasjon

Øverst i hver fil finnes konfigurasjonsvariablene:

```python
BASE_URL = "https://www.msorensen.no/sigarer?pageID="
OUTPUT_FILE = "msorensen_products.csv"
REQUEST_DELAY = 1.5  # sekunder mellom sider
```
