"""
Auto-oppdagelse av CSS-selektorer for produktlistesider.

Henter HTML fra siden, sender et utdrag til Claude API og ber den identifisere
hvilke CSS-selektorer som peker på produkter, priser, beskrivelser osv.
Validerer selektorene mot ekte HTML og lagrer resultatet til databasen.

Bruk:
  python selector_discovery.py --url https://example.com/products --site example
  python selector_discovery.py --url https://example.com/products --site example --dynamic
  python selector_discovery.py --site msorensen --validate   # kun valider eksisterende
"""

import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import HEADERS, DATABASE_FILE

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DISCOVERY_MODEL   = "claude-opus-4-6"

# Dager før selektorer regnes som foreldede og bør re-valideres
SELECTOR_MAX_AGE_DAYS = int(os.getenv("SELECTOR_MAX_AGE_DAYS", "7"))


# ---------------------------------------------------------------------------
# HTML-henting
# ---------------------------------------------------------------------------

def _fetch_static(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def _fetch_dynamic(url):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    chrome_bin        = os.getenv("CHROME_BIN")
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")

    if chrome_bin:
        options.binary_location = chrome_bin

    if chromedriver_path:
        driver = webdriver.Chrome(service=Service(chromedriver_path), options=options)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        driver.get(url)
        time.sleep(3)
        return driver.page_source
    finally:
        driver.quit()


def fetch_html(url, is_dynamic=False):
    return _fetch_dynamic(url) if is_dynamic else _fetch_static(url)


# ---------------------------------------------------------------------------
# HTML-rensing — fjerner støy og kutter til passende størrelse for Claude
# ---------------------------------------------------------------------------

def clean_html(html, max_chars=7000):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "svg", "head", "noscript", "iframe", "link", "meta"]):
        tag.decompose()

    # Prøv å finne hoved-innholdscontainer
    main = (
        soup.find("main")
        or soup.find(id=re.compile(r"main|content|product", re.I))
        or soup.find(class_=re.compile(r"main|content|product.?list", re.I))
        or soup.body
        or soup
    )

    cleaned = re.sub(r"\s+", " ", str(main))
    return cleaned[:max_chars]


# ---------------------------------------------------------------------------
# Claude API — ber modellen analysere HTML og returnere selektorer
# ---------------------------------------------------------------------------

def discover_with_claude(html_sample, url):
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY er ikke satt. "
            "Eksporter nøkkelen din: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Du analyserer HTML fra en nettbutikk med produktlisting.
URL: {url}

Finn CSS-selektorer for disse feltene. Selektorene gjelder for HELE siden (ikke relative til container).

Felter å finne:
- container:      repetering beholder per produkt, f.eks. "div.product-item"
- names:          produktnavn/tittel
- descriptions:   produktbeskrivelse (null hvis ikke til stede)
- productnumbers: produktnummer eller SKU (null hvis ikke til stede)
- prices:         pris
- quantities:     lagerstatus eller antall (null hvis ikke til stede)
- product_links:  href-lenke til produktside (selekter a-elementet)

Regler:
- Bruk kun CSS-selektorer (ikke XPath)
- Skriv null for felt som ikke finnes på siden
- Svar med KUN gyldig JSON — ingen forklaring, ingen markdown

Eksempel på gyldig svar:
{{"container": "div.product-row", "names": "span.product-title", "descriptions": null, "productnumbers": "span.sku", "prices": "span.price", "quantities": "span.stock", "product_links": "a.product-link"}}

