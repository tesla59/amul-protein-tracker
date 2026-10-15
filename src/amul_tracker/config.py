"""Configuration loading and validation for Amul Protein Tracker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


@dataclass(frozen=True)
class LocationConfig:
    pincode: str = "411036"
    expected_substore: str = "pune-br"


@dataclass(frozen=True)
class MonitorConfig:
    interval_minutes: int = 60
    failure_warning_threshold: int = 3
    request_timeout_seconds: int = 25
    max_consecutive_retries: int = 2


@dataclass(frozen=True)
class ProductConfig:
    id: str
    name: str
    url: str
    enabled: bool = True

    @property
    def slug(self) -> str:
        """Extract slug from product URL if possible, otherwise fallback to id."""
        path = urlparse(self.url).path.rstrip("/")
        if "/product/" in path:
            return path.split("/product/")[-1]
        return self.id


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str | None = None
    chat_id: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class AppConfig:
    location: LocationConfig = field(default_factory=LocationConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    products: list[ProductConfig] = field(default_factory=list)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    state_file_path: Path = Path("state.json")

    def get_enabled_products(self) -> list[ProductConfig]:
        return [p for p in self.products if p.enabled]


def _validate_url(url: str) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    return bool(parsed.scheme in ("http", "https") and parsed.netloc)


def load_config(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
    require_telegram: bool = False,
) -> AppConfig:
    """Load and validate configuration from YAML file and environment variables."""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    # Default config path
    if config_path is None:
        default_yaml = Path("config/products.yaml")
        if default_yaml.exists():
            config_path = default_yaml
        else:
            config_path = Path("products.yaml")

    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML file {config_path}: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ConfigError(f"Invalid YAML content in {config_path}: expected dictionary at root")

    # Parse location
    loc_data = raw_data.get("location") or {}
    if not isinstance(loc_data, dict):
        raise ConfigError("Config 'location' must be a dictionary")
    pincode = str(loc_data.get("pincode", "411036")).strip()
    if not pincode:
        raise ConfigError("Location 'pincode' cannot be empty")
    expected_substore = str(loc_data.get("expected_substore", "pune-br")).strip()
    location_cfg = LocationConfig(pincode=pincode, expected_substore=expected_substore)

    # Parse monitor
    mon_data = raw_data.get("monitor") or {}
    if not isinstance(mon_data, dict):
        raise ConfigError("Config 'monitor' must be a dictionary")
    try:
        interval_minutes = int(mon_data.get("interval_minutes", 60))
        failure_threshold = int(mon_data.get("failure_warning_threshold", 3))
        request_timeout = int(mon_data.get("request_timeout_seconds", 25))
        max_retries = int(mon_data.get("max_consecutive_retries", 2))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"Monitor settings must be valid integers: {exc}") from exc

    if failure_threshold < 1:
        raise ConfigError("failure_warning_threshold must be at least 1")
    if request_timeout < 1:
        raise ConfigError("request_timeout_seconds must be at least 1")
    if max_retries < 0:
        raise ConfigError("max_consecutive_retries must be non-negative")

    monitor_cfg = MonitorConfig(
        interval_minutes=interval_minutes,
        failure_warning_threshold=failure_threshold,
        request_timeout_seconds=request_timeout,
        max_consecutive_retries=max_retries,
    )

    # Parse products
    raw_products = raw_data.get("products")
    if raw_products is None or not isinstance(raw_products, list):
        raise ConfigError("Config must contain a 'products' list")

    seen_ids: set[str] = set()
    products: list[ProductConfig] = []
    for idx, item in enumerate(raw_products):
        if not isinstance(item, dict):
            raise ConfigError(f"Product item at index {idx} must be a dictionary")

        p_id = item.get("id")
        if not p_id or not isinstance(p_id, str) or not p_id.strip():
            raise ConfigError(f"Product at index {idx} is missing a valid 'id'")
        p_id = p_id.strip()

        if p_id in seen_ids:
            raise ConfigError(f"Duplicate product id found: '{p_id}'")
        seen_ids.add(p_id)

        name = item.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            raise ConfigError(f"Product '{p_id}' is missing a valid 'name'")
        name = name.strip()

        url = item.get("url")
        if not url or not isinstance(url, str) or not _validate_url(url):
            raise ConfigError(f"Product '{p_id}' has an invalid or missing 'url': {url}")
        url = url.strip()

        enabled = bool(item.get("enabled", True))
        products.append(ProductConfig(id=p_id, name=name, url=url, enabled=enabled))

    # Parse Telegram credentials from environment
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
    telegram_cfg = TelegramConfig(bot_token=bot_token, chat_id=chat_id)

    if require_telegram and not telegram_cfg.is_configured:
        missing = []
        if not bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        raise ConfigError(f"Missing required Telegram credentials: {', '.join(missing)}")

    state_path = Path(os.environ.get("STATE_FILE_PATH", "state.json"))

    return AppConfig(
        location=location_cfg,
        monitor=monitor_cfg,
        products=products,
        telegram=telegram_cfg,
        state_file_path=state_path,
    )
