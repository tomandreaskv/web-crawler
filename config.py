import os

# Legg til nye nettsteder her ved å definere selektorer — ingen kodeendringer nødvendig
SITES = {
    "msorensen": {
        "name": "M. Sørensen",
        "base_url": "https://www.msorensen.no/sigarer?pageID=",
        "dynamic": True,   # krever JavaScript-rendering (Selenium)
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

REDIS_URL             = os.getenv("REDIS_URL", "redis://localhost:6379")
STEAL_THRESHOLD       = int(os.getenv("STEAL_THRESHOLD", "60"))       # sekunder før en worker regnes som dø
SELECTOR_MAX_AGE_DAYS = int(os.getenv("SELECTOR_MAX_AGE_DAYS", "7"))  # dager før selektorer re-valideres

REQUEST_DELAY  = 1.5
DATABASE_FILE  = os.getenv("DATABASE_FILE", "products.db")
MAX_RETRIES    = 3
HEADERS        = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

LOG_FILE       = os.getenv("LOG_FILE", "crawler.log")
LOG_ROTATION   = "10 MB"

# --- Varslinger ---
# Konfigurer via miljøvariabler eller rediger direkte her.
NOTIFICATIONS = {
    "email": {
        "enabled":   False,
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "username":  os.getenv("SMTP_USER", ""),
        "password":  os.getenv("SMTP_PASSWORD", ""),
        "from_addr": os.getenv("SMTP_FROM", ""),
        "to_addrs":  [a for a in os.getenv("NOTIFY_EMAIL", "").split(",") if a],
    },
    "slack": {
        "enabled":     False,
        "webhook_url": os.getenv("SLACK_WEBHOOK", ""),
    },
}

# Aktiver kanaler automatisk hvis env-variabler er satt
if NOTIFICATIONS["email"]["to_addrs"] and NOTIFICATIONS["email"]["username"]:
    NOTIFICATIONS["email"]["enabled"] = True
if NOTIFICATIONS["slack"]["webhook_url"]:
    NOTIFICATIONS["slack"]["enabled"] = True
