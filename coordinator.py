"""
Koordinator for distribuert crawling.

Ansvar:
  1. Seed Redis-køer med startside (0) for hvert nettsted
  2. Overvåk workers med heartbeat — stjel oppgaver fra trege/døde workers
  3. Vis live status i terminalen
  4. Lagre kjøringsresultat i SQLite når alt er ferdig

Start (koordinerer alle nettsteder i config.py):
  python coordinator.py

Start med spesifikke nettsteder:
  python coordinator.py --sites msorensen,annet-nettsted

Kun overvåking (ikke seed på nytt):
  python coordinator.py --no-reset
"""

import json
import os
import sys
import time
from datetime import datetime

import redis as redis_lib
from loguru import logger

from config import SITES, DATABASE_FILE, REDIS_URL, STEAL_THRESHOLD, SELECTOR_MAX_AGE_DAYS
from db import start_run, finish_run, get_selectors


# ---------------------------------------------------------------------------
# Redis-tilkobling
# ---------------------------------------------------------------------------

def get_redis():
    r = redis_lib.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    return r


# ---------------------------------------------------------------------------
# Kø-administrasjon
# ---------------------------------------------------------------------------

def ensure_selectors(sites, db_file):
    """
    Sjekk om selektorer er gyldige og ferske for hvert nettsted.
    - Ingen selektorer i DB → kjør heuristisk oppdagelse
    - Eldre enn SELECTOR_MAX_AGE_DAYS → re-valider mot live side
    - Validering feiler → kjør oppdagelse på nytt
    """
    from datetime import timedelta
    from selector_discovery import discover, validate_existing

    stale_after = datetime.now() - timedelta(days=SELECTOR_MAX_AGE_DAYS)

    for site in sites:
        site_cfg = SITES.get(site, {})
        base_url = site_cfg.get("base_url", "")
        is_dyn   = site_cfg.get("dynamic", False)
        record   = get_selectors(site, db_file)

        if record and record["valid"]:
            discovered = datetime.strptime(record["discovered_at"], "%Y-%m-%d %H:%M:%S")
            if discovered > stale_after:
                logger.info(f"'{site}': selektorer er ferske ({record['discovered_at']})")
                continue

            logger.info(f"'{site}': selektorer er {(datetime.now() - discovered).days} dager gamle — re-validerer")
            if validate_existing(site, db_file):
                continue
            logger.warning(f"'{site}': nettsiden har endret seg — re-oppdager")
        else:
            if record:
                logger.warning(f"'{site}': forrige oppdagelse var ugyldig — prøver på nytt")
            else:
                logger.info(f"'{site}': ingen selektorer i DB — starter heuristisk oppdagelse")

        if not base_url:
            logger.error(f"'{site}': base_url mangler i config.py — kan ikke oppdage selektorer")
            continue

        result = discover(base_url + "0", is_dyn, site, db_file)
        if not result and site_cfg.get("selectors"):
            logger.warning(f"'{site}': heuristikk feilet — faller tilbake til config.py-selektorer")


def seed_queues(r, sites, reset=True):
    """Initialiser køer for hvert nettsted med startside 0."""
    for site in sites:
        if reset:
            r.delete(
                f"queue:{site}",
                f"seen:{site}",
                f"exhausted:{site}",
                f"stats:{site}",
            )
        # Legg kun til side 0 — workers legger til neste side selv etter hvert
        if r.sadd(f"seen:{site}", 0):
            r.rpush(f"queue:{site}", json.dumps({"site": site, "page": 0}))
            logger.info(f"Kø initialisert for '{site}'")
        else:
            logger.info(f"Kø for '{site}' finnes allerede — hopper over seeding")


# ---------------------------------------------------------------------------
# Work-stealing
# ---------------------------------------------------------------------------

