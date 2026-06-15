#!/usr/bin/env python3
"""URL Health Monitor - Pings URLs and sends alerts on failures."""

import json
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

import requests

# Configuration from environment variables
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
SEND_RECOVERY_ALERTS = os.getenv("SEND_RECOVERY_ALERTS", "true").lower() == "true"
# Only fire a down-alert after a target has been continuously failing for this many seconds.
ALERT_AFTER_SECONDS = int(os.getenv("ALERT_AFTER_SECONDS", "300"))

# URLs to monitor — comma-separated list or JSON array.
# Examples:
#   MONITOR_URLS=https://example.com/health,https://api.example.com/health
#   MONITOR_URLS=["https://example.com/health","https://api.example.com/health"]
_raw_urls = os.getenv("MONITOR_URLS", "")
if _raw_urls.strip().startswith("["):
    URLS_TO_MONITOR: list[str] = json.loads(_raw_urls)
else:
    URLS_TO_MONITOR = [u.strip() for u in _raw_urls.split(",") if u.strip()]

if not URLS_TO_MONITOR:
    raise RuntimeError("MONITOR_URLS is required but not set. Provide a comma-separated list of URLs to monitor.")

# Slack configuration
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# Email configuration
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # Comma-separated list

# Track URL states: True = healthy, False = unhealthy
url_states: dict[str, bool] = {}

# When each URL first started failing in the current outage (monotonic seconds).
# Cleared when the URL recovers.
url_first_failure_time: dict[str, float] = {}

# Whether a down-alert has been sent for the current outage window.
# Only when this is True will a recovery alert fire.
url_alert_sent: dict[str, bool] = {}


def check_url(url: str) -> tuple[bool, str]:
    """Check if a URL is reachable. Returns (success, error_message)."""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True)
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        return True, ""
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {str(e)[:100]}"
    except requests.exceptions.RequestException as e:
        return False, f"Request error: {str(e)[:100]}"


def send_slack_alert(failed_urls: list[tuple[str, str]], is_recovery: bool = False) -> bool:
    """Send a Slack alert for failed or recovered URLs."""
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not configured, skipping Slack alert")
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if is_recovery:
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "✅ URL(s) Recovered",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{len(failed_urls)} URL(s) are back online* at {timestamp}"
                }
            },
            {"type": "divider"}
        ]
        for url, _ in failed_urls:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• `{url}` ✓"
                }
            })
    else:
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 Cannot Reach URL(s)!",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{len(failed_urls)} URL(s) are unreachable* at {timestamp}"
                }
            },
            {"type": "divider"}
        ]
        for url, error in failed_urls:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• `{url}`\n  └ Error: _{error}_"
                }
            })

    payload = {"blocks": blocks}

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            print("Slack alert sent successfully")
            return True
        else:
            print(f"Slack alert failed: HTTP {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Slack alert failed: {e}")
        return False


def send_email_alert(failed_urls: list[tuple[str, str]], is_recovery: bool = False) -> bool:
    """Send an email alert for failed or recovered URLs."""
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO]):
        print("Email not fully configured, skipping email alert")
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    recipients = [email.strip() for email in EMAIL_TO.split(",")]

    if is_recovery:
        subject = f"✅ URLs Recovered - {len(failed_urls)} URL(s) Back Online"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #16a34a;">URLs Recovered</h2>
            <p><strong>{len(failed_urls)} URL(s) are back online</strong> at {timestamp}</p>
            <hr>
            <ul>
        """
        for url, _ in failed_urls:
            html_body += f"<li><code>{url}</code> ✓</li>"
        html_body += """
            </ul>
            <hr>
            <p style="color: #6b7280; font-size: 12px;">
                This alert was sent by the URL Health Monitor.
            </p>
        </body>
        </html>
        """
        text_body = f"URLs Recovered\n\n{len(failed_urls)} URL(s) are back online at {timestamp}\n\n"
        for url, _ in failed_urls:
            text_body += f"- {url} ✓\n"
    else:
        subject = f"🚨 Cannot Reach URL! - {len(failed_urls)} URL(s) Unreachable"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #dc2626;">URL Health Check Failed</h2>
            <p><strong>{len(failed_urls)} URL(s) are unreachable</strong> at {timestamp}</p>
            <hr>
            <table style="border-collapse: collapse; width: 100%;">
                <tr style="background-color: #f3f4f6;">
                    <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">URL</th>
                    <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Error</th>
                </tr>
        """
        for url, error in failed_urls:
            html_body += f"""
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;"><code>{url}</code></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{error}</td>
                </tr>
            """
        html_body += """
            </table>
            <hr>
            <p style="color: #6b7280; font-size: 12px;">
                This alert was sent by the URL Health Monitor.
            </p>
        </body>
        </html>
        """
        text_body = f"URL Health Check Failed\n\n{len(failed_urls)} URL(s) are unreachable at {timestamp}\n\n"
        for url, error in failed_urls:
            text_body += f"- {url}\n  Error: {error}\n"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        print("Email alert sent successfully")
        return True
    except Exception as e:
        print(f"Email alert failed: {e}")
        return False


