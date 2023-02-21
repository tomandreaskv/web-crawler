import requests
from requests import get
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
from pandas import DataFrame
import csv
import re
import json
from selenium.webdriver import Chrome
from selenium.webdriver import ChromeOptions

Options
url ="https://www.msorensen.no/sigarer?pageID="
driver = Chrome(executable_path='D:/dev/repo/web-crawler/chromedriver_win32/chromedriver.exe')
driver.get('https://quotes.toscrape.com/js/')


with open('msorensen_products.csv', mode='w') as csv_file:
   fieldnames = ['Link','Product number', 'Title', 'Description', 'Price', 'Date']
   writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
   writer.writeheader()

headers = {'User-Agent': 'Mozilla/5.0'}

titles = []
descriptions = []
prices = []
quantities = []
product_links = []
productnumbers = []


def scrapeMSorensenOnlineStore(webpage, pageNumber):
  next_page = webpage + str(pageNumber)
  response = requests.get(str(next_page) ,headers=headers)
  soup = BeautifulSoup(response.content, 'html.parser')

  products = soup.findAll('div', class_ = 'd4-row d4-listing-row')
  for product in products:
     name = product.find('span', class_ = 'product-desc1')
     description = product.find('span', class_ = 'product-desc2')
     productnumber = product.find('span', class_ = 'product-desc-prod-num')

     stockcontainer = product.find('div', class_ = 'DynamicStockTooltipContainer')
     quantitySpans = stockcontainer.findAll('span')
     quantity = quantitySpans[0].text.strip("()").strip()

     pricecontainer = product.find('div', class_ = 'd4-listing-cell d4-col-2 price-cell')
     listpriceContainer = pricecontainer.find('div', class_ = 'ListPriceContainer')
     priceLabelContainer = listpriceContainer.find('div', class_ = 'PriceLabelContainer')
     price = priceLabelContainer.find('span', id=re.compile("^adprice__1680")).get('content')
     print(price)


scrapeMSorensenOnlineStore(url, 0)
data = {'Link': product_links, 'Product number': productnumbers, 'Title': titles, 'Description': descriptions, 'Price': prices, 'Date': quantities}
df = pd.DataFrame(data)
df.to_csv('msorensen_products.csv', index=False, encoding='utf-8')




#headers = {"Accept-Language": "en-US, en;q=0.5"}

#url = "https://www.msorensen.no/sigarer"

#results = requests.get(url, headers=headers)

#soup = BeautifulSoup(results.text, "html.parser")
#print(soup.prettify())



#productDiv = soup.find_all('div', class_ = 'd4-row d4-listing-row')

#for container in productDiv:
#    name = ""
#    description = ""
#    productNumber = ""
#    price = ""
#    quantity = ""
#    productLink = ""
#    descriptionContainer = container.find_all('div', class_ = 'd4-listing-cell d4-col description-cell')
#    for descriptionCell in descriptionContainer:
#      name = descriptionCell.a.find('span', class_ = 'product-desc1').text
#      description = descriptionCell.a.find('span', class_ = 'product-desc2').text
#      productNumber = descriptionCell.a.find('span', class_ = 'product-desc-prod-num').text
#      print(name)
#    
#    priceContainer = container.find('div', class_ = 'd4-listing-cell d4-col-2 price-cell').text
#    price = priceContainer.div.div.find('span', class_ = 'locate-prices-1680  AddPriceLabel').text
#
#    print("name: " + name +" description: " + description +" qty: " + quantity+" Price: " + price + " NOK" +" productNumber: " + productNumber + " productLink: " + productLink)

#field-paging-next paging-block paging-center knapp for flere produkter