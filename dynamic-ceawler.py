import requests
from bs4 import BeautifulSoup
from selenium.webdriver import Chrome
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from datetime import datetime
import pandas as pd
import numpy as np
from pandas import DataFrame
import csv

#static
#response = requests.get('https://quotes.toscrape.com/')
#soup = BeautifulSoup(response.text, 'lxml')

names_text = []
descriptions_text = []
prices_text = []
quantities_text = []
product_links_text = []
productnumbers_text = []
scrape_date = []

def current_time():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

with open('msorensen_products.csv', mode='w') as csv_file:
   fieldnames = ['Link','Product number', 'Title', 'Description', 'Price','Quantity', 'Date']
   writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
   writer.writeheader()

url = "https://www.msorensen.no/sigarer?pageID="
#dynamic
options = ChromeOptions()
options.headless = True
driver = Chrome(executable_path='D:/dev/repo/web-crawler/chromedriver_win32/chromedriver.exe', options=options)
driver.get(url+'0')
soup = BeautifulSoup(driver.page_source, 'lxml')


names = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc1')
descriptions = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc2')
productnumbers = driver.find_elements(By.CSS_SELECTOR, 'span.product-desc-prod-num')
product_links = driver.find_elements(By.CSS_SELECTOR, 'div.description-cell > a.AdProductLink')
prices = driver.find_elements(By.XPATH,"//span[@class='locate-prices-1680  AddPriceLabel']")
stock = driver.find_elements(By.CSS_SELECTOR,"div.DynamicStockTooltipContainer")
quantities = driver.find_elements(By.CSS_SELECTOR,"div.DynamicStockTooltipContainer > span:nth-child(2)")

print(len(names))
print(len(product_links))
for x in range(len(names)):
    print(names[x].text)
    names_text.append(names[x].text)
    print(descriptions[x].text)
    descriptions_text.append(descriptions[x].text)
    print(productnumbers[x].text)
    productnumbers_text.append(productnumbers[x].text)
    print(quantities[x].text.strip("()").strip())
    quantities_text.append(quantities[x].text.strip("()").strip())
    print(prices[x].text)
    prices_text.append(prices[x].text)
    print(product_links[x].get_attribute('href'))
    product_links_text.append(product_links[x].get_attribute('href'))
    scrape_date.append(current_time())
    print("\n")

data = {'Link': product_links_text, 'Product number': productnumbers_text, 'Title': names_text, 'Description': descriptions_text, 'Price': prices_text, 'Quantity': quantities_text, 'Date': scrape_date}
df = pd.DataFrame(data)
df.to_csv('msorensen_products.csv', index=False, encoding='utf-8')


driver.quit()


