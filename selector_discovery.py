"""
Auto-oppdagelse av CSS-selektorer for produktlistesider.

Bruker møstergjenkjenning på HTML for å finne produktcontainere og feltselektorer.
Logikk:
  1. Tell repetering block-elementer (div/li/article med klasser)
  2. Velg containeren med høyest "produktsignal" (pris + lenke + rimelig tekst)
  3. Finn felt (navn, pris, lenke osv.) inni containeren via klasse-mønstre

Bruk:
  python selector_discovery.py --url https://example.com/products --site example
  python selector_discovery.py --url https://example.com/products --site example --dynamic
  python selector_discovery.py --site msorensen --validate
"""

import json
import os
import re
import sys
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import HEADERS, DATABASE_FILE

SELECTOR_MAX_AGE_DAYS = int(os.getenv("SELECTOR_MAX_AGE_DAYS", "7"))

# Mønstre for feltgjenkjenning
_PRICE_RE   = re.compile(r"\d+[,.]?\d*\s*(kr|NOK|\$|€|,-)", re.I)
_NAME_CLS   = re.compile(r"(product.?(name|title|desc1)|item.?name|heading|prod.?name)", re.I)
_DESC_CLS   = re.compile(r"(desc(?:ription)?2?|summary|product.?body|prod.?desc(?!\d))", re.I)
_SKU_CLS    = re.compile(r"(sku|product.?num|item.?num|prod.?num|artnr|varenr)", re.I)
_STOCK_CLS  = re.compile(r"(stock|quantity|qty|availability|lager|beholdning)", re.I)
_PROD_CLS   = re.compile(r"(product|item|card|listing|row|article)", re.I)


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
# Heuristisk oppdagelse
# ---------------------------------------------------------------------------

def _css_sel(el):
    """Lag en enkel CSS-selektor fra et BeautifulSoup-element."""
    if el is None:
        return None
    classes = el.get("class", [])
    if classes:
        return f"{el.name}.{'.'.join(classes)}"
    if el.get("id"):
        return f"#{el['id']}"
    return el.name


def _find_container(soup):
    """
    Finn den mest sannsynlige produkt-containeren.
    Teller alle (tag, klasser)-kombinasjoner som repeterer 3+ ganger og
    scorer hver kandidat på pris, lenke og klasse-navn.
    """
    counts = Counter()
    for el in soup.find_all(["div", "li", "article", "tr"]):
        classes = tuple(sorted(el.get("class", [])))
        if classes:
            counts[(el.name, classes)] += 1

    candidates = [(tag, cls, n) for (tag, cls), n in counts.items() if n >= 3]

    best_sel, best_score = None, 0

    for tag, classes, count in candidates:
        sel    = f"{tag}.{'.'.join(classes)}"
        sample = soup.select_one(sel)
        if not sample:
            continue

        score = 0
        text  = sample.get_text(" ", strip=True)

        if _PRICE_RE.search(text):
            score += 4
        if sample.find("a"):
            score += 2
        if 15 < len(text) < 800:
            score += 1
        if _PROD_CLS.search(" ".join(classes).lower()):
            score += 2
        if count >= 8:
            score += 1

        if score > best_score:
            best_score = score
            best_sel   = sel

    if best_sel and best_score >= 4:
        logger.debug(f"Container funnet: {best_sel!r} (score={best_score})")
        return best_sel

    logger.warning("Ingen produktcontainer funnet med tilstrekkelig score")
    return None


def _find_in(container, pattern_re, tag_hints=None):
    """
    Finn første element der klasse-navn matcher pattern_re.
    Sjekker tag_hints først (f.eks. h2, h3 for produktnavn).
    """
    if tag_hints:
        for tag in tag_hints:
            el = container.find(tag)
            if el:
                return _css_sel(el)

    for el in container.find_all(True):
        cls_str = " ".join(el.get("class", []))
        if pattern_re.search(cls_str):
            sel = _css_sel(el)
            if sel:
                return sel
    return None


def _find_price(container):
    """Finn pris-element via tekst-mønster (tall + valuta)."""
    for el in container.find_all(True):
        if _PRICE_RE.search(el.get_text()):
            # Foretrekk løvnoder (mest spesifikke)
            children = list(el.children)
            has_sub  = any(
                hasattr(c, "find_all") and c.get("class")
                for c in children
            )
            if not has_sub:
                sel = _css_sel(el)
                if sel:
                    return sel
    return None


def _find_link(container):
    """Finn produkt-lenken i containeren."""
    for el in container.find_all("a"):
        href = el.get("href", "")
        if href and not href.startswith("#") and len(href) > 3:
            return _css_sel(el) or "a"
    return None


