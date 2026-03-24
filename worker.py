"""
Distribuert crawler-worker.

Henter scrape-oppgaver fra Redis-kø, skraper siden, lagrer resultater
og legger neste side i køen automatisk (dynamisk paginering).
Støtter work-stealing: tar fra alle tilgjengelige nettsted-køer.

Start:
  python worker.py [--sites msorensen] [--db products.db]

Med Docker (3 parallelle workers):
  docker compose up --scale worker=3
"""

import json
import os
import re
import socket
import sys
import time
import uuid
from datetime import datetime

import redis as redis_lib
from loguru import logger

from config import SITES, MAX_RETRIES, HEADERS, DATABASE_FILE, REDIS_URL
from db import save_products, get_selectors

WORKER_ID          = os.getenv("WORKER_ID", f"{socket.gethostname()[:8]}-{str(uuid.uuid4())[:6]}")
HEARTBEAT_INTERVAL = 10   # sekunder mellom heartbeats
REQUEST_DELAY      = float(os.getenv("REQUEST_DELAY", "1.5"))


# ---------------------------------------------------------------------------
# Redis-tilkobling
# ---------------------------------------------------------------------------

def get_redis():
    r = redis_lib.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    return r


# ---------------------------------------------------------------------------
# Heartbeat — koordinatoren vet at workeren lever
# ---------------------------------------------------------------------------

def send_heartbeat(r, status="idle", current_task=None):
    r.hset(f"worker:{WORKER_ID}", mapping={
        "status":       status,
        "current_task": json.dumps(current_task) if current_task else "",
        "last_seen":    time.time(),
        "host":         socket.gethostname(),
    })
    r.expire(f"worker:{WORKER_ID}", 120)  # auto-slett etter 2 min uten heartbeat


# ---------------------------------------------------------------------------
# Kø-operasjoner
# ---------------------------------------------------------------------------

def enqueue_page(r, site, page):
    """Legg til side atomisk — returnerer True hvis siden er ny."""
    added = r.sadd(f"seen:{site}", page)
    if added:
        r.rpush(f"queue:{site}", json.dumps({"site": site, "page": page}))
    return bool(added)


def pop_task(r, queues):
    """Blokkerende pop fra første tilgjengelige kø (work stealing innebygd)."""
    result = r.blpop(queues, timeout=5)
    if result:
        _, raw = result
        return json.loads(raw)
    return None


# ---------------------------------------------------------------------------
# Selektor-oppslag — DB-selektorer prioriteres over config.py
# ---------------------------------------------------------------------------

def _load_selectors(site, db_file):
    """
    Henter selektorer for et nettsted.
    Prioritetsrekkefølge:
      1. Validerte selektorer fra databasen (auto-oppdaget)
      2. Selektorer fra config.py (manuelt konfigurert)
    Returnerer dict med string-selektorer, eller None.
    """
    record = get_selectors(site, db_file)
    if record and record["valid"]:
        logger.debug(f"[{site}] Bruker selektorer fra DB (oppdaget {record['discovered_at']})")
        return record["selectors"]

    site_cfg = SITES.get(site, {})
    cfg_sels = site_cfg.get("selectors")
    if cfg_sels:
        logger.debug(f"[{site}] Bruker selektorer fra config.py")
        # Konverter tuple-format ("css", "...") til string-format
        return {k: v[1] for k, v in cfg_sels.items()}

    logger.error(f"[{site}] Ingen selektorer tilgjengelig — hopper over siden")
    return None


# ---------------------------------------------------------------------------
# Generisk scraper — bruker CSS-selektorer fra DB eller config
#
# Strategi:
#   1. Finn alle produkt-containere med "container"-selektoren
#   2. Hent hvert felt (navn, pris osv.) relativt til containeren
#   Denne tilnærmingen er robust mot manglende felt (returnerer tom streng)
# ---------------------------------------------------------------------------

def _extract_with_soup(soup, selectors):
    """Statisk ekstraksjon med BeautifulSoup og CSS-selektorer."""
    containers = soup.select(selectors.get("container", ""))
    if not containers:
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for c in containers:
        def txt(field):
            sel = selectors.get(field)
            if not sel:
                return ""
            el = c.select_one(sel)
            return el.get_text(strip=True) if el else ""

        def attr(field, attribute):
            sel = selectors.get(field)
            if not sel:
                return ""
            el = c.select_one(sel)
            return el.get(attribute, "") if el else ""

        records.append({
            "Link":           attr("product_links", "href"),
            "Product number": txt("productnumbers"),
            "Title":          txt("names"),
            "Description":    txt("descriptions"),
            "Price":          txt("prices"),
            "Quantity":       txt("quantities").strip("()"),
            "Date":           now,
        })
    return records or None


