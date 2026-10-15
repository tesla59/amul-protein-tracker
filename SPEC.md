# Amul Protein Tracker — Project Specification

**Status:** Implementation handoff spec. The stock-data integration is not yet verified end-to-end; do not represent the monitor as production-ready until the acceptance tests below pass.

## 1. Goal

Build a small, public GitHub repository that monitors products on Amul's protein shop page and sends Telegram notifications when a product's stock status changes.

- Shop page: https://shop.amul.com/en/browse/protein
- Intended delivery pincode: `411036`
- Pincode lookup previously resolved this to substore: `pune-br`
- Run frequency: every 60 minutes via GitHub Actions
- Runtime: Python
- Configuration: YAML
- State persistence: dedicated Git branch
- Notifications: Telegram Bot API

This project is a stock-change notifier, not an ordering bot. It must never add products to cart or place orders.

## 2. Core requirements

### 2.1 Product configuration

Products must be managed in a YAML file, not hard-coded in the Python checker. Support a list of products with stable IDs/slugs, display names, and product URLs. Example shape (adjust fields to the final parser):

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
  - id: "amul-product-slug"
    name: "Example Amul Protein Product"
    url: "https://shop.amul.com/product/example-product"
    enabled: true
```

The example product is illustrative only. Populate `products` with verified real product URLs before enabling scheduled alerts. Allow disabled entries to remain in the config.

### 2.2 Stock states

The parser must return exactly one of:

- `IN_STOCK`
- `OUT_OF_STOCK`
- `UNKNOWN`

`UNKNOWN` is mandatory for missing, malformed, contradictory, or unrecognized data. Never treat an empty page, HTTP error page, bot challenge, login/pincode modal, missing product card, or failed parse as `OUT_OF_STOCK` or `IN_STOCK`.

Known markup from a saved page capture:

- Product cards use `.product-grid-item`.
- Product links use `/product/<slug>`.
- Product names use `.product-grid-name`.
- Out-of-stock cards have class `outofstock` and `.stock-indicator-text` containing `Sold Out`.
- In-stock cards have an `a[title="Add to Cart"]` control whose text is `ADD`.

Treat these as observed selectors from a saved capture, not a guarantee that the live site always uses them. Validate the live page and adapt selectors conservatively. If signals conflict, return `UNKNOWN` and log the reason.

### 2.3 Notifications

Send Telegram notifications only for stock-state transitions:

- `OUT_OF_STOCK` → `IN_STOCK`: notify that the product is back in stock.
- `IN_STOCK` → `OUT_OF_STOCK`: notify that the product is sold out.
- Same known state as previous run: no notification.
- `UNKNOWN`: do not overwrite the last known state and do not send a stock-change notification.

**First-run behavior:** If no prior state exists and the current result is `IN_STOCK`, send an initial “currently in stock” alert. If it is `OUT_OF_STOCK`, save the known state without sending a first-run alert unless the project owner explicitly changes this rule. If it is `UNKNOWN`, do not create a false stock state.

Include product name, state/change, configured pincode (`411036`), check timestamp in India time (Asia/Kolkata), and product URL in messages. Escape Telegram Markdown/HTML safely or use plain text.

### 2.4 Failure warnings

Track consecutive *checker failures* separately from stock states. A checker failure includes a network/HTTP failure, blocked/challenge page, invalid response format, failure to resolve the configured location, or a page that cannot be parsed reliably. A product legitimately marked `UNKNOWN` should count as a failed check if the reason indicates the checker could not establish stock status.

- After `failure_warning_threshold` consecutive failed runs (default `3`), send a Telegram warning that monitoring is impaired.
- Avoid sending the same warning on every failed hourly run. Notify once when the threshold is crossed; optionally re-notify at a documented longer interval (e.g. every 24 hours) while the failure persists.
- When a successful check resumes after failures, send a recovery notification if a failure warning had previously been sent.
- A successful run resets the consecutive-failure counter.
- Do not change saved product stock state during failed checks.

### 2.5 State persistence

Persist state on a dedicated branch, e.g. `stock-state`, separate from the default source branch.

Persist at least:

```json
{
  "schema_version": 1,
  "last_successful_check": "ISO-8601 timestamp",
  "consecutive_failures": 0,
  "failure_warning_sent": false,
  "products": {
    "amul-product-slug": {
      "state": "IN_STOCK",
      "checked_at": "ISO-8601 timestamp"
    }
  }
}
```

Implementation constraints:

- If the state branch does not exist yet, initialize it safely.
- Read and update state atomically within a single workflow run.
- Do not commit state on a failed check if it would erase valid prior state.
- Commit only when state changes to avoid noisy commits.
- Handle empty branch/repository and missing state file gracefully.
- Use the GitHub Actions token with the minimum permissions required to read/write the state branch. Do not expose the token in logs.
- Avoid concurrent workflow runs (`concurrency`) so two runs cannot race and overwrite state.
- State branch commits must not trigger an infinite workflow loop. Use workflow path filters or explicit event restrictions.

A reasonable alternative is to use `actions/checkout` with the state branch and commit the state file there, or use GitHub API calls. Document the chosen strategy in README.

## 3. Amul website integration and known findings

### 3.1 Location selection

The website asks for a pincode in an incognito session. A pincode lookup endpoint was observed:

```text
https://shop.amul.com/entity/pincode?limit=50&filters[0][field]=pincode&filters[0][value]=411036&filters[0][operator]=regex&filters[0][use_autocomplete]=1&new_search=1&cf_cache=1h
```

A request for `411036` returned a record with `pincode: "411036"` and `substore: "pune-br"` in earlier tests. The site frontend then calls a preference endpoint:

```text
PUT https://shop.amul.com/entity/ms.settings/_/setPreferences
JSON body: {"data":{"store":"pune-br"}}
```

This endpoint returned `200 Updated successfully` in one Python test but `401 Unauthorized` in a later test. The behavior is inconsistent and unresolved. Do not assume that a successful pincode lookup means the store preference was applied.

### 3.2 Product HTML / API is unresolved

A saved HTML capture of the category page was about 169 KB and contained 23 `.product-grid-item` cards (6 marked sold out, 17 marked available at capture time). This is historical evidence only and must not be treated as current inventory.

A direct Python `requests` fetch of the same category URL returned a roughly 4.7 KB HTML shell with no product cards. After one preference request, the category response still contained zero cards. This strongly suggests the live page populates product data through JavaScript or a separate request, or the direct request is receiving a different response. The actual product-data request has **not** been identified and verified.

A HAR file was inspected previously but no definitive inventory XHR/API was identified. The project must not ship a fragile monitor that scrapes only the 4.7 KB shell or silently reports every product as sold out.

### 3.3 Required investigation before production use

Use browser DevTools on the real category page:

1. Open `https://shop.amul.com/en/browse/protein` in a normal browser session.
2. Set pincode `411036` and verify the site shows the correct delivery location.
3. Open DevTools → Network; enable **Preserve log** and filter to **Fetch/XHR**.
4. Reload the page, wait for product cards to render, and identify the request whose response contains product names, product IDs/slugs, stock status, or inventory data.
5. Inspect its request URL, method, query/body, response structure, and whether it depends on cookies, headers, or the selected substore.
6. Prefer a stable, public read-only data endpoint if available. Do not hard-code session cookies, browser tokens, or short-lived anti-bot values into the repository.
7. Reproduce the request from a clean Python session using only legitimate, non-secret headers and the location setup flow. Confirm the returned products and stock match the browser.
8. If the site requires browser-only behavior or blocks automated access, use a Playwright-based page-rendering approach only if allowed by the site's terms and technically reliable. Do not bypass CAPTCHA, access controls, or anti-bot protections.
9. Add a fixture from a sanitized response and parser tests. Never commit personal cookies, session IDs, authorization headers, bot tokens, or HAR files containing credentials.

