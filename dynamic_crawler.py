import sys
import time
from datetime import datetime

from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from config import SITES, MAX_RETRIES
from db import save_products, get_last_two_scrapes
from reporter import generate_diff, print_report
from utils import build_arg_parser, check_robots_txt, export_data, run_on_schedule


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

    descriptions  = driver.find_elements(By.CSS_SELECTOR, selectors["descriptions"][1])
    productnumbers = driver.find_elements(By.CSS_SELECTOR, selectors["productnumbers"][1])
    product_links = driver.find_elements(By.CSS_SELECTOR, selectors["product_links"][1])
    prices        = driver.find_elements(By.XPATH,        selectors["prices"][1])
    quantities    = driver.find_elements(By.CSS_SELECTOR, selectors["quantities"][1])

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


def scrape_page_with_retry(driver, url, selectors, delay):
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

    proxy_index = 0
    driver = setup_driver(proxies[0])
    all_records = []

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
            records = scrape_page_with_retry(driver, url, selectors, args.delay)

            if records is None:
                logger.info(f"Ingen produkter på side {page} — ferdig.")
                break

            all_records.extend(records)
            logger.success(f"Side {page}: {len(records)} produkter")
            page += 1

    except KeyboardInterrupt:
        logger.warning("Avbrutt av bruker.")
    finally:
        driver.quit()
        logger.info("Driver lukket.")

    if not all_records:
        logger.warning("Ingen data å lagre.")
        return

    save_products(all_records, args.site, args.db)
    export_data(all_records, args.output, formats)

    latest, previous = get_last_two_scrapes(args.site, args.db)
    if previous:
        print_report(generate_diff(previous, latest))
    else:
        logger.info("Første kjøring — ingen tidligere data å sammenligne med.")


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

    args = build_arg_parser().parse_args()
    logger.info(f"Starter dynamisk crawler for '{args.site}'...")

    if args.schedule:
        run_on_schedule(args.schedule, run_crawl, args)
    else:
        run_crawl(args)


if __name__ == "__main__":
    main()