def steal_stale_tasks(r):
    """
    Finn workers som ikke har sendt heartbeat på STEAL_THRESHOLD sekunder.
    Legg oppgavene deres tilbake i køen slik at andre workers kan hente dem.
    """
    worker_keys = r.keys("worker:*")
    stolen = 0

    for key in worker_keys:
        data = r.hgetall(key)
        if not data:
            continue

        last_seen = float(data.get("last_seen", 0))
        age       = time.time() - last_seen

        if age > STEAL_THRESHOLD:
            raw_task = data.get("current_task", "")
            if raw_task:
                try:
                    task = json.loads(raw_task)
                    site = task["site"]
                    r.lpush(f"queue:{site}", json.dumps(task))
                    r.hset(key, "current_task", "")
                    stolen += 1
                    logger.warning(
                        f"Work-stealing: tok side {task['page']} ({site}) "
                        f"fra {key} ({age:.0f}s uten heartbeat)"
                    )
                except (json.JSONDecodeError, KeyError):
                    pass

    return stolen


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(r, sites):
    site_stats = {}
    for site in sites:
        stats = r.hgetall(f"stats:{site}")
        site_stats[site] = {
            "queue":     r.llen(f"queue:{site}"),
            "seen":      r.scard(f"seen:{site}"),
            "exhausted": r.scard(f"exhausted:{site}"),
            "products":  int(stats.get("products", 0)),
            "pages":     int(stats.get("pages",    0)),
        }

    workers = []
    for key in r.keys("worker:*"):
        data = r.hgetall(key)
        if data:
            age = round(time.time() - float(data.get("last_seen", time.time())), 1)
            workers.append({
                "id":         key.replace("worker:", ""),
                "status":     data.get("status", "?"),
                "age_s":      age,
                "dead":       age > STEAL_THRESHOLD,
                "task":       data.get("current_task", ""),
            })

    return site_stats, workers


def print_status(r, sites):
    stats, workers = get_status(r, sites)
    logger.info("─" * 65)
    for site, s in stats.items():
        circuit = r.hgetall(f"circuit:{site}")
        circuit_state = circuit.get("state", "closed") if circuit else "closed"
        logger.info(
            f"  {site:<16} | kø: {s['queue']:>4} | sider: {s['pages']:>5} "
            f"| produkter: {s['products']:>6} | uttømt: {s['exhausted']} "
            f"| circuit: {circuit_state}"
        )
    if workers:
        logger.info(f"  Workers ({len(workers)}):")
        for w in workers:
            dead_marker = " ⚠ DØD" if w["dead"] else ""
            task_info   = ""
            if w["task"]:
                try:
                    t = json.loads(w["task"])
                    task_info = f" → side {t.get('page')} ({t.get('site')})"
                except (json.JSONDecodeError, KeyError):
                    pass
            logger.info(
                f"    [{w['status']:>7}] {w['id']}  "
                f"(sist sett {w['age_s']}s siden){task_info}{dead_marker}"
            )
    else:
        logger.info("  Ingen workers tilkoblet ennå — venter...")


# ---------------------------------------------------------------------------
# Planlagt kjøring
# ---------------------------------------------------------------------------

def _should_run_schedule(schedule_str, last_run_iso):
    """
    Returns True if the schedule has triggered since last_run.
    schedule_str: "02:00" (daily at time), "6h" (every 6 hours), "30m" (every 30 min)
    """
    now = datetime.now()
    if not last_run_iso:
        return True
    last = datetime.fromisoformat(last_run_iso)

    if ":" in schedule_str:  # "HH:MM" — daglig
        h, m = map(int, schedule_str.split(":"))
        scheduled_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return last < scheduled_today <= now
    elif schedule_str.endswith("h"):
        hours = int(schedule_str[:-1])
        return (now - last).total_seconds() >= hours * 3600
    elif schedule_str.endswith("m"):
        minutes = int(schedule_str[:-1])
        return (now - last).total_seconds() >= minutes * 60
    return False


