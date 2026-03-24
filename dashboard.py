"""
Streamlit-dashboard for web-crawler.

Start:
  streamlit run dashboard.py
"""

import plotly.graph_objects as go
import streamlit as st

from config import DATABASE_FILE, SITES
from db import (
    add_alert,
    delete_alert,
    get_all_alerts,
    get_last_two_scrapes,
    get_product_history,
    get_products,
    get_runs,
)
from reporter import generate_diff


# ---------------------------------------------------------------------------
# Sideoppsett
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Web-crawler dashboard",
    page_icon="🔍",
    layout="wide",
)

st.title("Web-crawler dashboard")

# --- Sidepanel ---
with st.sidebar:
    st.header("Innstillinger")
    site = st.selectbox("Nettsted", list(SITES.keys()), format_func=lambda k: SITES[k]["name"])
    db_file = st.text_input("Databasefil", value=DATABASE_FILE)
    st.divider()
    st.caption("Oppdater siden for å hente ferske data.")

tab_produkter, tab_historikk, tab_endringer, tab_kjøringer, tab_varsler = st.tabs(
    ["Produkter", "Prishistorikk", "Endringer", "Kjørehistorikk", "Prisvarsler"]
)


# ---------------------------------------------------------------------------
# Tab 1 — Produkter
# ---------------------------------------------------------------------------

with tab_produkter:
    st.subheader("Siste produktliste")

    col1, col2, col3 = st.columns(3)
    filter_title    = col1.text_input("Søk i tittel")
    filter_price    = col2.number_input("Maks pris (kr)", min_value=0, value=0, step=10)
    filter_in_stock = col3.checkbox("Kun på lager")

    products = get_products(
        site,
        db_file,
        limit=500,
        title=filter_title or None,
        max_price=float(filter_price) if filter_price else None,
        in_stock=filter_in_stock,
    )

    if products:
        st.caption(f"{len(products)} produkter")
        st.dataframe(products, use_container_width=True, hide_index=True)
    else:
        st.info("Ingen produkter funnet. Har du kjørt crawleren?")


# ---------------------------------------------------------------------------
# Tab 2 — Prishistorikk
# ---------------------------------------------------------------------------

with tab_historikk:
    st.subheader("Prishistorikk per produkt")

    products_all = get_products(site, db_file, limit=500)
    if products_all:
        options = {
            f"{p['title']} [{p['product_number']}]": p["product_number"]
            for p in products_all
            if p.get("product_number")
        }
        chosen_label = st.selectbox("Velg produkt", list(options.keys()))
        chosen_num   = options[chosen_label]

        history = get_product_history(chosen_num, site, db_file)
        if len(history) > 1:
            dates  = [h["scraped_at"] for h in history]
            prices = []
            for h in history:
                try:
                    p = float(
                        str(h["price"])
                        .replace("\xa0", "").replace(" ", "")
                        .replace(",", ".").replace("kr", "").strip()
                    )
                    prices.append(p)
                except (ValueError, TypeError):
                    prices.append(None)

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dates, y=prices, mode="lines+markers", name="Pris (kr)"))
            fig.update_layout(
                xaxis_title="Dato",
                yaxis_title="Pris (kr)",
                hovermode="x unified",
                margin=dict(l=0, r=0, t=30, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)

            min_p = min(p for p in prices if p is not None)
            max_p = max(p for p in prices if p is not None)
            cur_p = prices[-1]
            m1, m2, m3 = st.columns(3)
            m1.metric("Gjeldende pris", f"{cur_p:.2f} kr" if cur_p else "—")
            m2.metric("Laveste",        f"{min_p:.2f} kr")
            m3.metric("Høyeste",        f"{max_p:.2f} kr")
        else:
            st.info("Trenger minst to scrape-kjøringer for å vise prishistorikk.")
    else:
        st.info("Ingen produkter i databasen ennå.")


# ---------------------------------------------------------------------------
# Tab 3 — Endringer
# ---------------------------------------------------------------------------

with tab_endringer:
    st.subheader("Endringer siden forrige kjøring")

    latest, previous = get_last_two_scrapes(site, db_file)
    if not latest:
        st.info("Ingen data ennå.")
    elif not previous:
        st.info("Kun én kjøring registrert — ingen diff tilgjengelig.")
    else:
        report = generate_diff(previous, latest)

        col_ny, col_fjernet, col_pris = st.columns(3)
        col_ny.metric("Nye produkter",      len(report["new_products"]))
        col_fjernet.metric("Fjernet",        len(report["removed"]))
        col_pris.metric("Prisendringer",     len(report["price_changes"]))

        if report["price_changes"]:
            st.markdown("#### Prisendringer")
            st.dataframe(report["price_changes"], use_container_width=True, hide_index=True)

        if report["new_products"]:
            st.markdown("#### Nye produkter")
            st.dataframe(report["new_products"], use_container_width=True, hide_index=True)

        if report["removed"]:
            st.markdown("#### Fjernede produkter")
            st.dataframe(report["removed"], use_container_width=True, hide_index=True)

        if not any([report["new_products"], report["removed"], report["price_changes"]]):
            st.success("Ingen endringer siden forrige kjøring.")


# ---------------------------------------------------------------------------
# Tab 4 — Kjørehistorikk
# ---------------------------------------------------------------------------

with tab_kjøringer:
    st.subheader("Kjørehistorikk")

    runs = get_runs(db_file, limit=100)
    if runs:
        for run in runs:
            status = run.get("status", "—")
            color = {"ok": "✅", "error": "❌", "interrupted": "⚠️", "running": "🔄"}.get(status, "❓")
            with st.expander(
                f"{color} {run['started_at']}  —  {run['site']}  "
                f"({run.get('products_found', 0)} produkter, {run.get('pages_scraped', 0)} sider)"
            ):
                st.json(run)
    else:
        st.info("Ingen kjøringer registrert ennå.")


# ---------------------------------------------------------------------------
# Tab 5 — Prisvarsler
# ---------------------------------------------------------------------------

with tab_varsler:
    st.subheader("Prisvarsler")

    # Skjema for nytt varsel
    with st.form("new_alert"):
        st.markdown("**Legg til nytt prisvarsel**")
        c1, c2, c3 = st.columns(3)
        a_site   = c1.selectbox("Nettsted", list(SITES.keys()), key="alert_site")
        a_num    = c2.text_input("Produktnummer", placeholder="COH-006")
        a_price  = c3.number_input("Målpris (kr)", min_value=0.0, step=1.0)
        submitted = st.form_submit_button("Legg til varsel")
        if submitted:
            if a_num.strip():
                add_alert(a_site, a_num.strip(), a_price, db_file)
                st.success(f"Varsel lagt til for {a_num} @ {a_price} kr")
                st.rerun()
            else:
                st.error("Produktnummer kan ikke være tomt.")

    st.divider()

    # Liste over eksisterende varsler
    alerts = get_all_alerts(db_file)
    if alerts:
        for alert in alerts:
            triggered = alert.get("triggered_at")
            badge = "✅ Utløst" if triggered else "🔔 Aktiv"
            col_info, col_del = st.columns([5, 1])
            col_info.markdown(
                f"**{alert['product_number']}** ({alert['site']})  —  "
                f"Målpris: **{alert['target_price']:.2f} kr**  —  {badge}"
            )
            if col_del.button("Slett", key=f"del_{alert['id']}"):
                delete_alert(alert["id"], db_file)
                st.rerun()
    else:
        st.info("Ingen prisvarsler opprettet ennå.")
