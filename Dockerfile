FROM python:3.11-slim

# Installer Chromium og chromedriver via apt
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Sett chromedriver-sti slik at webdriver_manager ikke laster ned en ny
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV CHROME_BIN=/usr/bin/chromium

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Datamappen monteres som volum fra docker-compose
VOLUME ["/app/data"]

ENV DATABASE_FILE=/app/data/products.db
ENV LOG_FILE=/app/data/crawler.log

ENTRYPOINT ["python", "dynamic_crawler.py"]
