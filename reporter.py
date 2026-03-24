from loguru import logger


def generate_diff(previous, current):
    """Sammenligner to scrape-resultater og returnerer endringer."""
    prev = {p["Product number"]: p for p in previous if p.get("Product number")}
    curr = {p["Product number"]: p for p in current if p.get("Product number")}

    new_products = [p for k, p in curr.items() if k not in prev]
    removed = [p for k, p in prev.items() if k not in curr]
    price_changes = []

    for num, c in curr.items():
        if num in prev:
            old_p = prev[num].get("Price", "")
            new_p = c.get("Price", "")
            if old_p != new_p and old_p and new_p:
                price_changes.append(
                    {
                        "product_number": num,
                        "title": c.get("Title", ""),
                        "old_price": old_p,
                        "new_price": new_p,
                    }
                )

    return {"new_products": new_products, "removed": removed, "price_changes": price_changes}


def print_report(report):
    new = report["new_products"]
    removed = report["removed"]
    changes = report["price_changes"]

    logger.info("=" * 52)
    logger.info("  ENDRINGSRAPPORT")
    logger.info("=" * 52)

    if new:
        logger.info(f"Nye produkter ({len(new)}):")
        for p in new:
            logger.info(f"  +  {p.get('Title', '')}  [{p.get('Product number', '')}]")

    if removed:
        logger.warning(f"Fjernede produkter ({len(removed)}):")
        for p in removed:
            logger.warning(f"  -  {p.get('Title', '')}  [{p.get('Product number', '')}]")

    if changes:
        logger.warning(f"Prisendringer ({len(changes)}):")
        for c in changes:
            logger.warning(
                f"  ~  {c['title']}  [{c['product_number']}]:  "
                f"{c['old_price']}  →  {c['new_price']}"
            )

    if not new and not removed and not changes:
        logger.info("  Ingen endringer siden forrige kjøring.")

    logger.info("=" * 52)