def run_health_check() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Run health check on all URLs.

    Returns:
        Tuple of (urls_to_alert_down, urls_to_alert_recovered).

        urls_to_alert_down: URLs that have been continuously failing for >= ALERT_AFTER_SECONDS
            and have not yet had a down-alert sent this outage window.
        urls_to_alert_recovered: URLs that just came back up AND had a down-alert sent.
    """
    global url_states, url_first_failure_time, url_alert_sent
    now = time.monotonic()
    urls_to_alert_down = []
    urls_to_alert_recovered = []

    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Running health check...")

    for url in URLS_TO_MONITOR:
        success, error = check_url(url)
        was_healthy = url_states.get(url, True)  # Assume healthy on first check

        if success:
            print(f"  ✓ {url}")
            if not was_healthy:
                # URL just recovered
                if url_alert_sent.get(url, False):
                    urls_to_alert_recovered.append((url, ""))
                    print(f"    ^ RECOVERED (alert had fired)")
                else:
                    print(f"    ^ recovered before alert threshold — no recovery alert needed")
                # Reset outage tracking
                url_first_failure_time.pop(url, None)
                url_alert_sent[url] = False
            url_states[url] = True
        else:
            print(f"  ✗ {url} - {error}")
            if was_healthy:
                # First failure in this outage window — start the clock
                url_first_failure_time[url] = now
                url_alert_sent[url] = False
                print(f"    ^ first failure — waiting {ALERT_AFTER_SECONDS}s before alerting")
            else:
                # Still failing — check if threshold crossed
                first_failure = url_first_failure_time.get(url, now)
                elapsed = now - first_failure
                if not url_alert_sent.get(url, False) and elapsed >= ALERT_AFTER_SECONDS:
                    urls_to_alert_down.append((url, error))
                    url_alert_sent[url] = True
                    print(f"    ^ sustained {elapsed:.0f}s — firing alert")
                elif not url_alert_sent.get(url, False):
                    print(f"    ^ still failing ({elapsed:.0f}s / {ALERT_AFTER_SECONDS}s threshold)")
            url_states[url] = False

    return urls_to_alert_down, urls_to_alert_recovered


def main():
    """Main loop - runs health checks at configured interval."""
    slack_alert_enabled = bool(SLACK_WEBHOOK_URL)
    email_alert_enabled = bool(all([SMTP_HOST, EMAIL_TO]))
    if not slack_alert_enabled and not email_alert_enabled:
        print("ERROR: At least one alert method must be enabled.")
        return
    print("=" * 60)
    print("Heartbeat Monitor Started")
    print(f"Check interval: {CHECK_INTERVAL_SECONDS} seconds")
    print(f"Request timeout: {REQUEST_TIMEOUT_SECONDS} seconds")
    print(f"Alert after sustained failure: {ALERT_AFTER_SECONDS} seconds")
    print(f"Monitoring {len(URLS_TO_MONITOR)} URLs:")
    for url in URLS_TO_MONITOR:
        print(f"  - {url}")
    print(f"Slack alerts: {'Enabled' if slack_alert_enabled else 'Disabled'}")
    print(f"Email alerts: {'Enabled' if email_alert_enabled else 'Disabled'}")
    print(f"Recovery alerts: {'Enabled' if SEND_RECOVERY_ALERTS else 'Disabled'}")
    print("=" * 60)

    while True:
        try:
            urls_to_alert_down, urls_to_alert_recovered = run_health_check()

            if urls_to_alert_down:
                print(f"\n{len(urls_to_alert_down)} URL(s) past threshold — sending alerts...")
                send_slack_alert(urls_to_alert_down, is_recovery=False)
                send_email_alert(urls_to_alert_down, is_recovery=False)

            if urls_to_alert_recovered and SEND_RECOVERY_ALERTS:
                print(f"\n{len(urls_to_alert_recovered)} URL(s) recovered — sending recovery alerts...")
                send_slack_alert(urls_to_alert_recovered, is_recovery=True)
                send_email_alert(urls_to_alert_recovered, is_recovery=True)

            # Summary
            total_unhealthy = sum(1 for s in url_states.values() if not s)
            if total_unhealthy == 0:
                print("All URLs healthy.")
            else:
                print(f"{total_unhealthy} URL(s) currently unhealthy.")

        except Exception as e:
            print(f"Error during health check: {e}")

        print(f"Next check in {CHECK_INTERVAL_SECONDS} seconds...")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
