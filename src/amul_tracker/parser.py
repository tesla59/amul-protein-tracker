"""Stock parser for Amul Protein Tracker.

Supports parsing both StoreHippo JSON API product records and
rendered/captured HTML product grid cards.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class StockState(str, Enum):
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StockResult:
    state: StockState
    reason: str
    product_name: str | None = None
    product_slug: str | None = None
    inventory_quantity: int | None = None
    raw_data: dict[str, Any] | None = None


def parse_json_product_record(record: dict[str, Any], expected_slug: str | None = None) -> StockResult:
    """Parse a single StoreHippo ms.products JSON record.
    
    Treats missing, malformed, or contradictory data as UNKNOWN.
    """
    if not isinstance(record, dict) or not record:
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Product record is empty or not a dictionary",
        )

    product_name = record.get("name")
    product_slug = record.get("alias")

    if expected_slug and product_slug and expected_slug != product_slug:
        return StockResult(
            state=StockState.UNKNOWN,
            reason=f"Product slug mismatch: expected {expected_slug}, got {product_slug}",
            product_name=product_name,
            product_slug=product_slug,
        )

    if not product_name or not product_slug:
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Product record missing name or alias",
            product_name=product_name,
            product_slug=product_slug,
        )

    available = record.get("available")
    inventory_qty = record.get("inventory_quantity")

    # Validate availability fields
    if available is None and inventory_qty is None:
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Neither 'available' nor 'inventory_quantity' present in record",
            product_name=product_name,
            product_slug=product_slug,
            raw_data=record,
        )

    # Check for contradictions
    # available = 1 but inventory_quantity is 0 or negative
    if available == 1 and inventory_qty is not None and inventory_qty <= 0:
        return StockResult(
            state=StockState.UNKNOWN,
            reason=f"Contradictory stock signals: available=1 but inventory_quantity={inventory_qty}",
            product_name=product_name,
            product_slug=product_slug,
            inventory_quantity=inventory_qty,
            raw_data=record,
        )

    # Explicit available=0
    if available == 0:
        return StockResult(
            state=StockState.OUT_OF_STOCK,
            reason=f"Explicitly unavailable (available=0, inventory_quantity={inventory_qty})",
            product_name=product_name,
            product_slug=product_slug,
            inventory_quantity=inventory_qty,
            raw_data=record,
        )

    # Explicit available=1 with positive inventory
    if available == 1 and (inventory_qty is None or inventory_qty > 0):
        return StockResult(
            state=StockState.IN_STOCK,
            reason=f"In stock (available=1, inventory_quantity={inventory_qty})",
            product_name=product_name,
            product_slug=product_slug,
            inventory_quantity=inventory_qty,
            raw_data=record,
        )

    # Fallback to inventory quantity if available is not boolean 0/1
    if inventory_qty is not None:
        if inventory_qty > 0:
            return StockResult(
                state=StockState.IN_STOCK,
                reason=f"In stock based on inventory_quantity={inventory_qty}",
                product_name=product_name,
                product_slug=product_slug,
                inventory_quantity=inventory_qty,
                raw_data=record,
            )
        elif inventory_qty <= 0:
            return StockResult(
                state=StockState.OUT_OF_STOCK,
                reason=f"Out of stock based on inventory_quantity={inventory_qty}",
                product_name=product_name,
                product_slug=product_slug,
                inventory_quantity=inventory_qty,
                raw_data=record,
            )

    return StockResult(
        state=StockState.UNKNOWN,
        reason=f"Unrecognized stock status: available={available}, inventory_quantity={inventory_qty}",
        product_name=product_name,
        product_slug=product_slug,
        raw_data=record,
    )


def parse_html_category_page(html_content: str, target_slug: str) -> StockResult:
    """Parse HTML category page for a product card by target slug.
    
    Adheres strictly to SPEC section 2.2:
    - Product cards use .product-grid-item.
    - Product links use /product/<slug>.
    - Product names use .product-grid-name (or equivalent title/name).
    - Out-of-stock cards have class 'outofstock' and .stock-indicator-text containing 'Sold Out'.
    - In-stock cards have an a[title="Add to Cart"] control whose text is 'ADD'.
    - Conflicting, missing, empty, or challenge pages return UNKNOWN.
    """
    if not html_content or not isinstance(html_content, str) or not html_content.strip():
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Empty HTML content",
            product_slug=target_slug,
        )

    # Detect common challenge / blocked / modal pages
    lower_html = html_content.lower()
    challenge_signatures = [
        "cf-chl-bypass",
        "cf-browser-verification",
        "just a moment...",
        "attention required! | cloudflare",
        "challenge-platform",
        "cf-turnstile",
        "robot or human",
        "access denied",
    ]
    if any(sig in lower_html for sig in challenge_signatures):
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Cloudflare or bot challenge page detected",
            product_slug=target_slug,
        )

    if "<html" not in lower_html:
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Malformed HTML response (no <html> tag)",
            product_slug=target_slug,
        )

    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception as exc:
        return StockResult(
            state=StockState.UNKNOWN,
            reason=f"Failed to parse HTML with BeautifulSoup: {exc}",
            product_slug=target_slug,
        )

    # Locate product card
    target_card = None
    all_cards = soup.select(".product-grid-item")

    for card in all_cards:
        # Check links inside card
        links = card.find_all("a", href=True)
        found_target = False
        for a in links:
            href = a["href"]
            if f"/product/{target_slug}" in href or href.endswith(f"/{target_slug}"):
                found_target = True
                break
        if found_target:
            target_card = card
            break

    if target_card is None:
        return StockResult(
            state=StockState.UNKNOWN,
            reason=f"Product card for slug '{target_slug}' not found in HTML (found {len(all_cards)} total cards)",
            product_slug=target_slug,
        )

    # Extract product name
    name_el = target_card.select_one(".product-grid-name") or target_card.select_one("a[title]")
    product_name = name_el.get_text(strip=True) if name_el else target_slug

    card_classes = target_card.get("class", [])
    has_outofstock_class = "outofstock" in card_classes

    indicator_el = target_card.select_one(".stock-indicator-text")
    indicator_text = indicator_el.get_text(strip=True) if indicator_el else ""
    has_sold_out_text = bool(re.search(r"sold\s*out", indicator_text, re.IGNORECASE))

    is_explicit_oos = has_outofstock_class or has_sold_out_text

    add_btn = target_card.select_one('a[title="Add to Cart"]')
    if not add_btn:
        add_btn = target_card.find(lambda el: el.name in ("a", "button") and "add to cart" in (el.get("title", "")).lower())

    has_add_cart = False
    if add_btn:
        btn_text = add_btn.get_text(strip=True)
        if "add" in btn_text.lower():
            has_add_cart = True

    # Check conflict
    if is_explicit_oos and has_add_cart:
        return StockResult(
            state=StockState.UNKNOWN,
            reason="Conflicting stock markers: both sold-out indicators and Add to Cart button present",
            product_name=product_name,
            product_slug=target_slug,
        )

    if is_explicit_oos:
        return StockResult(
            state=StockState.OUT_OF_STOCK,
            reason=f"Out of stock indicator detected (classes: {card_classes}, text: '{indicator_text}')",
            product_name=product_name,
            product_slug=target_slug,
        )

    if has_add_cart:
        return StockResult(
            state=StockState.IN_STOCK,
            reason="In stock (found active 'Add to Cart' button with ADD text)",
            product_name=product_name,
            product_slug=target_slug,
        )

    return StockResult(
        state=StockState.UNKNOWN,
        reason="No recognizable in-stock or out-of-stock marker on product card",
        product_name=product_name,
        product_slug=target_slug,
    )
