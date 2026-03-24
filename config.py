# Legg til nye nettsteder her ved å definere selektorer — ingen kodeendringer nødvendig
SITES = {
    "msorensen": {
        "name": "M. Sørensen",
        "base_url": "https://www.msorensen.no/sigarer?pageID=",
        "selectors": {
            "names":          ("css",   "span.product-desc1"),
            "descriptions":   ("css",   "span.product-desc2"),
            "productnumbers": ("css",   "span.product-desc-prod-num"),
            "product_links":  ("css",   "div.description-cell > a.AdProductLink"),
            "prices":         ("xpath", "//span[@class='locate-prices-1680  AddPriceLabel']"),
            "quantities":     ("css",   "div.DynamicStockTooltipContainer > span:nth-child(2)"),
        },
    },
}

REQUEST_DELAY = 1.5   # sekunder mellom sider
DATABASE_FILE = "products.db"
MAX_RETRIES = 3
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
