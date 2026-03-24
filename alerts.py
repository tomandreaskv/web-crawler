"""
Prisvarsel-håndtering.

Bruk:
  python alerts.py add msorensen COH-006 249
  python alerts.py list
  python alerts.py delete 3
"""

import argparse
import sys

from loguru import logger

from config import DATABASE_FILE, NOTIFICATIONS
from db import add_alert, get_active_alerts, get_all_alerts, delete_alert, mark_alert_triggered
from notifier import notify_alert_triggered


# ---------------------------------------------------------------------------
# Sjekk mot gjeldende produkter
# ---------------------------------------------------------------------------

def _parse_price(price_str):
    """Konverterer prissstreng til float, returnerer None ved feil."""
    try:
        cleaned = (
            str(price_str)
            .replace("\xa0", "")
            .replace(" ", "")
            .replace(",", ".")
            .replace("kr", "")
            .strip()
        )
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def check_alerts(current_products, site, db_file, notify_cfg):
    """
    Sammenligner aktive varsler mot current_products.
    Sender varsling og markerer varselet som utløst hvis målprisen er nådd.
    """
    active = get_active_alerts(site, db_file)
    if not active:
        return

    products_by_num = {
        p.get("Product number", ""): p
        for p in current_products
        if p.get("Product number")
    }

    for alert in active:
        product = products_by_num.get(alert["product_number"])
        if not product:
            continue

        current_price = _parse_price(product.get("Price", ""))
        if current_price is None:
            continue

        if current_price <= alert["target_price"]:
            logger.warning(
                f"Målpris nådd: {alert['product_number']} "
                f"({current_price} <= {alert['target_price']})"
            )
            notify_alert_triggered(alert, current_price, notify_cfg)
            mark_alert_triggered(alert["id"], db_file)


# ---------------------------------------------------------------------------
# CLI-grensesnitt
# ---------------------------------------------------------------------------

def _print_alerts(alerts):
    if not alerts:
        print("Ingen aktive varsler.")
        return
    print(f"{'ID':<5} {'Nettsted':<12} {'Produkt':<16} {'Målpris':>10}  {'Utløst'}")
    print("-" * 60)
    for a in alerts:
        triggered = a["triggered_at"] or "—"
        print(f"{a['id']:<5} {a['site']:<12} {a['product_number']:<16} {a['target_price']:>10.2f}  {triggered}")


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

    parser = argparse.ArgumentParser(
        description="Administrer prisvarsler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Eksempler:\n"
            "  python alerts.py add msorensen COH-006 249\n"
            "  python alerts.py list\n"
            "  python alerts.py delete 3\n"
        ),
    )
    parser.add_argument("--db", default=DATABASE_FILE)
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Legg til nytt prisvarsel")
    p_add.add_argument("site",           help="Nettsted (f.eks. msorensen)")
    p_add.add_argument("product_number", help="Produktnummer")
    p_add.add_argument("target_price",   type=float, help="Målpris i kr")

    # list
    sub.add_parser("list", help="Vis alle varsler")

    # delete
    p_del = sub.add_parser("delete", help="Slett et varsel")
    p_del.add_argument("id", type=int, help="Varsel-ID")

    args = parser.parse_args()

    if args.command == "add":
        add_alert(args.site, args.product_number, args.target_price, args.db)
    elif args.command == "list":
        _print_alerts(get_all_alerts(args.db))
    elif args.command == "delete":
        delete_alert(args.id, args.db)
        logger.success(f"Varsel {args.id} slettet.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