**Important debugging note:** In Python Requests, `session.headers` displays session defaults; it does not show the actual prepared headers on a specific request. To inspect the outgoing request safely, inspect `response.request.headers` but print only header names or boolean presence flags. Never print the `Cookie` value or any token.

## 4. Suggested repository structure

```text
amul-protein-tracker/
├── .github/
│   └── workflows/
│       └── stock-check.yml
├── config/
│   └── products.yaml
├── src/
│   └── amul_tracker/
│       ├── __init__.py
│       ├── config.py
│       ├── amul_client.py
│       ├── parser.py
│       ├── state.py
│       ├── telegram.py
│       ├── checker.py
│       └── main.py
├── tests/
│   ├── fixtures/
│   │   ├── category_in_stock.html
│   │   └── category_out_of_stock.html
│   ├── test_parser.py
│   ├── test_state.py
│   ├── test_notifications.py
│   └── test_checker.py
├── .gitignore
├── pyproject.toml
├── README.md
├── SPEC.md
└── LICENSE
```

The exact module layout may change, but keep website access, parsing, state management, Telegram notifications, and orchestration separated for testability.

## 5. Configuration and secrets

Use GitHub repository Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Never store secrets in YAML, source code, test fixtures, README, logs, commits, or workflow artifacts. `.env` may be supported for local development but must be ignored by Git. Validate required configuration at startup and fail clearly without echoing secret values.

