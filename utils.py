import argparse
import time
import urllib.robotparser
from urllib.parse import urlparse

import pandas as pd
import schedule
from loguru import logger

from config import REQUEST_DELAY, DATABASE_FILE, SITES


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Web-crawler for produktdata")
    parser.add_argument(
        "--site", default="msorensen", choices=list(SITES.keys()),
        help="Nettsted å scrape (default: msorensen)",
    )
    parser.add_argument(
        "--output", default="msorensen_products",
        help="Outputfilnavn uten filendelse (default: msorensen_products)",
    )
    parser.add_argument(
        "--format", default="csv",
        help="Eksportformat: csv, json, excel — kombiner med komma (default: csv)",
    )
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"Sekunder mellom sider (default: {REQUEST_DELAY})",
    )
    parser.add_argument(
        "--pages", type=int, default=0,
        help="Maks antall sider, 0 = alle (default: 0)",
    )
    parser.add_argument(
        "--proxies", default="",
        help="Kommaseparert liste med proxyer: http://1.2.3.4:8080,http://5.6.7.8:8080",
    )
    parser.add_argument(
        "--db", default=DATABASE_FILE,
        help=f"SQLite-databasefil (default: {DATABASE_FILE})",
    )
    parser.add_argument(
        "--schedule", default="", metavar="INTERVAL",
        help="Kjør automatisk på intervall: 30m, 2h, eller klokkeslett 02:00",
    )
    return parser


def check_robots_txt(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        allowed = rp.can_fetch("*", url)
        if allowed:
            logger.info(f"robots.txt: scraping tillatt for {parsed.netloc}")
        else:
            logger.warning(f"robots.txt: scraping IKKE tillatt for {parsed.netloc}")
        return allowed
    except Exception as e:
        logger.warning(f"Kunne ikke lese robots.txt ({e}) — fortsetter likevel.")
        return True


def export_data(records, output_base, formats):
    df = pd.DataFrame(records)
    for fmt in formats:
        fmt = fmt.strip().lower()
        if fmt == "csv":
            path = output_base + ".csv"
            df.to_csv(path, index=False, encoding="utf-8")
            logger.success(f"CSV eksportert: {path}")
        elif fmt == "json":
            path = output_base + ".json"
            df.to_json(path, orient="records", force_ascii=False, indent=2)
            logger.success(f"JSON eksportert: {path}")
        elif fmt == "excel":
            path = output_base + ".xlsx"
            df.to_excel(path, index=False)
            logger.success(f"Excel eksportert: {path}")
        else:
            logger.warning(f"Ukjent eksportformat: '{fmt}' — hopper over.")


def run_on_schedule(interval_str, job_fn, args):
    """
    interval_str eksempler: '30m', '2h', '02:00'
    Kjører job_fn(args) umiddelbart, deretter på det gitte intervallet.
    """
    try:
        if ":" in interval_str:
            schedule.every().day.at(interval_str).do(job_fn, args)
            logger.info(f"Planlagt daglig kjøring kl. {interval_str}")
        elif interval_str.endswith("m"):
            minutes = int(interval_str[:-1])
            schedule.every(minutes).minutes.do(job_fn, args)
            logger.info(f"Planlagt kjøring hvert {minutes}. minutt")
        elif interval_str.endswith("h"):
            hours = int(interval_str[:-1])
            schedule.every(hours).hours.do(job_fn, args)
            logger.info(f"Planlagt kjøring hver {hours}. time")
        else:
            raise ValueError(f"Ukjent format: '{interval_str}'")
    except (ValueError, AttributeError) as e:
        logger.error(f"Ugyldig --schedule verdi: {e}")
        logger.error("Gyldige formater: '30m', '2h', '02:00'")
        return

    job_fn(args)  # kjør umiddelbart første gang
    while True:
        schedule.run_pending()
        time.sleep(30)
