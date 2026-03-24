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
from db import save_products

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
# Statisk scraping (requests + BeautifulSoup)
# ---------------------------------------------------------------------------

def _scrape_static(url):
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")
    products = soup.findAll("div", class_="d4-row d4-listing-row")
    if not products:
        return None

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for product in products:
        try:
            name          = product.find("span", class_="product-desc1")
            description   = product.find("span", class_="product-desc2")
            productnumber = product.find("span", class_="product-desc-prod-num")
            link          = product.find("a",    class_="AdProductLink")

            quantity = ""
            sc = product.find("div", class_="DynamicStockTooltipContainer")
            if sc:
                spans = sc.findAll("span")
                if spans:
                    quantity = spans[0].text.strip("()").strip()

            price = ""
            pc = product.find("div", class_="d4-listing-cell d4-col-2 price-cell")
            if pc:
                lp = pc.find("div", class_="ListPriceContainer")
                if lp:
                    label = lp.find("div", class_="PriceLabelContainer")
                    if label:
                        span = label.find("span", id=re.compile(r"^adprice__1680"))
                        if span:
                            price = span.get("content", "")

            records.append({
                "Link":           link["href"] if link else "",
                "Product number": productnumber.text if productnumber else "",
                "Title":          name.text          if name          else "",
                "Description":    description.text   if description   else "",
                "Price":          price,
                "Quantity":       quantity,
                "Date":           now,
            })
        except Exception as e:
            logger.warning(f"Feil ved produkt: {e}")
    return records or None


# ---------------------------------------------------------------------------
# Dynamisk scraping (Selenium + Chromium)
# ---------------------------------------------------------------------------

def _scrape_dynamic(url, selectors):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By

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

        names = driver.find_elements(By.CSS_SELECTOR, selectors["names"][1])
        if not names:
            return None

        descriptions   = driver.find_elements(By.CSS_SELECTOR, selectors["descriptions"][1])
        productnumbers = driver.find_elements(By.CSS_SELECTOR, selectors["productnumbers"][1])
        product_links  = driver.find_elements(By.CSS_SELECTOR, selectors["product_links"][1])
        prices         = driver.find_elements(By.XPATH,        selectors["prices"][1])
        quantities     = driver.find_elements(By.CSS_SELECTOR, selectors["quantities"][1])

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for i in range(len(names)):
            try:
                records.append({
                    "Link":           product_links[i].get_attribute("href") if i < len(product_links) else "",
                    "Product number": productnumbers[i].text                 if i < len(productnumbers) else "",
                    "Title":          names[i].text,
                    "Description":    descriptions[i].text                   if i < len(descriptions)   else "",
                    "Price":          prices[i].text                         if i < len(prices)          else "",
                    "Quantity":       quantities[i].text.strip("()").strip() if i < len(quantities)      else "",
                    "Date":           now,
                })
            except Exception as e:
                logger.warning(f"Feil ved produkt {i}: {e}")
        return records or None
    finally:
        driver.quit()


# ---------------------------------------------------------------------------
# Oppgavebehandling med retry
# ---------------------------------------------------------------------------

def process_task(r, task, db_file):
    site      = task["site"]
    page      = task["page"]
    site_cfg  = SITES[site]
    url       = site_cfg["base_url"] + str(page)
    is_dyn    = site_cfg.get("dynamic", False)

    logger.info(f"[{WORKER_ID}] {site} side {page} — {'dynamisk' if is_dyn else 'statisk'}")

    for attempt in range(MAX_RETRIES):
        try:
            records = (
                _scrape_dynamic(url, site_cfg["selectors"])
                if is_dyn
                else _scrape_static(url)
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
