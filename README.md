# Heartbeat Monitor

A lightweight Docker container that monitors URLs and sends Slack or email alerts when they become unreachable. One image, any environment — configure entirely via environment variables.

## Features

- Monitors any set of URLs at a configurable interval (default: every 60 seconds)
- Fires alerts only after a target has been **continuously failing for a configurable threshold** (default: 5 minutes) — eliminates single-check false positives
- Sends one alert per outage window; sends a recovery alert only if a down-alert already fired
- Slack webhook alerts with formatted blocks
- HTML email alerts via SMTP
- Published to `ghcr.io/automattf/heartbeat` on every push to `main`

## Quick Start

```bash
cp .env.example .env
# Edit .env: set MONITOR_URLS and SLACK_WEBHOOK_URL at minimum
docker compose up -d
```

## Configuration

All configuration is via environment variables. See `.env.example` for a template.

| Variable | Default | Required | Description |
|---|---|---|---|
| `MONITOR_URLS` | — | Yes | Comma-separated list (or JSON array) of URLs to monitor |
| `SLACK_WEBHOOK_URL` | — | Yes* | Slack incoming webhook URL |
| `ALERT_AFTER_SECONDS` | `300` | No | Seconds a target must fail continuously before an alert fires |
| `CHECK_INTERVAL_SECONDS` | `60` | No | How often to check each URL (seconds) |
| `REQUEST_TIMEOUT_SECONDS` | `30` | No | Timeout per HTTP request (seconds) |
| `SEND_RECOVERY_ALERTS` | `true` | No | Send a recovery alert when a URL comes back up |
| `SMTP_HOST` | — | No | SMTP hostname (all SMTP vars required together to enable email) |
| `SMTP_PORT` | `587` | No | SMTP port |
| `SMTP_USER` | — | No | SMTP username |
| `SMTP_PASSWORD` | — | No | SMTP password |
| `EMAIL_FROM` | — | No | Sender address |
| `EMAIL_TO` | — | No | Comma-separated recipient list |

\* `SLACK_WEBHOOK_URL` or all email vars required; at least one alert channel must be configured.

### MONITOR_URLS formats

```bash
# Comma-separated
MONITOR_URLS=https://example.com/health,https://api.example.com/health

# JSON array
MONITOR_URLS=["https://example.com/health","https://api.example.com/health"]
```

## Alert Behavior

The `ALERT_AFTER_SECONDS` threshold controls when alerts fire:

- First failure: starts a per-URL clock, logs "waiting Ns before alerting"
- Subsequent failures: logs elapsed/threshold time; fires alert once threshold is crossed
- Recovery before threshold: no alert, no recovery message (transient blip suppressed)
- Recovery after alert fired: sends a recovery alert (if `SEND_RECOVERY_ALERTS=true`)

Set `ALERT_AFTER_SECONDS=0` to alert on the first failed check (no sustained-failure requirement).

## Docker Image

Published to `ghcr.io/automattf/heartbeat` on every push to `main`.

```bash
# Pull latest
docker pull ghcr.io/automattf/heartbeat:latest

# Run directly
docker run -d \
  --name heartbeat \
  --restart unless-stopped \
  -e MONITOR_URLS="https://example.com/health,https://api.example.com/health" \
  -e SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..." \
  -e ALERT_AFTER_SECONDS=300 \
  ghcr.io/automattf/heartbeat:latest
```

## Kubernetes Deployment

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: heartbeat-secrets
type: Opaque
stringData:
  MONITOR_URLS: "https://example.com/health,https://api.example.com/health"
  SLACK_WEBHOOK_URL: "https://hooks.slack.com/services/..."
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: heartbeat
spec:
  replicas: 1
  selector:
    matchLabels:
      app: heartbeat
  template:
    metadata:
      labels:
        app: heartbeat
    spec:
      containers:
      - name: heartbeat
        image: ghcr.io/automattf/heartbeat:latest
        envFrom:
        - secretRef:
            name: heartbeat-secrets
        env:
        - name: ALERT_AFTER_SECONDS
          value: "300"
        - name: CHECK_INTERVAL_SECONDS
          value: "60"
```

## Setting up a Slack Webhook

1. Go to [Slack API Apps](https://api.slack.com/apps)
2. Create a new app or select an existing one
3. Enable "Incoming Webhooks"
4. Add a new webhook to your workspace and target channel
5. Copy the webhook URL into `SLACK_WEBHOOK_URL`

## GHCR Authentication

The image at `ghcr.io/automattf/heartbeat` is public. Kubernetes clusters can pull it without credentials. If your cluster policy requires explicit pull secrets, create one:

```bash
kubectl create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=<github-username> \
  --docker-password=<github-pat-with-read:packages>
```

Then add `imagePullSecrets: [{name: ghcr-pull}]` to the pod spec.