Non-secret settings should live in YAML or environment variables, including pincode, expected substore, timeout, failure threshold, and product list.

## 6. GitHub Actions workflow

Requirements:

- Schedule with cron every hour (`0 * * * *`). GitHub scheduled workflows can start late; describe this as hourly best-effort, not an exact SLA.
- Also support `workflow_dispatch` for manual runs.
- Set an explicit timezone strategy: cron runs in UTC. The top-of-hour schedule is acceptable; log and format notification timestamps in `Asia/Kolkata`.
- Set minimal permissions, adding `contents: write` only if needed for state persistence.
- Use a supported Python version and pin dependencies reasonably.
- Add workflow concurrency to prevent overlapping runs.
- Apply a job timeout.
- Do not print secrets or full request headers/cookies.
- Make the job exit non-zero on checker failures so GitHub Actions visibly reports a problem, while still sending a Telegram failure warning when the configured threshold is reached.
- Do not allow commits to the state branch to recursively start stock checks.

## 7. Robustness and observability

- Set connection and read timeouts.
- Use a small bounded retry policy for transient network errors and `429`/`5xx`; respect `Retry-After` where practical. Do not retry indefinitely.
- Handle `401`, `403`, `429`, `5xx`, redirects to unexpected pages, Cloudflare/challenge pages, malformed JSON/HTML, and changed markup explicitly.
- Check content type and expected response structure before parsing.
- Validate that the expected location is active or that the inventory response is scoped to `pune-br`. If location cannot be confirmed, fail closed with `UNKNOWN`.
- Log structured, useful diagnostics: check time, HTTP status, response content type/length, product count, parser outcome, and reason for `UNKNOWN`. Do not log cookies or credentials.
- Never infer `OUT_OF_STOCK` from a product missing in the response unless the endpoint's documented semantics prove the response is complete and omission means unavailable.
- If configured products are not found, treat it as a parser/integration error, not as sold out.
- Keep the last known valid stock state if a check fails.

## 8. Testing requirements

Unit tests must cover:

