import sqlite3
from datetime import datetime

from loguru import logger


def _connect(db_file):
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            site           TEXT NOT NULL,
            product_number TEXT,
            title          TEXT,
            description    TEXT,
            price          TEXT,
            quantity       TEXT,
            link           TEXT,
            scraped_at     TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def save_products(records, site, db_file):
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            site,
            r.get("Product number", ""),
            r.get("Title", ""),
            r.get("Description", ""),
            r.get("Price", ""),
            r.get("Quantity", ""),
            r.get("Link", ""),
            now,
        )
        for r in records
    ]
    conn.executemany(
        """
        INSERT INTO products
            (site, product_number, title, description, price, quantity, link, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    logger.success(f"Lagret {len(rows)} produkter til databasen ({db_file})")


def get_last_two_scrapes(site, db_file):
    """Returnerer (siste, forrige) som lister med produkt-dicts."""
    conn = _connect(db_file)
    cursor = conn.execute(
        "SELECT DISTINCT scraped_at FROM products WHERE site = ? ORDER BY scraped_at DESC LIMIT 2",
        (site,),
    )
    dates = [row[0] for row in cursor.fetchall()]

    def fetch(date):
        cur = conn.execute(
            "SELECT product_number, title, price, quantity, link "
            "FROM products WHERE site = ? AND scraped_at = ?",
            (site, date),
        )
        return [
            {
                "Product number": r[0],
                "Title": r[1],
                "Price": r[2],
                "Quantity": r[3],
                "Link": r[4],
            }
            for r in cur.fetchall()
        ]

    latest = fetch(dates[0]) if len(dates) >= 1 else []
    previous = fetch(dates[1]) if len(dates) >= 2 else []
    conn.close()
    return latest, previous
