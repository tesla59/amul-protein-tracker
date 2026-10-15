"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from amul_tracker.config import AppConfig, ConfigError, load_config


def test_load_valid_config(tmp_path: Path):
    yaml_content = """
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
  - id: "disabled-product"
    name: "Disabled Product"
    url: "https://shop.amul.com/product/disabled-product"
    enabled: false
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    config = load_config(config_path=cfg_file)
    assert config.location.pincode == "411036"
    assert config.location.expected_substore == "pune-br"
    assert config.monitor.interval_minutes == 60
    assert config.monitor.failure_warning_threshold == 3
    assert len(config.products) == 2
    assert len(config.get_enabled_products()) == 1
    assert config.products[0].slug == "amul-whey-protein-32-g-or-pack-of-30-sachets"


def test_duplicate_product_id_rejected(tmp_path: Path):
    yaml_content = """
products:
  - id: "dup-id"
    name: "Prod 1"
    url: "https://shop.amul.com/product/prod-1"
  - id: "dup-id"
    name: "Prod 2"
    url: "https://shop.amul.com/product/prod-2"
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ConfigError, match="Duplicate product id"):
        load_config(config_path=cfg_file)


def test_missing_name_rejected(tmp_path: Path):
    yaml_content = """
products:
  - id: "test-id"
    name: ""
    url: "https://shop.amul.com/product/test-id"
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ConfigError, match="missing a valid 'name'"):
        load_config(config_path=cfg_file)


def test_invalid_url_rejected(tmp_path: Path):
    yaml_content = """
products:
  - id: "test-id"
    name: "Valid Name"
    url: "not-a-valid-url"
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ConfigError, match="invalid or missing 'url'"):
        load_config(config_path=cfg_file)


def test_missing_config_file():
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(config_path="nonexistent_config.yaml")


def test_invalid_monitor_threshold(tmp_path: Path):
    yaml_content = """
monitor:
  failure_warning_threshold: 0
products:
  - id: "test-id"
    name: "Name"
    url: "https://shop.amul.com/product/test"
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ConfigError, match="failure_warning_threshold must be at least 1"):
        load_config(config_path=cfg_file)


def test_require_telegram_credentials(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    yaml_content = """
products:
  - id: "test-id"
    name: "Name"
    url: "https://shop.amul.com/product/test"
"""
    cfg_file = tmp_path / "products.yaml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ConfigError, match="Missing required Telegram credentials"):
        load_config(config_path=cfg_file, require_telegram=True)
