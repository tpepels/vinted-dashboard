# Vinted Dashboard

A local, Dockerized dashboard for Vinted inventory, orders and notifications.

This repository is configured for the Vinted profile:

- https://www.vinted.pt/member/58344842

The app deliberately does **not** scrape Vinted's private/internal APIs. It keeps a local source of truth and reconstructs buy/sell order state from Vinted email notifications. This avoids coupling the dashboard to undocumented endpoints.

## What it does

- Overview with active listings, open sales, open purchases and unread notifications.
- Listings inventory with status, price, ISBN, dates and Vinted deep links.
- CSV import for existing listings.
- Automatic creation/update of sold listings from Vinted sale emails.
- Sell-order state tracking:
  - sold / awaiting shipment
  - shipping label generated
  - shipped
  - ready for buyer pickup
  - completed / cancelled
- Buy-order state tracking:
  - paid
  - shipped
  - ready for pickup
  - completed / cancelled
- Notification timeline from Vinted emails.
- Read/unread notification state.
- Persistent SQLite database.
- Runs as a single Docker container.

## Run

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Open:

```
http://SERVER_IP:5050
```

Health check:

```bash
curl http://localhost:5050/api/health
```

## Email sync

The current sync adapter uses IMAP. For Gmail, use a Google App Password rather than your normal password.

Set in `.env`:

```env
EMAIL_USERNAME=you@gmail.com
EMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Then use **Sync email** in the dashboard.

The app only stores parsed Vinted event data and the Gmail message ID used for de-duplication. It does not persist your mailbox password in the database.

## Import existing active listings

Use the **Import CSV** button. Column names are flexible. Supported aliases include:

- `title`, `name`, `item`
- `price`, `amount`
- `status`
- `url`, `vinted_url`, `link`
- `isbn`
- `listed_at`, `created_at`
- `image_url`

Example:

```csv
title,price,status,isbn,vinted_url
Stoner - John Williams,8.00,active,9780099561545,https://www.vinted.pt/items/...
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Tests:

```bash
pytest
```

## Data

SQLite lives at `./data/vinted.db` by default. Back up the `data` directory.

## Security

The intended deployment is LAN-only. Do not expose port 5050 directly to the public internet without adding authentication/reverse-proxy access control.
