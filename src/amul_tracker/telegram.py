"""Telegram notification service for Amul Protein Tracker.

Handles message formatting, HTML escaping, and Telegram Bot API delivery.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from amul_tracker.config import TelegramConfig
from amul_tracker.parser import StockState

logger = logging.getLogger(__name__)

INDIA_TZ = ZoneInfo("Asia/Kolkata")


class TelegramDeliveryError(Exception):
    """Raised when Telegram API call fails."""


class TelegramNotifier:
    """Delivers formatted stock change and monitoring health alerts to Telegram."""

    def __init__(self, config: TelegramConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def send_message(self, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = False) -> bool:
        """Send a message via Telegram Bot API."""
        if not self.config.is_configured:
            logger.warning("Telegram is not configured (missing token or chat ID). Skipping notification.")
            return False

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }

        try:
            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                logger.error("Telegram API error: HTTP %d - %s", resp.status_code, resp.text)
                raise TelegramDeliveryError(f"Telegram API returned HTTP {resp.status_code}")
            return True
        except requests.RequestException as exc:
            logger.error("Failed to deliver Telegram notification: %s", exc)
            raise TelegramDeliveryError(f"Network error delivering Telegram message: {exc}") from exc

    def notify_stock_transition(
        self,
        product_name: str,
        product_url: str,
        old_state: StockState | None,
        new_state: StockState,
        pincode: str = "411036",
        check_time: datetime | None = None,
        is_first_run: bool = False,
    ) -> bool:
        """Format and send stock change alert."""
        if check_time is None:
            check_time = datetime.now(timezone.utc)
        time_ist = check_time.astimezone(INDIA_TZ).strftime("%d-%b-%Y %I:%M %p IST")

        safe_name = html.escape(product_name)
        safe_url = html.escape(product_url)
        safe_pin = html.escape(pincode)

        if is_first_run and new_state == StockState.IN_STOCK:
            header = "🟢 <b>Amul Protein: Currently In Stock</b>"
            status_desc = "Product is currently available for order!"
        elif old_state == StockState.OUT_OF_STOCK and new_state == StockState.IN_STOCK:
            header = "🚀 <b>Amul Protein: Back In Stock!</b>"
            status_desc = "Stock status transitioned from <b>Sold Out</b> to <b>In Stock</b>!"
        elif old_state == StockState.IN_STOCK and new_state == StockState.OUT_OF_STOCK:
            header = "🔴 <b>Amul Protein: Sold Out</b>"
            status_desc = "Stock status transitioned from <b>In Stock</b> to <b>Sold Out</b>."
        else:
            header = f"ℹ️ <b>Amul Protein: Status Update ({new_state.value})</b>"
            status_desc = f"Current status: <b>{new_state.value}</b>"

        message = (
            f"{header}\n\n"
            f"📦 <b>Product:</b> {safe_name}\n"
            f"📍 <b>Delivery Pincode:</b> {safe_pin}\n"
            f"🕒 <b>Checked At:</b> {time_ist}\n"
            f"📝 <b>Details:</b> {status_desc}\n\n"
            f'🔗 <a href="{safe_url}">View on Amul Store</a>'
        )

        logger.info("Sending stock notification for '%s': %s -> %s", product_name, old_state, new_state)
        return self.send_message(message)

    def notify_failure_warning(
        self,
        consecutive_failures: int,
        threshold: int,
        last_error_message: str,
        pincode: str = "411036",
        check_time: datetime | None = None,
    ) -> bool:
        """Send warning when consecutive checker failures reach threshold."""
        if check_time is None:
            check_time = datetime.now(timezone.utc)
        time_ist = check_time.astimezone(INDIA_TZ).strftime("%d-%b-%Y %I:%M %p IST")

        safe_err = html.escape(last_error_message[:400])
        safe_pin = html.escape(pincode)

        message = (
            f"⚠️ <b>Amul Protein Tracker: Monitoring Alert</b>\n\n"
            f"The stock checker has failed <b>{consecutive_failures}</b> consecutive times "
            f"(threshold: {threshold}).\n\n"
            f"📍 <b>Configured Pincode:</b> {safe_pin}\n"
            f"🕒 <b>Time:</b> {time_ist}\n"
            f"⚠️ <b>Last Error:</b> <code>{safe_err}</code>\n\n"
            f"Please check GitHub Actions logs or website availability."
        )

        logger.warning("Sending failure threshold warning (%d consecutive failures)", consecutive_failures)
        return self.send_message(message)

    def notify_recovery(
        self,
        resolved_after_failures: int,
        pincode: str = "411036",
        check_time: datetime | None = None,
    ) -> bool:
        """Send notification when check succeeds after a failure warning was active."""
        if check_time is None:
            check_time = datetime.now(timezone.utc)
        time_ist = check_time.astimezone(INDIA_TZ).strftime("%d-%b-%Y %I:%M %p IST")
        safe_pin = html.escape(pincode)

        message = (
            f"✅ <b>Amul Protein Tracker: Service Recovered</b>\n\n"
            f"Stock check completed successfully after <b>{resolved_after_failures}</b> earlier failed runs.\n\n"
            f"📍 <b>Delivery Pincode:</b> {safe_pin}\n"
            f"🕒 <b>Time:</b> {time_ist}\n"
            f"Normal monitoring has resumed."
        )

        logger.info("Sending service recovery notification after %d failures", resolved_after_failures)
        return self.send_message(message)