def discover_heuristic(html):
    """
    Heuristisk oppdagelse av selektorer fra rå HTML.
    Returnerer selektorer-dict eller None.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()

    container_sel = _find_container(soup)
    if not container_sel:
        return None

    sample = soup.select_one(container_sel)

    selectors = {
        "container":      container_sel,
        "names":          _find_in(sample, _NAME_CLS, tag_hints=["h2", "h3", "h4"]),
        "descriptions":   _find_in(sample, _DESC_CLS),
        "productnumbers": _find_in(sample, _SKU_CLS),
        "prices":         _find_price(sample),
        "quantities":     _find_in(sample, _STOCK_CLS),
        "product_links":  _find_link(sample),
    }

    found   = [k for k, v in selectors.items() if v]
    missing = [k for k, v in selectors.items() if not v]
    logger.info(f"Fant selektorer for: {found}")
    if missing:
        logger.debug(f"Ikke funnet: {missing}")

    return selectors


# ---------------------------------------------------------------------------
# Validering
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"container", "names", "prices", "product_links"}


def validate_selectors(selectors, html):
    """
    Tester selektorene mot HTML og teller treff.
    Returnerer (er_gyldig, treff_per_felt, detaljer).
    """
    soup    = BeautifulSoup(html, "html.parser")
    details = {}

    for field, selector in selectors.items():
        if not selector:
            details[field] = {"count": 0, "samples": [], "valid": False}
            continue
        try:
            elements = soup.select(selector)
            samples  = [e.get_text(strip=True)[:60] for e in elements[:3]]
            details[field] = {"count": len(elements), "samples": samples, "valid": len(elements) > 0}
        except Exception as e:
            logger.debug(f"Selektor '{selector}' feilet: {e}")
            details[field] = {"count": 0, "samples": [], "valid": False}

        status = "OK  " if details[field]["valid"] else "FEIL"
        logger.debug(f"  [{status}] {field:<16} {selector!r} → {details[field]['count']} treff")

    is_valid   = all(details.get(f, {}).get("valid") for f in REQUIRED_FIELDS)
    hit_counts = {f: v["count"] for f, v in details.items()}
    return is_valid, hit_counts, details


# ---------------------------------------------------------------------------
# Hoved-inngang
# ---------------------------------------------------------------------------

def discover(url, is_dynamic=False, site=None, db_file=None):
    """
    Full oppdagelse: henter HTML → heuristisk analyse → validerer → lagrer i DB.
    Returnerer selektorer-dict hvis vellykket, None ved feil.
    """
    logger.info(f"Starter selektor-oppdagelse for: {url}")

    try:
        html = fetch_html(url, is_dynamic)
        logger.debug(f"HTML hentet ({len(html):,} tegn)")
    except Exception as e:
        logger.error(f"Kunne ikke hente siden: {e}")
        return None

    selectors = discover_heuristic(html)

    if not selectors:
        logger.error("Heuristikken fant ingen produktcontainer")
        return None

    is_valid, hit_counts, details = validate_selectors(selectors, html)

    if is_valid:
        logger.success("Alle kjernefelter validert")
    else:
        failing = [f for f in REQUIRED_FIELDS if not details.get(f, {}).get("valid")]
        logger.warning(f"Validering feilet for: {failing}")

    if site and db_file:
        from db import save_selectors
        save_selectors(site, url, is_dynamic, selectors, hit_counts, is_valid, db_file)
        logger.info(f"Selektorer lagret i databasen for '{site}'")

    return selectors if is_valid else None


def validate_existing(site, db_file):
    """
    Henter lagrede selektorer og tester dem mot en live side.
    Returnerer True hvis fortsatt gyldige.
    """
    from db import get_selectors, save_selectors
    from config import SITES

    config = get_selectors(site, db_file)
    if not config:
        logger.warning(f"Ingen lagrede selektorer for '{site}'")
        return False

    site_cfg = SITES.get(site, {})
    url      = site_cfg.get("base_url", config["base_url"]) + "0"

    logger.info(f"Validerer selektorer for '{site}' mot: {url}")

    try:
        html = fetch_html(url, config["dynamic"])
    except Exception as e:
        logger.error(f"Kunne ikke hente siden: {e}")
        return False

    is_valid, hit_counts, _ = validate_selectors(config["selectors"], html)

    save_selectors(
        site, config["base_url"], config["dynamic"],
        config["selectors"], hit_counts, is_valid, db_file,
    )

    if is_valid:
        logger.success(f"'{site}': selektorer er fortsatt gyldige")
    else:
        logger.warning(f"'{site}': selektorer er utdaterte — nettsiden har endret seg")

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
    parser.add_argument("--validate", action="store_true", help="Valider eksisterende selektorer")
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
        print("\nOppdagelse mislyktes — se logger over")
        sys.exit(1)
