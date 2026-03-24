"""
REST API for web-crawler data.

Start:
  uvicorn api:app --reload --port 8000

Dokumentasjon:
  http://localhost:8000/docs
"""

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from config import DATABASE_FILE, SITES
from db import (
    add_alert,
    delete_alert,
    get_active_alerts,
    get_all_alerts,
    get_last_two_scrapes,
    get_product_history,
    get_products,
    get_run,
    get_runs,
)
from reporter import generate_diff

app = FastAPI(
    title="Web-crawler API",
    description="Hent produktdata, prishistorikk og prisvarsler fra web-crawleren.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Produkter
# ---------------------------------------------------------------------------

@app.get("/products", summary="Hent siste produktliste")
def list_products(
    site: str = Query("msorensen", description="Nettsted"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    title: Optional[str] = Query(None, description="Filtrer på tittel (delstreng)"),
    max_price: Optional[float] = Query(None, description="Maks pris"),
    in_stock: bool = Query(False, description="Kun produkter på lager"),
    db: str = Query(DATABASE_FILE),
):
    if site not in SITES:
        raise HTTPException(status_code=404, detail=f"Ukjent nettsted: {site}")
    return get_products(site, db, limit, offset, title, max_price, in_stock)


@app.get("/products/{product_number}/history", summary="Prishistorikk for ett produkt")
def product_history(
    product_number: str,
    site: str = Query("msorensen"),
    db: str = Query(DATABASE_FILE),
):
    history = get_product_history(product_number, site, db)
    if not history:
        raise HTTPException(status_code=404, detail=f"Ingen data for {product_number}")
    return history


# ---------------------------------------------------------------------------
# Diff-rapport
# ---------------------------------------------------------------------------

@app.get("/diff", summary="Endringsrapport mellom siste to kjøringer")
def diff(
    site: str = Query("msorensen"),
    db: str = Query(DATABASE_FILE),
):
    latest, previous = get_last_two_scrapes(site, db)
    if not latest:
        raise HTTPException(status_code=404, detail="Ingen data i databasen ennå.")
    if not previous:
        return {"message": "Kun én kjøring — ingen diff tilgjengelig.", "new_products": [], "removed": [], "price_changes": []}
    return generate_diff(previous, latest)


# ---------------------------------------------------------------------------
# Kjørehistorikk
# ---------------------------------------------------------------------------

@app.get("/runs", summary="Liste over alle crawl-kjøringer")
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    db: str = Query(DATABASE_FILE),
):
    return get_runs(db, limit)


@app.get("/runs/{run_id}", summary="Detaljer om én kjøring")
def get_run_detail(run_id: int, db: str = Query(DATABASE_FILE)):
    run = get_run(run_id, db)
    if not run:
        raise HTTPException(status_code=404, detail=f"Kjøring {run_id} ikke funnet.")
    return run


# ---------------------------------------------------------------------------
# Prisvarsler
# ---------------------------------------------------------------------------

class AlertCreate(BaseModel):
    site:           str
    product_number: str
    target_price:   float


@app.get("/alerts", summary="Liste over alle prisvarsler")
def list_alerts(db: str = Query(DATABASE_FILE)):
    return get_all_alerts(db)


@app.post("/alerts", status_code=201, summary="Opprett nytt prisvarsel")
def create_alert(body: AlertCreate, db: str = Query(DATABASE_FILE)):
    if body.site not in SITES:
        raise HTTPException(status_code=400, detail=f"Ukjent nettsted: {body.site}")
    add_alert(body.site, body.product_number, body.target_price, db)
    return {"message": "Varsel opprettet.", "product_number": body.product_number, "target_price": body.target_price}


@app.delete("/alerts/{alert_id}", summary="Slett et prisvarsel")
def remove_alert(alert_id: int, db: str = Query(DATABASE_FILE)):
    existing = get_all_alerts(db)
    if not any(a["id"] == alert_id for a in existing):
        raise HTTPException(status_code=404, detail=f"Varsel {alert_id} ikke funnet.")
    delete_alert(alert_id, db)
    return {"message": f"Varsel {alert_id} slettet."}


# ---------------------------------------------------------------------------
# Helse
# ---------------------------------------------------------------------------

@app.get("/health", summary="Helsesjekk")
def health():
    return {"status": "ok", "sites": list(SITES.keys())}
