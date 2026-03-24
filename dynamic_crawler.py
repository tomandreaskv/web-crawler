from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from loguru import logger
from datetime import datetime
import pandas as pd
import time
import sys

# --- Konfigurasjon ---
BASE_URL = "https://www.msorensen.no/sigarer?pageID="
OUTPUT_FILE = "msorensen_products.csv"
REQUEST_DELAY = 1.5  # sekunder mellom sider


def current_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def setup_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def scrape_page(driver, page_number):
    url = BASE_URL + str(page_number)
    logger.info(f"Scraper side {page_number}: {url}")
    driver.get(url)
    time.sleep(REQUEST_DELAY)

    names = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc1')
    if not names:
        logger.info(f"Ingen produkter funnet på side {page_number}, stopper.")
        return None

    descriptions = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc2')
    productnumbers = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc-prod-num')
    product_links = driver.find_elements(By.CSS_SELECTOR, 'div.description-cell > a.AdProductLink')
    prices = driver.find_elements(By.XPATH, "//span[@class='locate-prices-1680  AddPriceLabel']")
    quantities = driver.find_elements(By.CSS_SELECTOR, "div.DynamicStockTooltipContainer > span:nth-child(2)")

    records = []
    for i in range(len(names)):
        try:
            record = {
                'Link': product_links[i].get_attribute('href') if i < len(product_links) else '',
                'Product number': productnumbers[i].text if i < len(productnumbers) else '',
                'Title': names[i].text,
                'Description': descriptions[i].text if i < len(descriptions) else '',
                'Price': prices[i].text if i < len(prices) else '',
                'Quantity': quantities[i].text.strip("()").strip() if i < len(quantities) else '',
                'Date': current_time(),
            }
            records.append(record)
        except Exception as e:
            logger.warning(f"Feil ved produkt {i} på side {page_number}: {e}")
            continue

    logger.success(f"Hentet {len(records)} produkter fra side {page_number}")
    return records


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    logger.info("Starter dynamisk web-crawler...")

    driver = setup_driver()
    all_records = []

    try:
        page = 0
        while True:
            records = scrape_page(driver, page)
            if records is None:
                break
            all_records.extend(records)
            page += 1
    except KeyboardInterrupt:
        logger.warning("Avbrutt av bruker.")
    except Exception as e:
        logger.error(f"Uventet feil: {e}")
    finally:
        driver.quit()
        logger.info("Driver lukket.")

    if all_records:
        df = pd.DataFrame(all_records)
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
        logger.success(f"Lagret {len(all_records)} produkter til {OUTPUT_FILE}")
    else:
        logger.warning("Ingen data å lagre.")


if __name__ == "__main__":
    main()