def check_schedules(r, sites, db_file):
    """Re-seed queues for sites whose schedule has triggered."""
    for site in sites:
        schedule_str = SITES.get(site, {}).get("schedule")
        if not schedule_str:
            continue
        last_run = r.get(f"last_schedule_run:{site}")
        if _should_run_schedule(schedule_str, last_run):
            logger.info(f"'{site}': planlagt kjøring trigget ({schedule_str}) — re-seeder kø")
            seed_queues(r, [site], reset=True)
            r.set(f"last_schedule_run:{site}", datetime.now().isoformat())


# ---------------------------------------------------------------------------
# Ferdig-deteksjon
# ---------------------------------------------------------------------------

def is_done(r, sites):
    """
    Ferdig når:
      - Alle køer er tomme
      - Minst én side er markert som uttømt (= pagination fant slutten)
      - Ingen workers jobber aktivt
    """
    for site in sites:
        if r.llen(f"queue:{site}") > 0:
            return False
        if r.scard(f"exhausted:{site}") == 0:
            return False

    # Sjekk at ingen worker jobber aktivt
    for key in r.keys("worker:*"):
        data = r.hgetall(key)
        if data.get("status") == "working" and data.get("current_task"):
            age = time.time() - float(data.get("last_seen", 0))
            if age <= STEAL_THRESHOLD:
                return False

    return True


# ---------------------------------------------------------------------------
# Hoved-løkke
# ---------------------------------------------------------------------------

def run_coordinator(sites=None, db_file=DATABASE_FILE, reset=True, interval=5):
    r = get_redis()
    active_sites = sites or list(SITES.keys())

    logger.info(f"Koordinator startet | Nettsteder: {active_sites}")

    # Opprett kjøringspost i SQLite for hvert nettsted
    run_ids = {}
    for site in active_sites:
        run_ids[site] = start_run(site, db_file)
        logger.info(f"Kjøring #{run_ids[site]} startet for '{site}'")

    # Valider / oppdage selektorer før vi starter
    if reset:
        logger.info("Sjekker selektorer for alle nettsteder ...")
        ensure_selectors(active_sites, db_file)

    seed_queues(r, active_sites, reset=reset)
    logger.info("Køer klare — venter på workers (kjør: docker compose up --scale worker=N)")

    done = False
    try:
        while True:
            stolen = steal_stale_tasks(r)
            if stolen:
                logger.warning(f"Work-stealing: gjeninnkøyde {stolen} oppgave(r)")

            print_status(r, active_sites)
            check_schedules(r, active_sites, db_file)

            if is_done(r, active_sites):
                logger.success("Alle nettsteder er ferdig crawlet!")
                done = True
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        logger.warning("Koordinator avbrutt av bruker.")

    # Lagre sluttresultat
    for site in active_sites:
        stats = r.hgetall(f"stats:{site}")
        finish_run(
            run_ids[site],
            status="ok" if done else "interrupted",
            pages=int(stats.get("pages",    0)),
            products=int(stats.get("products", 0)),
            error=None,
            db_file=db_file,
        )
        logger.info(
            f"'{site}': {stats.get('pages', 0)} sider, "
            f"{stats.get('products', 0)} produkter lagret i databasen"
        )


def main():
    logger.remove()
    logger.add(sys.stdout, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

    import argparse
    parser = argparse.ArgumentParser(description="Koordinator for distribuert crawling")
    parser.add_argument("--sites", default="",
                        help="Kommaseparert liste med nettsteder (default: alle i config.py)")
    parser.add_argument("--db",       default=DATABASE_FILE)
    parser.add_argument("--interval", type=int, default=5,
                        help="Sekunder mellom statusoppdateringer (default: 5)")
    parser.add_argument("--no-reset", action="store_true",
                        help="Ikke nullstill køer — fortsett en avbrutt kjøring")
    args = parser.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()] or None
    run_coordinator(sites, args.db, reset=not args.no_reset, interval=args.interval)


if __name__ == "__main__":
    main()
