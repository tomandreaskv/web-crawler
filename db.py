import hashlib
import json
import os
import sqlite3
from datetime import datetime

from loguru import logger


# ---------------------------------------------------------------------------
# Tilkobling og schema
# ---------------------------------------------------------------------------

def _connect(db_file):
    conn = sqlite3.connect(db_file, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # tillater parallelle skrivinger fra flere workers
    conn.execute("PRAGMA synchronous=NORMAL")
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

        CREATE TABLE IF NOT EXISTS site_selectors (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            site          TEXT NOT NULL,
            base_url      TEXT NOT NULL,
            dynamic       INTEGER DEFAULT 0,
            selectors     TEXT NOT NULL,   -- JSON: {"container": "...", "names": "...", ...}
            hit_counts    TEXT NOT NULL,   -- JSON: {"container": 20, "names": 20, ...}
            valid         INTEGER DEFAULT 1,
            discovered_at TEXT NOT NULL,
            validated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS webhooks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            url        TEXT NOT NULL,
            site       TEXT,           -- NULL = alle nettsteder
            event_type TEXT NOT NULL DEFAULT 'all',  -- 'price_change', 'new_product', 'back_in_stock', 'all'
            secret     TEXT,           -- valgfri HMAC-nøkkel for signaturverifisering
            created_at TEXT NOT NULL,
            active     INTEGER DEFAULT 1
        );
    """)
    conn.commit()

    # Migrer eksisterende databaser: legg til product_hash-kolonne hvis den mangler
    for col in [("products", "product_hash", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE {col[0]} ADD COLUMN {col[1]} {col[2]}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    return conn


# ---------------------------------------------------------------------------
# Produkter
# ---------------------------------------------------------------------------

def save_products(records, site, db_file):
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    skipped = 0

    for rec in records:
        product_number = rec.get("Product number", "")
        price    = rec.get("Price", "")
        quantity = rec.get("Quantity", "")
        product_hash = hashlib.md5(f"{price}|{quantity}".encode()).hexdigest()

        # Deduplisering: hopp over rader der pris og lagerstatus ikke har endret seg
        if product_number:
            existing = conn.execute(
                "SELECT product_hash FROM products WHERE site = ? AND product_number = ? "
                "ORDER BY scraped_at DESC LIMIT 1",
                (site, product_number),
            ).fetchone()
            if existing and existing[0] == product_hash:
                skipped += 1
                continue

        conn.execute(
            """
            INSERT INTO products
                (site, product_number, title, description, price, quantity, link, scraped_at, product_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site,
                product_number,
                rec.get("Title", ""),
                rec.get("Description", ""),
                price,
                quantity,
                rec.get("Link", ""),
                now,
                product_hash,
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    logger.success(f"Lagret {inserted} produkter til databasen ({db_file}) — {skipped} uendret hoppes over")
    _mirror_to_external(records, site, now)


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


# ---------------------------------------------------------------------------
# Selektorer — oppdagede og validerte CSS-selektorer per nettsted
# ---------------------------------------------------------------------------

def save_selectors(site, base_url, dynamic, selectors, hit_counts, valid, db_file):
    """Lagrer et nytt sett med selektorer. Eldre rader beholdes som historikk."""
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO site_selectors
            (site, base_url, dynamic, selectors, hit_counts, valid, discovered_at, validated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            site,
            base_url,
            int(dynamic),
            json.dumps(selectors, ensure_ascii=False),
            json.dumps(hit_counts),
            int(valid),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    status = "gyldig" if valid else "ugyldig"
    logger.info(f"Selektorer lagret for '{site}' ({status})")


def get_selectors(site, db_file):
    """
    Henter siste selektorer for et nettsted.
    Returnerer dict med nøklene: site, base_url, dynamic, selectors, valid, discovered_at
    — eller None hvis ingen finnes.
    """
    conn = _connect(db_file)
    row = conn.execute(
        """
        SELECT site, base_url, dynamic, selectors, hit_counts, valid, discovered_at, validated_at
        FROM site_selectors
        WHERE site = ?
        ORDER BY discovered_at DESC
        LIMIT 1
        """,
        (site,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    return {
        "site":         row["site"],
        "base_url":     row["base_url"],
        "dynamic":      bool(row["dynamic"]),
        "selectors":    json.loads(row["selectors"]),
        "hit_counts":   json.loads(row["hit_counts"]),
        "valid":        bool(row["valid"]),
        "discovered_at": row["discovered_at"],
        "validated_at": row["validated_at"],
    }


def get_selector_history(site, db_file):
    """Alle historiske selektor-versjoner for et nettsted."""
    conn = _connect(db_file)
    rows = conn.execute(
        "SELECT * FROM site_selectors WHERE site = ? ORDER BY discovered_at DESC",
        (site,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def save_webhook(url, site, event_type, secret, db_file):
    conn = _connect(db_file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        "INSERT INTO webhooks (url, site, event_type, secret, created_at) VALUES (?, ?, ?, ?, ?)",
        (url, site, event_type, secret, now)
    )
    wid = cursor.lastrowid
    conn.commit(); conn.close()
    return wid

def get_webhooks(db_file, site=None):
    conn = _connect(db_file)
    if site:
        rows = conn.execute(
            "SELECT * FROM webhooks WHERE active=1 AND (site=? OR site IS NULL)", (site,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM webhooks WHERE active=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_webhook(webhook_id, db_file):
    conn = _connect(db_file)
    conn.execute("UPDATE webhooks SET active=0 WHERE id=?", (webhook_id,))
    conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Ekstern DB-mirror
# ---------------------------------------------------------------------------

def _mirror_to_external(records, site, scraped_at):
    """Optionally mirror data to PostgreSQL/MySQL via DATABASE_URL env var."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url or not records:
        return
    try:
        import pandas as pd
        from sqlalchemy import create_engine
        engine = create_engine(db_url, pool_pre_ping=True)
        df = pd.DataFrame(records)
        df["site"] = site
        df["scraped_at"] = scraped_at
        df.to_sql("products", engine, if_exists="append", index=False)
        logger.debug(f"Speilte {len(records)} rader til ekstern DB")
    except Exception as e:
        logger.warning(f"Ekstern DB-mirror feilet: {e}")
