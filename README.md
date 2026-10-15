# Amul Protein Tracker

[![CI Tests](https://github.com/tesla59/amul-protein-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/tesla59/amul-protein-tracker/actions/workflows/ci.yml)
[![Stock Check](https://github.com/tesla59/amul-protein-tracker/actions/workflows/stock-check.yml/badge.svg)](https://github.com/tesla59/amul-protein-tracker/actions/workflows/stock-check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An automated, reliable stock-monitoring and notification service for products on Amul's online store ([shop.amul.com](https://shop.amul.com/en/browse/protein)).

When a monitored product transitions between stock states (e.g., from **Sold Out** to **Back in Stock**), the tracker immediately sends a rich notification to your personal or group Telegram channel.

> [!IMPORTANT]
> **Disclaimer & Scope:** This is an unofficial, personal utility not affiliated with, authorized, or endorsed by GCMMF / Amul. This project is strictly a read-only stock notifier. It **never** adds items to cart, performs automated checkout, or places orders. Stock availability can fluctuate rapidly between check cycles and is not guaranteed at purchase time.

---

## Features

- **StoreHippo Platform Integration:** Resolves live inventory using StoreHippo session handshakes, location preference scoping, and authenticated REST entity queries.
- **Strict Stock State Machine:** Emits exactly one of `IN_STOCK`, `OUT_OF_STOCK`, or `UNKNOWN`.
- **Fail-Closed Safety:** Incomplete fetches, network drops, bot challenges, or malformed data evaluate to `UNKNOWN`. The tracker **never** falsely assumes `OUT_OF_STOCK` on failed checks.
- **Transition-Only Alerts:** Duplicate notifications are suppressed when stock remains unchanged. First-run alerts only fire for products that are currently in stock.
- **Health Escalation & Recovery:** Automatically tracks consecutive check failures. If failures meet the configured threshold (default: 3), a Telegram alert is sent once. When normal service resumes, a recovery notification is delivered.
- **Dedicated Git State Persistence:** Keeps stock history and health metrics on an isolated `stock-state` Git branch, keeping the `main` source branch clean and history free from hourly commit noise.
- **Local Diagnostics:** Includes a `--verify-only` mode to test live site connectivity, substore resolution, and inventory parsing without updating state or sending alerts.

---

## Monitored Delivery Location

- **Pincode:** `411036`
- **Resolved Substore:** `pune-br` (StoreHippo ID: `66506004a7cddee1b8adb014`)

---

## Getting Started

### 1. Prerequisites

- Python 3.10+
- A Telegram account and bot token

### 2. Local Installation

Clone the repository and install the package in editable mode:

```bash
git clone https://github.com/tesla59/amul-protein-tracker.git
cd amul-protein-tracker

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies and CLI
pip install -e ".[dev]"
```

### 3. Local Configuration (`.env`)

Create a local `.env` file for your Telegram bot credentials (this file is ignored by Git):

```env
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_CHAT_ID="987654321"
```

---

## Setting Up Telegram Notifications

1. **Create a Telegram Bot:**
   - Open Telegram and message [@BotFather](https://t.me/BotFather).
   - Send `/newbot`, choose a name and username for your bot.
   - BotFather will provide an HTTP API token (e.g., `123456789:ABCdef...`). Save this as `TELEGRAM_BOT_TOKEN`.

2. **Obtain Your Chat ID:**
   - Start a conversation with your new bot by clicking **Start** or sending `/start`.
   - Send a message to [@userinfobot](https://t.me/userinfobot) to get your numerical user/chat ID (e.g., `987654321`). Save this as `TELEGRAM_CHAT_ID`.
   - *(Optional for Groups)*: Add your bot to a group or channel, make it an admin, and use the group chat ID (which typically starts with a minus sign, e.g., `-1001234567890`).

3. **Verify Telegram Notifications:**
   Test your configuration locally or run unit tests to ensure everything is connected.

---

## Managing Monitored Products

Products are defined in `config/products.yaml`:

```yaml
location:
  pincode: "411036"
  expected_substore: "pune-br"

monitor:
  interval_minutes: 60
  failure_warning_threshold: 3
  request_timeout_seconds: 25
  max_consecutive_retries: 2

products:
  - id: "amul-whey-protein-32-g-or-pack-of-30-sachets"
    name: "Amul Whey Protein, 32 g | Pack of 30 Sachets"
    url: "https://shop.amul.com/product/amul-whey-protein-32-g-or-pack-of-30-sachets"
    enabled: true

  - id: "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
    name: "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
    url: "https://shop.amul.com/product/amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
    enabled: true
```

- **Add a Product:** Find the product slug on [shop.amul.com](https://shop.amul.com/en/browse/protein), copy the URL, and append an entry to `products`.
- **Disable a Product:** Set `enabled: false` to temporarily stop monitoring without deleting the entry.

---

## Running Locally

### Diagnostic / Live Verification Mode
Test the live StoreHippo API connection, location verification, and current inventory without sending alerts or writing state:

```bash
amul-tracker --verify-only
```

Sample output:
```text
=== Amul Protein Tracker: Diagnostic / Live Verification ===
Configured Pincode: 411036
Expected Substore: pune-br
Configured Products: 8 (8 enabled)

✅ Location verified: substore 'pune-br'
✅ Fetched 23 product records from StoreHippo API

--- Configured Products Evaluation ---
📦 Amul Whey Protein, 32 g | Pack of 30 Sachets
   Slug: amul-whey-protein-32-g-or-pack-of-30-sachets
   Stock State: OUT_OF_STOCK
   Reason: Explicitly unavailable (available=0, inventory_quantity=0)
   Inventory Qty: 0

📦 Amul High Protein Plain Lassi, 200 mL | Pack of 30
   Slug: amul-high-protein-plain-lassi-200-ml-or-pack-of-30
   Stock State: IN_STOCK
   Reason: In stock (available=1, inventory_quantity=82)
   Inventory Qty: 82
```

### Full Run
Execute a check cycle, evaluate transitions, and persist state to `state.json`:

```bash
amul-tracker -c config/products.yaml -s state.json
```

---

## Running Automated Tests

Run the full pytest suite (no live network access required):

```bash
pytest -v
```

All 44 automated unit tests cover:
- In-stock and out-of-stock markers
- Missing products and safe fallback to `UNKNOWN`
- Contradictory inventory states
- Cloudflare challenges and malformed responses
- Duplicate alert suppression
- Transitions (`OUT_OF_STOCK` ↔ `IN_STOCK`)
- First-run behavior
- Consecutive failure escalation and recovery
- YAML schema and duplicate ID validation
- State file corruption resilience
- Substore location mismatch protection

---

## GitHub Actions Automated Deployment

The tracker runs automatically every hour via GitHub Actions (`.github/workflows/stock-check.yml`).

### 1. Configure Repository Secrets

In your GitHub repository:
1. Navigate to **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token.
   - `TELEGRAM_CHAT_ID`: Your numerical Telegram chat ID.

### 2. How State Persistence Works

State is persisted on a dedicated orphan Git branch named `stock-state`:
1. Each workflow run fetches `state.json` from the `stock-state` branch.
2. The tracker checks stock and updates `state.json`.
3. If state changed, GitHub Actions commits `state.json` directly to `stock-state` with `[skip ci]`.
4. If state is unchanged, no commit is generated.

### 3. How to Reset State

If you want to clear tracking history and reset all stock states:
```bash
git push origin --delete stock-state
```
On the next run, the tracker will automatically re-initialize a fresh `state.json`.

### 4. Manual Execution

You can manually trigger a check at any time:
1. Go to the **Actions** tab in GitHub.
2. Select **Amul Stock Monitor**.
3. Click **Run workflow** (with optional `verify_only` flag).

---

## Troubleshooting

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| `LocationVerificationError: substore mismatch` | Pincode did not map to the expected substore (e.g. `pune-br`). | Check if Amul changed delivery hubs. Verify via `amul-tracker --verify-only`. |
| `AmulBlockedError: Cloudflare / anti-bot challenge` | Cloudflare bot protection activated. | The checker fails closed with `UNKNOWN` to avoid false alerts. Back off check frequency. |
| `Missing required Telegram credentials` | Secrets not set in environment or GitHub Secrets. | Ensure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. |
| Telegram messages not arriving | Bot not started or chat ID incorrect. | Message `/start` to your bot first so Telegram allows it to message you. |
| Action started late | GitHub Actions cron scheduling is best-effort. | Normal behavior on GitHub public runner queues; check runs when runner capacity is free. |

---

## Architecture Overview

```text
amul-protein-tracker/
├── .github/
│   └── workflows/
│       ├── ci.yml              # Automated test suite on push/PR
│       └── stock-check.yml     # Hourly cron monitor with Git state persistence
├── config/
│   └── products.yaml           # Configured location and monitored products
├── src/
│   └── amul_tracker/
│       ├── __init__.py
│       ├── amul_client.py      # StoreHippo session handshake, location & REST API
│       ├── checker.py          # State evaluation, transition engine & health escalation
│       ├── config.py           # YAML schema validation & environment loader
│       ├── main.py             # CLI entrypoint & diagnostic tool
│       ├── parser.py           # Stock state machine (IN_STOCK / OUT_OF_STOCK / UNKNOWN)
│       ├── state.py            # Atomic state persistence & corruption guard
│       └── telegram.py         # Telegram message formatting & delivery
├── tests/
│   ├── fixtures/               # Sanitized HTML and API fixtures
│   ├── test_amul_client.py     # HTTP, auth, and retry tests
│   ├── test_checker.py         # Transition and failure alert tests
│   ├── test_config.py          # Configuration validation tests
│   ├── test_notifications.py   # Telegram delivery tests
│   ├── test_parser.py          # Stock state parsing tests
│   └── test_state.py           # State corruption & persistence tests
├── pyproject.toml              # Build & dependency metadata
├── README.md                   # Project documentation
└── SPEC.md                     # Engineering specification
```
