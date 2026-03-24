import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from loguru import logger

from alerts import check_alerts
from config import SITES, MAX_RETRIES, HEADERS, NOTIFICATIONS
from db import save_products, get_last_two_scrapes, start_run, finish_run
from notifier import notify_price_changes
from reporter import generate_diff, print_report
from utils import (
    build_arg_parser,
    check_robots_txt,
    export_data,
    apply_filters,
    run_on_schedule,
    setup_file_logging,
)


def current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _scrape_page(url, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    response = requests.get(url, headers=HEADERS, timeout=10, proxies=proxies)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "html.parser")
    products = soup.findAll("div", class_="d4-row d4-listing-row")
    if not products:
        return None

    records = []
    for product in products:
        try:
            name          = product.find("span", class_="product-desc1")
            description   = product.find("span", class_="product-desc2")
            productnumber = product.find("span", class_="product-desc-prod-num")
            link          = product.find("a",    class_="AdProductLink")

            quantity = ""
            stockcontainer = product.find("div", class_="DynamicStockTooltipContainer")
            if stockcontainer:
                spans = stockcontainer.findAll("span")
                if spans:
                    quantity = spans[0].text.strip("()").strip()

            price = ""
            pricecontainer = product.find("div", class_="d4-listing-cell d4-col-2 price-cell")
            if pricecontainer:
                listprice = pricecontainer.find("div", class_="ListPriceContainer")
                if listprice:
                    label = listprice.find("div", class_="PriceLabelContainer")
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
                "Date":           current_time(),
            })
        except Exception as e:
            logger.warning(f"Feil ved produkt: {e}")
    return records


def _scrape_with_retry(url, proxy=None):
    for attempt in range(MAX_RETRIES):
        try:
            return _scrape_page(url, proxy)
        except requests.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Forsøk {attempt + 1}/{MAX_RETRIES} feilet: {e}. Venter {wait}s...")
            time.sleep(wait)
    logger.error(f"Alle {MAX_RETRIES} forsøk feilet for {url}")
    return None


def run_crawl(args):
    site_config = SITES[args.site]
    base_url    = site_config["base_url"]
    proxies     = [p.strip() for p in args.proxies.split(",")] if args.proxies else [None]
    formats     = args.format.split(",")
    proxy_index = 0

    if not check_robots_txt(base_url):
        logger.error("Scraping ikke tillatt av robots.txt. Avbryter.")
        return

    run_id = start_run(args.site, args.db)
    all_records = []
    pages_scraped = 0
    error_msg = None

    try:
        page = 0
        while True:
            if args.pages and page >= args.pages:
                break

            proxy = None
            if proxies[0] is not None:
                proxy = proxies[proxy_index % len(proxies)]
                proxy_index += 1

            url = base_url + str(page)
            logger.info(f"Side {page}: {url}" + (f" (proxy: {proxy})" if proxy else ""))

            records = _scrape_with_retry(url, proxy)
            if records is None:
                logger.info(f"Ingen produkter på side {page} — ferdig.")
                break

            all_records.extend(records)
            pages_scraped = page + 1
            logger.success(f"Side {page}: {len(records)} produkter")
            page += 1
            time.sleep(args.delay)

    except KeyboardInterrupt:
        logger.warning("Avbrutt av bruker.")
        error_msg = "interrupted"
    except Exception as e:
        logger.error(f"Uventet feil: {e}")
        error_msg = str(e)

    status = "error" if error_msg and error_msg != "interrupted" else (
        "interrupted" if error_msg == "interrupted" else "ok"
    )
    finish_run(run_id, status, pages_scraped, len(all_records), error_msg, args.db)

    if not all_records:
        logger.warning("Ingen data å lagre.")
        return

    all_records = apply_filters(all_records, args)

    save_products(all_records, args.site, args.db)
    export_data(all_records, args.output, formats)

    latest, previous = get_last_two_scrapes(args.site, args.db)
    if previous:
        report = generate_diff(previous, latest)
        print_report(report)
        if args.notify:
            notify_price_changes(report, NOTIFICATIONS)
    else:
        logger.info("Første kjøring — ingen tidligere data å sammenligne med.")

    check_alerts(all_records, args.site, args.db, NOTIFICATIONS)


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    setup_file_logging()

    args = build_arg_parser().parse_args()
    logger.info(f"Starter statisk crawler for '{args.site}'...")

    if args.schedule:
        run_on_schedule(args.schedule, run_crawl, args)
    else:
        run_crawl(args)


if __name__ == "__main__":
    main()