HTML (forkortet):
{html_sample}"""

    response = client.messages.create(
        model=DISCOVERY_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Fjern markdown-kodeblokker hvis Claude la dem til
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Validering — tester at selektorene faktisk finner elementer i HTML-en
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"container", "names", "prices", "product_links"}


def validate_selectors(selectors, html):
    """
    Kjører hvert selector mot HTML og teller treff.
    Returnerer (er_gyldig, antall_treff_per_felt, detaljer).
    """
    soup = BeautifulSoup(html, "html.parser")
    details = {}

    for field, selector in selectors.items():
        if not selector:
            details[field] = {"count": 0, "samples": [], "valid": False}
            continue
        try:
            elements = soup.select(selector)
            samples  = [e.get_text(strip=True)[:60] for e in elements[:3]]
            details[field] = {
                "count":   len(elements),
                "samples": samples,
                "valid":   len(elements) > 0,
            }
        except Exception as e:
            logger.debug(f"Selektor '{selector}' feilet: {e}")
            details[field] = {"count": 0, "samples": [], "valid": False}

        status = "OK  " if details[field]["valid"] else "FEIL"
        logger.debug(f"  [{status}] {field:<16} {selector!r} → {details[field]['count']} treff")

    is_valid  = all(details.get(f, {}).get("valid") for f in REQUIRED_FIELDS)
    hit_counts = {f: v["count"] for f, v in details.items()}
    return is_valid, hit_counts, details


# ---------------------------------------------------------------------------
# Hoved-inngang — oppdager, validerer og lagrer selektorer
# ---------------------------------------------------------------------------

def discover(url, is_dynamic=False, site=None, db_file=None):
    """
    Full oppdagelse: henter HTML → renser → Claude → validerer → lagrer.
    Returnerer selektorer-dict hvis vellykket, None ved feil.
    """
    logger.info(f"Starter selektor-oppdagelse for: {url}")

    try:
        html = fetch_html(url, is_dynamic)
        logger.debug(f"HTML hentet ({len(html):,} tegn)")
    except Exception as e:
        logger.error(f"Kunne ikke hente siden: {e}")
        return None

    html_sample = clean_html(html)
    logger.debug(f"HTML renset til {len(html_sample):,} tegn")

    try:
        selectors = discover_with_claude(html_sample, url)
        logger.info(f"Claude returnerte selektorer for: {list(k for k, v in selectors.items() if v)}")
    except EnvironmentError as e:
        logger.error(str(e))
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Claude returnerte ugyldig JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Claude API feilet: {e}")
        return None

    is_valid, hit_counts, details = validate_selectors(selectors, html)

    if is_valid:
        logger.success("Alle kjernefelter validert — selektorer er klare til bruk")
    else:
        failing = [f for f in REQUIRED_FIELDS if not details.get(f, {}).get("valid")]
        logger.warning(f"Validering feilet for kjernefelt: {failing}")

    if site and db_file:
        from db import save_selectors
        save_selectors(site, url, is_dynamic, selectors, hit_counts, is_valid, db_file)
        logger.info(f"Selektorer lagret i databasen for '{site}'")

    return selectors if is_valid else None


def validate_existing(site, db_file):
    """
    Henter lagrede selektorer fra DB og tester dem mot en live side.
    Returnerer True hvis fortsatt gyldige, False hvis siden har endret seg.
    """
    from db import get_selectors
    from config import SITES

    config = get_selectors(site, db_file)
    if not config:
        logger.warning(f"Ingen lagrede selektorer for '{site}'")
        return False

    site_cfg = SITES.get(site, {})
    url      = site_cfg.get("base_url", config["base_url"]) + "0"

    logger.info(f"Validerer eksisterende selektorer for '{site}' mot: {url}")

    try:
        html = fetch_html(url, config["dynamic"])
    except Exception as e:
        logger.error(f"Kunne ikke hente siden for validering: {e}")
        return False

    selectors = config["selectors"]
    is_valid, hit_counts, _ = validate_selectors(selectors, html)

    from db import save_selectors
    save_selectors(
        site, config["base_url"], config["dynamic"],
        selectors, hit_counts, is_valid, db_file,
    )

    if is_valid:
        logger.success(f"'{site}': selektorer er fortsatt gyldige")
    else:
        logger.warning(f"'{site}': selektorer er utdaterte — nettsiden har sannsynligvis endret seg")

    return is_valid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="DEBUG")

    parser = argparse.ArgumentParser(description="Auto-oppdagelse av CSS-selektorer")
    parser.add_argument("--url",      help="URL til produktlisteside (side 0)")
    parser.add_argument("--site",     default="ukjent", help="Nettsted-ID (lagres i DB)")
    parser.add_argument("--dynamic",  action="store_true", help="Bruk Selenium for JavaScript-sider")
    parser.add_argument("--validate", action="store_true", help="Kun valider eksisterende selektorer")
    parser.add_argument("--db",       default=DATABASE_FILE)
    args = parser.parse_args()

    if args.validate:
        ok = validate_existing(args.site, args.db)
        sys.exit(0 if ok else 1)

    if not args.url:
        parser.error("--url er påkrevd (med mindre du bruker --validate)")

    result = discover(args.url, args.dynamic, args.site, args.db)
    if result:
        print("\nOppdagede selektorer:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\nOppdagelse mislyktes — se logger over for detaljer")
        sys.exit(1)
