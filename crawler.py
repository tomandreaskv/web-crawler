import requests
from bs4 import BeautifulSoup
from loguru import logger
from datetime import datetime
import pandas as pd
import re
import sys
import time

# --- Konfigurasjon ---
BASE_URL = "https://www.msorensen.no/sigarer?pageID="
OUTPUT_FILE = "msorensen_products.csv"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
REQUEST_DELAY = 1.5  # sekunder mellom sider


def current_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def scrape_page(page_number):
    url = BASE_URL + str(page_number)
    logger.info(f"Scraper side {page_number}: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Nettverksfeil på side {page_number}: {e}")
        return None

    soup = BeautifulSoup(response.content, 'html.parser')
    products = soup.findAll('div', class_='d4-row d4-listing-row')

    if not products:
        logger.info(f"Ingen produkter på side {page_number}, stopper.")
        return None

    records = []
    for product in products:
        try:
            name = product.find('span', class_='product-desc1')
            description = product.find('span', class_='product-desc2')
            productnumber = product.find('span', class_='product-desc-prod-num')
            link = product.find('a', class_='AdProductLink')

            quantity = ''
            stockcontainer = product.find('div', class_='DynamicStockTooltipContainer')
            if stockcontainer:
                quantity_spans = stockcontainer.findAll('span')
                if quantity_spans:
                    quantity = quantity_spans[0].text.strip("()").strip()

            price = ''
            pricecontainer = product.find('div', class_='d4-listing-cell d4-col-2 price-cell')
            if pricecontainer:
                listprice = pricecontainer.find('div', class_='ListPriceContainer')
                if listprice:
                    label = listprice.find('div', class_='PriceLabelContainer')
                    if label:
                        price_span = label.find('span', id=re.compile(r'^adprice__1680'))
                        if price_span:
                            price = price_span.get('content', '')

            record = {
                'Link': link['href'] if link else '',
                'Product number': productnumber.text if productnumber else '',
                'Title': name.text if name else '',
                'Description': description.text if description else '',
                'Price': price,
                'Quantity': quantity,
                'Date': current_time(),
            }
            records.append(record)
        except Exception as e:
            logger.warning(f"Feil ved produkt: {e}")
            continue

    logger.success(f"Hentet {len(records)} produkter fra side {page_number}")
    return records


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    logger.info("Starter statisk web-crawler...")

    all_records = []
    page = 0

    try:
        while True:
            records = scrape_page(page)
            if records is None:
                break
            all_records.extend(records)
            page += 1
            time.sleep(REQUEST_DELAY)
    except KeyboardInterrupt:
        logger.warning("Avbrutt av bruker.")
    except Exception as e:
        logger.error(f"Uventet feil: {e}")

    if all_records:
        df = pd.DataFrame(all_records)
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
        logger.success(f"Lagret {len(all_records)} produkter til {OUTPUT_FILE}")
    else:
        logger.warning("Ingen data å lagre.")


if __name__ == "__main__":
    main()
