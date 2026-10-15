"""Tests for stock parser covering JSON API records and HTML category cards."""

from pathlib import Path

from amul_tracker.parser import (
    StockResult,
    StockState,
    parse_html_category_page,
    parse_json_product_record,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- HTML Parser Tests ---

def test_html_in_stock_card():
    html_path = FIXTURES_DIR / "category_in_stock.html"
    content = html_path.read_text(encoding="utf-8")
    slug = "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"

    res = parse_html_category_page(content, slug)
    assert res.state == StockState.IN_STOCK
    assert "Add to Cart" in res.reason


def test_html_out_of_stock_card():
    html_path = FIXTURES_DIR / "category_out_of_stock.html"
    content = html_path.read_text(encoding="utf-8")
    slug = "amul-whey-protein-32-g-or-pack-of-30-sachets"

    res = parse_html_category_page(content, slug)
    assert res.state == StockState.OUT_OF_STOCK
    assert "Out of stock indicator detected" in res.reason


def test_html_missing_product_card_returns_unknown():
    html_path = FIXTURES_DIR / "category_in_stock.html"
    content = html_path.read_text(encoding="utf-8")
    slug = "nonexistent-product-slug"

    res = parse_html_category_page(content, slug)
    assert res.state == StockState.UNKNOWN
    assert "not found in HTML" in res.reason
    assert res.state != StockState.OUT_OF_STOCK


def test_html_conflicting_markers_returns_unknown():
    html_path = FIXTURES_DIR / "conflicting_card.html"
    content = html_path.read_text(encoding="utf-8")
    slug = "amul-whey-protein-32-g-or-pack-of-30-sachets"

    res = parse_html_category_page(content, slug)
    assert res.state == StockState.UNKNOWN
    assert "Conflicting stock markers" in res.reason


def test_html_challenge_page_returns_unknown():
    html_path = FIXTURES_DIR / "challenge_page.html"
    content = html_path.read_text(encoding="utf-8")
    slug = "any-product"

    res = parse_html_category_page(content, slug)
    assert res.state == StockState.UNKNOWN
    assert "challenge page detected" in res.reason


def test_html_empty_or_malformed_returns_unknown():
    for bad_content in ["", "   ", "Just random text without tags", "<html><body>No cards here</body></html>"]:
        res = parse_html_category_page(bad_content, "target-slug")
        assert res.state == StockState.UNKNOWN
        assert res.state != StockState.OUT_OF_STOCK


# --- JSON API Parser Tests ---

def test_json_in_stock():
    rec = {
        "name": "Amul High Protein Buttermilk",
        "alias": "amul-high-protein-buttermilk-200-ml-or-pack-of-30",
        "available": 1,
        "inventory_quantity": 477,
    }
    res = parse_json_product_record(rec, expected_slug="amul-high-protein-buttermilk-200-ml-or-pack-of-30")
    assert res.state == StockState.IN_STOCK
    assert res.inventory_quantity == 477


def test_json_out_of_stock_zero_inventory():
    rec = {
        "name": "Amul Whey Protein",
        "alias": "amul-whey-protein-32-g-or-pack-of-30-sachets",
        "available": 0,
        "inventory_quantity": 0,
    }
    res = parse_json_product_record(rec, expected_slug="amul-whey-protein-32-g-or-pack-of-30-sachets")
    assert res.state == StockState.OUT_OF_STOCK


def test_json_out_of_stock_available_zero_nonzero_inventory():
    rec = {
        "name": "Amul Whey Protein Gift Pack",
        "alias": "amul-whey-protein-gift-pack",
        "available": 0,
        "inventory_quantity": 2,
    }
    res = parse_json_product_record(rec)
    assert res.state == StockState.OUT_OF_STOCK


def test_json_contradiction_returns_unknown():
    rec = {
        "name": "Contradictory Item",
        "alias": "contradictory-item",
        "available": 1,
        "inventory_quantity": 0,
    }
    res = parse_json_product_record(rec)
    assert res.state == StockState.UNKNOWN
    assert "Contradictory" in res.reason


def test_json_missing_stock_fields_returns_unknown():
    rec = {
        "name": "Missing Info Item",
        "alias": "missing-info-item",
    }
    res = parse_json_product_record(rec)
    assert res.state == StockState.UNKNOWN
    assert "Neither 'available' nor 'inventory_quantity'" in res.reason


def test_json_slug_mismatch_returns_unknown():
    rec = {
        "name": "Item A",
        "alias": "item-a",
        "available": 1,
        "inventory_quantity": 10,
    }
    res = parse_json_product_record(rec, expected_slug="item-b")
    assert res.state == StockState.UNKNOWN
    assert "slug mismatch" in res.reason


def test_json_empty_or_invalid_record_returns_unknown():
    assert parse_json_product_record({}).state == StockState.UNKNOWN
    assert parse_json_product_record(None).state == StockState.UNKNOWN
