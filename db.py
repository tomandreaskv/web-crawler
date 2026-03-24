import sqlite3
from datetime import datetime

from loguru import logger


# ---------------------------------------------------------------------------
# Tilkobling og schema
# ---------------------------------------------------------------------------

def _connect(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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
        );

        CREATE TABLE IF NOT EXISTS runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            site            TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT,
            pages_scraped   INTEGER DEFAULT 0,
            products_found  INTEGER DEFAULT 0,
            error_message   TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            site           TEXT NOT NULL,
            product_number TEXT NOT NULL,
            target_price   REAL NOT NULL,
            created_at     TEXT NOT NULL,
            triggered_at   TEXT,
            notified       INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Produkter
# ---------------------------------------------------------------------------

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
                "Title":          r[1],
                "Price":          r[2],
                "Quantity":       r[3],
                "Link":           r[4],
            }
            for r in cur.fetchall()
        ]

    latest   = fetch(dates[0]) if len(dates) >= 1 else []
    previous = fetch(dates[1]) if len(dates) >= 2 else []
    conn.close()
    return latest, previous


def get_products(site, db_file, limit=200, offset=0, title=None, max_price=None, in_stock=False):
    """Henter siste scrape-resultat med valgfri filtrering (brukes av API og dashboard)."""
    conn = _connect(db_file)
    cursor = conn.execute(
        "SELECT DISTINCT scraped_at FROM products WHERE site = ? ORDER BY scraped_at DESC LIMIT 1",
        (site,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    latest_date = row[0]
    query = (
        "SELECT product_number, title, description, price, quantity, link, scraped_at "
        "FROM products WHERE site = ? AND scraped_at = ?"
    )
    params = [site, latest_date]

    if title:
        query += " AND title LIKE ?"
        params.append(f"%{title}%")
    if in_stock:
        query += " AND quantity != '' AND quantity != '0'"
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = [dict(r) for r in rows]

    if max_price is not None:
        def _parse_price(p):
            try:
                return float(str(p).replace(" ", "").replace(",", ".").replace("kr", "").strip())
            except (ValueError, TypeError):
                return float("inf")

        result = [r for r in result if _parse_price(r.get("price", "")) <= max_price]

    return result


def get_product_history(product_number, site, db_file):
    """Henter fullstendig prishistorikk for ett produkt."""
    conn = _connect(db_file)
    rows = conn.execute(
        "SELECT product_number, title, price, quantity, scraped_at "
        "FROM products WHERE site = ? AND product_number = ? ORDER BY scraped_at ASC",
        (site, product_number),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Kjørehistorikk
# ---------------------------------------------------------------------------

def start_run(site, db_file):
    """Registrerer start av en crawl-kjøring, returnerer run_id."""
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO runs (site, started_at, status) VALUES (?, ?, 'running')",
        (site, now),
    )
    run_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return run_id


def finish_run(run_id, status, pages, products, error, db_file):
    """Oppdaterer en kjøring med sluttresultat."""
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        UPDATE runs SET finished_at = ?, status = ?,
            pages_scraped = ?, products_found = ?, error_message = ?
        WHERE id = ?
        """,
        (now, status, pages, products, error, run_id),
    )
    conn.commit()
    conn.close()


def get_runs(db_file, limit=50):
    conn = _connect(db_file)
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run(run_id, db_file):
    conn = _connect(db_file)
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Prisvarsler
# ---------------------------------------------------------------------------

def add_alert(site, product_number, target_price, db_file):
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO alerts (site, product_number, target_price, created_at) VALUES (?, ?, ?, ?)",
        (site, product_number, target_price, now),
    )
    conn.commit()
    conn.close()
    logger.success(f"Varsel lagt til: {product_number} @ {target_price}")


def get_active_alerts(site, db_file):
    conn = _connect(db_file)
    rows = conn.execute(
        "SELECT * FROM alerts WHERE site = ? AND triggered_at IS NULL",
        (site,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_alerts(db_file):
    conn = _connect(db_file)
    rows = conn.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_triggered(alert_id, db_file):
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE alerts SET triggered_at = ?, notified = 1 WHERE id = ?",
        (now, alert_id),
    )
    conn.commit()
    conn.close()


def delete_alert(alert_id, db_file):
    conn = _connect(db_file)
    conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()