1. Explicit in-stock marker → `IN_STOCK`.
2. Explicit sold-out marker → `OUT_OF_STOCK`.
3. Missing product card → `UNKNOWN` / integration error, not sold out.
4. Conflicting stock markers → `UNKNOWN`.
5. Empty HTML, challenge page, login/pincode modal, and malformed response → failure/`UNKNOWN`.
6. No notification when state remains the same.
7. Notification on `OUT_OF_STOCK` → `IN_STOCK`.
8. Notification on `IN_STOCK` → `OUT_OF_STOCK`.
9. First run with in-stock product sends initial alert.
10. First run with out-of-stock product does not send an alert by default.
11. `UNKNOWN` never overwrites the last known stock state.
12. Failure counter increments and threshold warning sends once when crossed.
13. Successful check resets failure count and sends recovery if appropriate.
14. YAML validation catches duplicate IDs, missing names/URLs, and invalid URLs.
15. State file missing, corrupt, or wrong schema is handled safely; corruption must not silently erase known state.
16. Location mismatch prevents stock-state updates.

Use mocked HTTP and Telegram calls for tests. Tests must not contact the live store or send real Telegram messages.

## 9. README expectations

Explain:

- What the project does and limitations.
- Setup and GitHub repository configuration.
- How to create a Telegram bot and obtain a chat ID (without asking anyone to share it publicly).
- How to add/edit/disable products in `config/products.yaml`.
- How the state branch works and how to reset it.
- How to run locally and run tests.
- How hourly scheduling works and how to manually trigger a run.
- How to troubleshoot location, authentication, blocked requests, parser changes, and Telegram delivery.
- That stock availability can change between hourly checks and is not guaranteed at purchase time.
- That this is an unofficial personal project and is not affiliated with Amul.

## 10. Security and repository hygiene

- Add `.gitignore` for `.env`, virtual environments, caches, local state, logs, editor files, and downloaded HAR/cookie files.
- Never commit the saved HAR file or raw browser session data. HAR files can contain cookies and identifiers.
- If any live session cookies were exposed during debugging, clear the Amul site's cookies/session and do not reuse those values.
- Do not attempt to bypass access controls, CAPTCHAs, or other protections.
- Choose a license only after the repository owner decides which license they want; MIT is a common permissive option but should not be assumed.

## 11. Acceptance criteria / definition of done

The project is ready only when all of the following are true:

- [ ] A clean Python run obtains a real, complete product/inventory response for pincode `411036` / substore `pune-br`.
- [ ] Live stock parsing has been manually cross-checked against the browser for at least one in-stock and one sold-out product, if both are available.
- [ ] Unknown or blocked responses fail closed and cannot produce false stock alerts.
- [ ] All unit tests pass without live network access.
- [ ] A first-run in-stock test sends the expected Telegram message.
- [ ] State persists across separate GitHub Actions runs on the dedicated state branch.
- [ ] Transition alerts are sent exactly once per real transition.
- [ ] Failure warning threshold and recovery behavior work as specified.
- [ ] GitHub Actions runs manually and on the hourly schedule.
- [ ] Secrets are configured only in GitHub Secrets / local ignored environment variables.
- [ ] README documents setup, operation, limitations, and troubleshooting.

## 12. Priority order for implementation

1. **Resolve the live data source first.** Identify the product/inventory response and confirm location scoping. Do not build the production checker on the 4.7 KB HTML shell.
2. Implement a conservative parser with `UNKNOWN` as the safe fallback and fixture tests.
3. Implement YAML configuration and validation.
4. Implement transition logic and Telegram messages with mocked tests.
5. Implement persistent state and failure/recovery tracking.
6. Add GitHub Actions workflow and dedicated state branch handling.
7. Run a manual end-to-end test with secrets configured in GitHub, verify the messages and persisted state, then enable hourly checks.

---

## IDE handoff instruction

Start by inspecting the existing repository and preserving any working code. Compare it against this spec, then implement missing pieces in small, testable changes. **Do not claim the tracker works until the live inventory endpoint is identified and the acceptance criteria pass.** If the data endpoint remains unresolved, produce a clear diagnostic/investigation script and leave the production workflow disabled or clearly marked experimental rather than generating false stock notifications.
