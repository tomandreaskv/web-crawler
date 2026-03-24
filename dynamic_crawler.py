import sys
import time
from datetime import datetime

from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from alerts import check_alerts
from config import SITES, MAX_RETRIES, NOTIFICATIONS
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


def setup_driver(proxy=None):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )


def _scrape_page(driver, url, selectors, delay):
    driver.get(url)
    time.sleep(delay)

    names = driver.find_elements(By.CSS_SELECTOR, selectors["names"][1])
    if not names:
        return None

    descriptions   = driver.find_elements(By.CSS_SELECTOR, selectors["descriptions"][1])
    productnumbers = driver.find_elements(By.CSS_SELECTOR, selectors["productnumbers"][1])
    product_links  = driver.find_elements(By.CSS_SELECTOR, selectors["product_links"][1])
    prices         = driver.find_elements(By.XPATH,        selectors["prices"][1])
    quantities     = driver.find_elements(By.CSS_SELECTOR, selectors["quantities"][1])

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
                "Date":           current_time(),
            })
        except Exception as e:
            logger.warning(f"Feil ved produkt {i}: {e}")
    return records


def _scrape_with_retry(driver, url, selectors, delay):
    for attempt in range(MAX_RETRIES):
        try:
            return _scrape_page(driver, url, selectors, delay)
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"Forsøk {attempt + 1}/{MAX_RETRIES} feilet: {e}. Venter {wait}s...")
            time.sleep(wait)
    logger.error(f"Alle {MAX_RETRIES} forsøk feilet for {url}")
    return None


def run_crawl(args):
    site_config = SITES[args.site]
    base_url    = site_config["base_url"]
    selectors   = site_config["selectors"]
    proxies     = [p.strip() for p in args.proxies.split(",")] if args.proxies else [None]
    formats     = args.format.split(",")

    if not check_robots_txt(base_url):
        logger.error("Scraping ikke tillatt av robots.txt. Avbryter.")
        return

    run_id = start_run(args.site, args.db)
    proxy_index = 0
    driver = setup_driver(proxies[0])
    all_records = []
    pages_scraped = 0
    error_msg = None

    try:
        page = 0
        while True:
            if args.pages and page >= args.pages:
                break

            if len(proxies) > 1:
                proxy = proxies[proxy_index % len(proxies)]
                proxy_index += 1
                driver.quit()
                driver = setup_driver(proxy)
                logger.info(f"Bytter til proxy: {proxy}")

            url = base_url + str(page)
            logger.info(f"Side {page}: {url}")
            records = _scrape_with_retry(driver, url, selectors, args.delay)

            if records is None:
                logger.info(f"Ingen produkter på side {page} — ferdig.")
                break

            all_records.extend(records)
            pages_scraped = page + 1
            logger.success(f"Side {page}: {len(records)} produkter")
            page += 1

    except KeyboardInterrupt:
        logger.warning("Avbrutt av bruker.")
        error_msg = "interrupted"
    except Exception as e:
        logger.error(f"Uventet feil: {e}")
        error_msg = str(e)
    finally:
        driver.quit()
        logger.info("Driver lukket.")

    status = "error" if error_msg and error_msg != "interrupted" else (
        "interrupted" if error_msg == "interrupted" else "ok"
    )
    finish_run(run_id, status, pages_scraped, len(all_records), error_msg, args.db)

    if not all_records:
        logger.warning("Ingen data å lagre.")
        return

    # Filtrering
    all_records = apply_filters(all_records, args)

    # Lagring og eksport
    save_products(all_records, args.site, args.db)
    export_data(all_records, args.output, formats)

    # Diff-rapport
    latest, previous = get_last_two_scrapes(args.site, args.db)
    if previous:
        report = generate_diff(previous, latest)
        print_report(report)
        if args.notify:
            notify_price_changes(report, NOTIFICATIONS)
    else:
        logger.info("Første kjøring — ingen tidligere data å sammenligne med.")

    # Prisvarsler
    check_alerts(all_records, args.site, args.db, NOTIFICATIONS)


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    setup_file_logging()

    args = build_arg_parser().parse_args()
    logger.info(f"Starter dynamisk crawler for '{args.site}'...")

    if args.schedule:
        run_on_schedule(args.schedule, run_crawl, args)
    else:
        run_crawl(args)


if __name__ == "__main__":
    main()
