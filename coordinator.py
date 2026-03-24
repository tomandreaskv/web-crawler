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

import redis as redis_lib
from loguru import logger

from config import SITES, DATABASE_FILE, REDIS_URL, STEAL_THRESHOLD
from db import start_run, finish_run


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
        logger.info(
            f"  {site:<16} | kø: {s['queue']:>4} | sider: {s['pages']:>5} "
            f"| produkter: {s['products']:>6} | uttømt: {s['exhausted']}"
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

    seed_queues(r, active_sites, reset=reset)
    logger.info("Køer klare — venter på workers (kjør: docker compose up --scale worker=N)")

    done = False
    try:
        while True:
            stolen = steal_stale_tasks(r)
            if stolen:
                logger.warning(f"Work-stealing: gjeninnkøyde {stolen} oppgave(r)")

            print_status(r, active_sites)

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