def _extract_with_selenium(driver, selectors):
    """Dynamisk ekstraksjon med Selenium og CSS-selektorer."""
    from selenium.webdriver.common.by import By

    containers = driver.find_elements(By.CSS_SELECTOR, selectors.get("container", ""))
    if not containers:
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for c in containers:
        def txt(field):
            sel = selectors.get(field)
            if not sel:
                return ""
            try:
                el = c.find_element(By.CSS_SELECTOR, sel)
                return el.text.strip()
            except Exception:
                return ""

        def attr(field, attribute):
            sel = selectors.get(field)
            if not sel:
                return ""
            try:
                el = c.find_element(By.CSS_SELECTOR, sel)
                return el.get_attribute(attribute) or ""
            except Exception:
                return ""

        records.append({
            "Link":           attr("product_links", "href"),
            "Product number": txt("productnumbers"),
            "Title":          txt("names"),
            "Description":    txt("descriptions"),
            "Price":          txt("prices"),
            "Quantity":       txt("quantities").strip("()"),
            "Date":           now,
        })
    return records or None


def _scrape_static(url, selectors):
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    return _extract_with_soup(soup, selectors)


def _scrape_dynamic(url, selectors):
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
        time.sleep(REQUEST_DELAY)
        return _extract_with_selenium(driver, selectors)
    finally:
        driver.quit()


# ---------------------------------------------------------------------------
# Oppgavebehandling med retry
# ---------------------------------------------------------------------------

def process_task(r, task, db_file):
    site      = task["site"]
    page      = task["page"]
    site_cfg  = SITES.get(site, {})
    url       = site_cfg.get("base_url", "") + str(page)
    is_dyn    = site_cfg.get("dynamic", False)

    selectors = _load_selectors(site, db_file)
    if not selectors:
        logger.error(f"[{WORKER_ID}] Ingen selektorer for '{site}' — hopper over side {page}")
        return

    logger.info(f"[{WORKER_ID}] {site} side {page} — {'dynamisk' if is_dyn else 'statisk'}")

    for attempt in range(MAX_RETRIES):
        try:
            records = (
                _scrape_dynamic(url, selectors)
                if is_dyn
                else _scrape_static(url, selectors)
            )

            if records:
                save_products(records, site, db_file)
                # Oppdater statistikk atomisk i Redis
                r.hincrby(f"stats:{site}", "products", len(records))
                r.hincrby(f"stats:{site}", "pages",    1)
                # Legg neste side i kø (kun om den ikke er sett før)
                if enqueue_page(r, site, page + 1):
                    logger.debug(f"[{WORKER_ID}] Side {page + 1} lagt i kø for {site}")
                logger.success(f"[{WORKER_ID}] Side {page}: {len(records)} produkter")
            else:
                # Tom side = pagination er ferdig for denne grenen
                logger.info(f"[{WORKER_ID}] Ingen produkter på side {page} — slutt på paginering")
                r.sadd(f"exhausted:{site}", page)
            return

        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"Forsøk {attempt + 1}/{MAX_RETRIES} feilet: {e}. Venter {wait}s...")
            time.sleep(wait)

    # Alle forsøk feilet — legg oppgaven tilbake for en annen worker
    logger.error(f"[{WORKER_ID}] Alle forsøk feilet for {url} — gjeninnkøyer")
    r.lpush(f"queue:{site}", json.dumps(task))


# ---------------------------------------------------------------------------
# Hoved-løkke
# ---------------------------------------------------------------------------

def run_worker(sites=None, db_file=DATABASE_FILE):
    r = get_redis()
    active_sites = sites or list(SITES.keys())
    queues       = [f"queue:{s}" for s in active_sites]

    logger.info(f"Worker {WORKER_ID} klar | Nettsteder: {active_sites}")
    logger.info("Work-stealing aktivert — henter fra alle tilgjengelige køer")
    send_heartbeat(r, "idle")

    last_hb = time.time()

    while True:
        # Periodisk heartbeat uavhengig av arbeid
        if time.time() - last_hb >= HEARTBEAT_INTERVAL:
            send_heartbeat(r, "idle")
            last_hb = time.time()

        task = pop_task(r, queues)

        if task:
            send_heartbeat(r, "working", task)
            last_hb = time.time()
            process_task(r, task, db_file)
            send_heartbeat(r, "idle")
        else:
            # Sjekk om alt arbeid er ferdig
            queues_empty  = all(r.llen(q) == 0 for q in queues)
            any_exhausted = any(r.scard(f"exhausted:{s}") > 0 for s in active_sites)
            if queues_empty and any_exhausted:
                logger.success(f"[{WORKER_ID}] Ingen flere oppgaver — avslutter.")
                break


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

    import argparse
    parser = argparse.ArgumentParser(description="Distribuert crawler-worker")
    parser.add_argument("--sites", default="",
                        help="Kommaseparert liste med nettsteder (default: alle)")
    parser.add_argument("--db", default=DATABASE_FILE)
    args = parser.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()] or None
    run_worker(sites, args.db)


if __name__ == "__main__":
    main()
