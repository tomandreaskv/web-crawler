"""
Varslingssystem for prisendringer.

Konfigurasjon skjer via config.py (NOTIFICATIONS-dict) eller miljøvariabler.
Send e-post: sett SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, NOTIFY_EMAIL
Send Slack:  sett SLACK_WEBHOOK
"""

import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from loguru import logger


# ---------------------------------------------------------------------------
# E-post
# ---------------------------------------------------------------------------

def send_email(subject, body, cfg):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["from_addr"]
        msg["To"]      = ", ".join(cfg["to_addrs"])
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from_addr"], cfg["to_addrs"], msg.as_string())

        logger.success(f"E-post sendt til {cfg['to_addrs']}")
    except Exception as e:
        logger.error(f"Feil ved sending av e-post: {e}")


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def send_slack(text, cfg):
    try:
        response = requests.post(
            cfg["webhook_url"],
            data=json.dumps({"text": text}),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        logger.success("Slack-melding sendt.")
    except Exception as e:
        logger.error(f"Feil ved sending av Slack-melding: {e}")


# ---------------------------------------------------------------------------
# Felles utsendingsfunksjon
# ---------------------------------------------------------------------------

def _format_report(report, title="Prisendringer"):
    lines = [f"=== {title} ==="]

    if report.get("price_changes"):
        lines.append(f"\nPrisendringer ({len(report['price_changes'])}):")
        for c in report["price_changes"]:
            lines.append(
                f"  ~ {c['title']} [{c['product_number']}]: "
                f"{c['old_price']} → {c['new_price']}"
            )

    if report.get("new_products"):
        lines.append(f"\nNye produkter ({len(report['new_products'])}):")
        for p in report["new_products"]:
            lines.append(f"  + {p.get('Title', '')} [{p.get('Product number', '')}]")

    if report.get("removed"):
        lines.append(f"\nFjernede produkter ({len(report['removed'])}):")
        for p in report["removed"]:
            lines.append(f"  - {p.get('Title', '')} [{p.get('Product number', '')}]")

    return "\n".join(lines)


def notify_price_changes(report, cfg):
    """Sender varsling dersom det er faktiske endringer i rapporten."""
    has_changes = (
        report.get("price_changes")
        or report.get("new_products")
        or report.get("removed")
    )
    if not has_changes:
        return

    message = _format_report(report)

    if cfg["email"]["enabled"]:
        send_email("Prisvarsel — web-crawler", message, cfg["email"])

    if cfg["slack"]["enabled"]:
        send_slack(message, cfg["slack"])


def notify_alert_triggered(alert, current_price, cfg):
    """Sender varsling når en målpris er nådd."""
    msg = (
        f"=== Målpris nådd ===\n"
        f"Produkt:    {alert['product_number']}\n"
        f"Målpris:    {alert['target_price']} kr\n"
        f"Gjeldende: {current_price}\n"
    )

    if cfg["email"]["enabled"]:
        send_email("Prisvarsel — målpris nådd", msg, cfg["email"])

    if cfg["slack"]["enabled"]:
        send_slack(msg, cfg["slack"])
